"""SSRF regression tests for StalenessChecker outbound fetches.

Context / why this exists
-------------------------
Finding #1248 (CWE-918, HIGH): StalenessChecker fetched fully attacker-controlled
URLs (RSS <link>, second-order <a href>, and redirect targets) via a vanilla
requests.Session with allow_redirects=True and zero SSRF controls, at:
  - agents/staleness_checker.py  _find_primary_source_url   (self._session.get(item_url))
  - agents/staleness_checker.py  _extract_primary_published_date (self._session.get(primary_url))
An attacker-supplied URL (or a 302 hop from an allowlisted domain) let the server
issue GET requests to loopback / RFC1918 / link-local cloud-metadata
(169.254.169.254) targets.

These tests lock in the fix: outbound fetches go through _safe_get(), which
rejects non-http(s) schemes, resolves the host and blocks private/loopback/
link-local/reserved IPs, and re-validates every redirect hop.

Run with the project deps installed (requests, beautifulsoup4, PyYAML,
python-dateutil, httpx, anthropic):

  python3 -m unittest tests.staleness_checker_ssrf_test -v
"""

import io
import ipaddress
import logging
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

# Make the repo root importable when run directly or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.staleness_checker import StalenessChecker, SSRFBlockedError  # noqa: E402


# Hostnames the tests "resolve"; literal IPs resolve to themselves.
FAKE_DNS = {
    "news.example.com": "93.184.216.34",        # public -> allowed
    "redirector.example.com": "93.184.216.34",  # public first hop -> allowed
}


def fake_getaddrinfo(host, port=None, *args, **kwargs):
    """Deterministic stand-in for socket.getaddrinfo (no real DNS/network)."""
    try:
        ipaddress.ip_address(host)  # literal IP resolves to itself
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port or 0))]
    except ValueError:
        pass
    ip = FAKE_DNS.get(host)
    if ip is None:
        raise socket.gaierror(f"no fake DNS entry for {host!r}")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]


def make_response(status_code=200, headers=None, body=b"", url="http://news.example.com/"):
    resp = requests.Response()
    resp.status_code = status_code
    if headers:
        resp.headers.update(headers)
    resp._content = body
    # Mirror a real non-streamed response: body already drained and a raw
    # object present, so Response.close() (called by _safe_get on redirect
    # hops) behaves exactly as it does in production.
    resp._content_consumed = True
    resp.raw = io.BytesIO(body)
    resp.url = url
    return resp


def make_checker():
    # config_dir missing -> _load_releases logs a warning and returns {} (fine
    # here). Silence that expected warning so it doesn't leak into test/CI
    # output via logging's lastResort handler.
    logging.getLogger("agents.staleness_checker").setLevel(logging.ERROR)
    return StalenessChecker(config_dir="/nonexistent", target_date="2026-07-03")


class RecordingSession:
    """Session stub that records every get() and returns queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._responses:
            return self._responses.pop(0)
        return make_response(url=url)


class SafeGetBlockingTest(unittest.TestCase):
    def setUp(self):
        self.checker = make_checker()
        self.session = RecordingSession([])
        self.checker._session = self.session

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_cloud_metadata_ip(self, _dns):
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(self.session.calls, [], "must not issue request to metadata IP")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_loopback(self, _dns):
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://127.0.0.1:8080/admin")
        self.assertEqual(self.session.calls, [])

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_private_rfc1918(self, _dns):
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://10.1.2.3/")
        self.assertEqual(self.session.calls, [])

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_ipv4_mapped_ipv6_metadata_ip(self, _dns):
        # ::ffff:169.254.169.254 is the IPv4-mapped IPv6 spelling of the cloud
        # metadata IP. _ip_is_blocked() must unwrap the mapping and apply the
        # IPv4 rules, so this literal cannot bypass the link-local block.
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://[::ffff:169.254.169.254]/latest/meta-data/")
        self.assertEqual(self.session.calls, [], "mapped-IPv6 metadata IP must be blocked")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_ipv6_loopback(self, _dns):
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://[::1]:8080/admin")
        self.assertEqual(self.session.calls, [])

    def test_blocks_non_http_scheme(self):
        for url in ("file:///etc/passwd", "gopher://127.0.0.1:70/", "ftp://10.0.0.1/"):
            with self.assertRaises(SSRFBlockedError):
                self.checker._safe_get(url)
        self.assertEqual(self.session.calls, [])

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_redirect_into_internal_host(self, _dns):
        # First hop is a public, allowlisted-style host that 302s to the metadata IP.
        self.session = RecordingSession([
            make_response(302, {"Location": "http://169.254.169.254/latest/meta-data/"}),
        ])
        self.checker._session = self.session
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://redirector.example.com/go")
        # Exactly one outbound request (the first hop); the internal hop is blocked.
        self.assertEqual(len(self.session.calls), 1)
        self.assertEqual(self.session.calls[0][0], "http://redirector.example.com/go")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_redirect_cap_enforced_exactly(self, _dns):
        # max_redirects=2 -> 1 original GET + at most 2 redirect follows = 3
        # outbound requests total. When the 3rd response is still a redirect,
        # SSRFBlockedError is raised WITHOUT issuing a 4th request.
        self.session = RecordingSession([
            make_response(302, {"Location": "http://news.example.com/a"}),
            make_response(302, {"Location": "http://news.example.com/b"}),
            make_response(302, {"Location": "http://news.example.com/c"}),
            make_response(302, {"Location": "http://news.example.com/d"}),
        ])
        self.checker._session = self.session
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://news.example.com/start", max_redirects=2)
        self.assertEqual(len(self.session.calls), 3,
                         "cap is 1 original + max_redirects follows, no extra request")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_allows_public_host(self, _dns):
        self.session = RecordingSession([
            make_response(200, {"Content-Type": "text/html"}, b"<html>ok</html>"),
        ])
        self.checker._session = self.session
        resp = self.checker._safe_get("http://news.example.com/article")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ok", resp.text)
        self.assertEqual(len(self.session.calls), 1)
        # redirects must be disabled on the underlying session call
        self.assertFalse(self.session.calls[0][1].get("allow_redirects", True))

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_blocks_cgnat_shared_address(self, _dns):
        # 100.64.0.0/10 (carrier-grade NAT / shared address space) is not
        # flagged is_private on older Python, so the is_global check is what
        # blocks it. No request must be issued.
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://100.64.0.1/")
        self.assertEqual(self.session.calls, [])

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_caps_oversized_response_body(self, _dns):
        # A body larger than the cap is refused (treated upstream as a normal
        # non-fatal fetch failure) rather than buffered into memory.
        from agents.staleness_checker import MAX_RESPONSE_BYTES
        big = b"x" * (MAX_RESPONSE_BYTES + 1)
        self.session = RecordingSession([
            make_response(200, {"Content-Type": "text/html"}, big),
        ])
        self.checker._session = self.session
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://news.example.com/huge")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_rejects_declared_oversized_content_length(self, _dns):
        from agents.staleness_checker import MAX_RESPONSE_BYTES
        self.session = RecordingSession([
            make_response(
                200,
                {"Content-Type": "text/html", "Content-Length": str(MAX_RESPONSE_BYTES + 1)},
                b"<html>small actual body</html>",
            ),
        ])
        self.checker._session = self.session
        with self.assertRaises(SSRFBlockedError):
            self.checker._safe_get("http://news.example.com/liar")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_body_under_cap_is_returned_intact(self, _dns):
        self.session = RecordingSession([
            make_response(200, {"Content-Type": "text/html"}, b"<html>fresh</html>"),
        ])
        self.checker._session = self.session
        resp = self.checker._safe_get("http://news.example.com/ok")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("fresh", resp.text)


class SinkDoesNotReachInternalTest(unittest.TestCase):
    """End-to-end guard on the two real sinks: no request escapes to an internal host.

    On the vulnerable code (direct self._session.get) these fail because the
    internal URL is fetched; with _safe_get they pass (request is blocked first).
    """

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_extract_primary_date_does_not_fetch_metadata_ip(self, _dns):
        checker = make_checker()
        session = RecordingSession([])
        checker._session = session
        result = checker._extract_primary_published_date("http://169.254.169.254/latest/meta-data/")
        self.assertIsNone(result)
        self.assertEqual(session.calls, [], "second-order sink must not hit the metadata IP")

    @patch("agents.staleness_checker.socket.getaddrinfo", side_effect=fake_getaddrinfo)
    def test_find_primary_source_does_not_fetch_private_ip(self, _dns):
        checker = make_checker()
        session = RecordingSession([])
        checker._session = session

        class _Raw:
            def __init__(self, url):
                self.url = url

        class _Item:
            def __init__(self, url):
                self.item = _Raw(url)

        result = checker._find_primary_source_url(_Item("http://10.0.0.5/internal"))
        self.assertIsNone(result)
        self.assertEqual(session.calls, [], "first-order sink must not hit the private IP")


if __name__ == "__main__":
    unittest.main()
