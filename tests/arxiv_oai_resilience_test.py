"""Guards for the arXiv OAI-PMH harvester's failure behaviour.

Written after the 2026-08-17 outage, in which the Monday catch-up harvest was
the *only* arXiv transport that day, both archives hit a 60s read timeout on
the first and only attempt, `_harvest_archive` swallowed the exception and
returned `[]`, and the run published `research: success` with 12 items against
a Monday median of 334.

Three separate defects made that possible, and each has a test here:
  1. no retry            -> one stall was fatal
  2. 60s timeout         -> a *successful* call measured 51.5s that morning
  3. empty == failed     -> zero papers from a dead socket looked like a quiet day

Runs in the dependency-light guard job: `requests` is stubbed, so the whole
transport is under test control and nothing touches the network.

  python3 -m unittest tests.arxiv_oai_resilience_test -v
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Stub `requests` before loading the harvester. Real exception classes, so the
# module's `except requests.exceptions.RequestException` clauses behave.
# --------------------------------------------------------------------------
class _RequestException(Exception):
    pass


class _HTTPError(_RequestException):
    pass


class _ReadTimeout(_RequestException):
    pass


_installed_requests_stub = False
if "requests" not in sys.modules:
    _exceptions = types.ModuleType("requests.exceptions")
    _exceptions.RequestException = _RequestException
    _exceptions.HTTPError = _HTTPError
    _exceptions.ReadTimeout = _ReadTimeout

    _requests = types.ModuleType("requests")
    _requests.exceptions = _exceptions
    _requests.get = None  # every test installs its own

    sys.modules["requests"] = _requests
    sys.modules["requests.exceptions"] = _exceptions
    _installed_requests_stub = True

requests = sys.modules["requests"]

_spec = importlib.util.spec_from_file_location(
    "_arxiv_oai", REPO_ROOT / "agents" / "gatherers" / "arxiv_oai.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

ArxivOAIHarvester = _mod.ArxivOAIHarvester

# Drop our stubs from sys.modules now that the harvester has loaded: it bound
# the module object at import time, and every test below reaches it through
# the `requests` alias above. Leaving them installed poisoned a full local
# discovery run -- modules loading after us alphabetically (e.g.
# staleness_checker_ssrf_test) bound the stub and died in setUp with
# "module 'requests' has no attribute 'Session'" (14 errors, found 2026-08-21).
# CI is unaffected either way: each module runs in its own process.
if _installed_requests_stub:
    del sys.modules["requests"]
    del sys.modules["requests.exceptions"]

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
RAW_NS = "http://arxiv.org/OAI/arXivRaw/"


def _page(ids, datestamp="2026-08-17", resumption_token=None):
    """Build one valid ListRecords page containing v1 records for `ids`."""
    records = "".join(
        f"""
        <record>
          <header><identifier>oai:arXiv.org:{i}</identifier>
            <datestamp>{datestamp}</datestamp></header>
          <metadata>
            <arXivRaw xmlns="{RAW_NS}">
              <id>{i}</id>
              <title>Paper {i}</title>
              <authors>A. Author</authors>
              <abstract>An abstract.</abstract>
              <categories>cs.AI cs.LG</categories>
              <version version="v1"><date>Mon, 17 Aug 2026 00:00:00 GMT</date></version>
            </arXivRaw>
          </metadata>
        </record>"""
        for i in ids
    )
    token = (
        f"<resumptionToken>{resumption_token}</resumptionToken>"
        if resumption_token
        else "<resumptionToken/>"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="{OAI_NS}">
  <ListRecords>{records}{token}</ListRecords>
</OAI-PMH>""".encode()


class _Resp:
    def __init__(self, content=b"", status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HTTPError(f"HTTP {self.status_code}")


class _Transport:
    """Scripted responses; each entry is a _Resp or an exception to raise."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.timeouts = []

    def __call__(self, url, timeout=None, **kwargs):
        self.calls.append(url)
        self.timeouts.append(timeout)
        item = self.responses.pop(0) if self.responses else _Resp(_page([]))
        if isinstance(item, Exception):
            raise item
        return item


class _Clock:
    """Monotonic clock that only advances when the code sleeps."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def _harvester(transport, clock=None, **kw):
    clock = clock or _Clock()
    requests.get = transport
    kw.setdefault("backoff_seconds", 5.0)
    kw.setdefault("timeout", 180.0)
    kw.setdefault("deadline_seconds", 600.0)
    return ArxivOAIHarvester(
        ["cs.AI", "cs.LG"], sleep=clock.sleep, now=clock.now, **kw
    ), clock


class RetryBehaviour(unittest.TestCase):
    def test_transient_timeout_is_retried_and_recovers(self):
        """The 08-17 failure was a single stall. One retry would have saved it."""
        transport = _Transport(_ReadTimeout("read timed out"), _Resp(_page(["2608.001"])))
        harvester, clock = _harvester(transport, max_attempts=3)

        papers = harvester.harvest_date("2026-08-17")

        self.assertEqual(1, len(papers))
        self.assertEqual(2, len(transport.calls), "should have retried once")
        self.assertTrue(harvester.last_harvest_complete)
        self.assertEqual([], harvester.incomplete_archives)
        self.assertEqual([5.0], clock.slept, "first backoff is the base delay")

    def test_backoff_grows_exponentially(self):
        transport = _Transport(
            _ReadTimeout("1"), _ReadTimeout("2"), _Resp(_page(["2608.002"]))
        )
        harvester, clock = _harvester(transport, max_attempts=3)

        harvester.harvest_date("2026-08-17")

        self.assertEqual([5.0, 10.0], clock.slept)

    def test_exhausted_retries_mark_the_archive_incomplete(self):
        """Zero papers from a dead socket must not look like a quiet day."""
        transport = _Transport(*[_ReadTimeout("stalled")] * 9)
        harvester, _ = _harvester(transport, max_attempts=3)

        papers = harvester.harvest_date("2026-08-17")

        self.assertEqual([], papers)
        self.assertFalse(harvester.last_harvest_complete)
        # Both archives derived from cs.AI/cs.LG collapse to 'cs'
        self.assertEqual(["cs"], harvester.incomplete_archives)

    def test_harvest_date_never_raises_on_transport_failure(self):
        """A dead endpoint degrades the day; it must not kill the pipeline."""
        transport = _Transport(*[_ReadTimeout("stalled")] * 9)
        harvester, _ = _harvester(transport, max_attempts=3)

        try:
            harvester.harvest_date("2026-08-17")
        except Exception as exc:  # noqa: BLE001 - that's the point of the test
            self.fail(f"harvest_date raised {type(exc).__name__}: {exc}")

    def test_empty_day_is_complete_not_incomplete(self):
        """A genuinely empty range is a fact, not a fault."""
        transport = _Transport(_Resp(_page([])))
        harvester, _ = _harvester(transport)

        papers = harvester.harvest_date("2026-08-15", "2026-08-16")

        self.assertEqual([], papers)
        self.assertTrue(harvester.last_harvest_complete)


class RateLimitBehaviour(unittest.TestCase):
    def test_503_is_retried_honouring_retry_after(self):
        transport = _Transport(
            _Resp(b"", status_code=503, headers={"Retry-After": "12"}),
            _Resp(_page(["2608.003"])),
        )
        harvester, clock = _harvester(transport, max_attempts=3)

        papers = harvester.harvest_date("2026-08-17")

        self.assertEqual(1, len(papers))
        self.assertEqual([12.0], clock.slept, "must honour Retry-After, not our backoff")
        self.assertTrue(harvester.last_harvest_complete)

    def test_unparseable_retry_after_falls_back_to_our_backoff(self):
        transport = _Transport(
            _Resp(b"", status_code=503, headers={"Retry-After": "Wed, 19 Aug 2026 07:00:00 GMT"}),
            _Resp(_page(["2608.004"])),
        )
        harvester, clock = _harvester(transport, max_attempts=3)

        harvester.harvest_date("2026-08-17")

        self.assertEqual([5.0], clock.slept)

    def test_404_is_not_retried(self):
        """Client errors are our bug, not the network's; retrying just wastes time."""
        transport = _Transport(_Resp(b"", status_code=404))
        harvester, _ = _harvester(transport, max_attempts=3)

        harvester.harvest_date("2026-08-17")

        self.assertEqual(1, len(transport.calls))
        self.assertFalse(harvester.last_harvest_complete)


class PaginationBehaviour(unittest.TestCase):
    def test_pages_already_fetched_survive_a_later_failure(self):
        """Partial beats nothing -- but it must still be flagged as partial."""
        transport = _Transport(
            _Resp(_page(["2608.010", "2608.011"], resumption_token="tok1")),
            *[_ReadTimeout("died on page 2")] * 3,
        )
        harvester, _ = _harvester(transport, max_attempts=3)

        papers = harvester.harvest_date("2026-08-17")

        self.assertEqual(2, len(papers), "page 1 results must be kept")
        self.assertFalse(harvester.last_harvest_complete)

    def test_full_pagination_completes(self):
        transport = _Transport(
            _Resp(_page(["2608.020"], resumption_token="tok1")),
            _Resp(_page(["2608.021"])),
        )
        harvester, _ = _harvester(transport)

        papers = harvester.harvest_date("2026-08-17")

        self.assertEqual(2, len(papers))
        self.assertTrue(harvester.last_harvest_complete)


class DeadlineBehaviour(unittest.TestCase):
    def test_deadline_stops_the_retry_loop(self):
        """timeout x attempts x pages x archives must not run unbounded."""
        transport = _Transport(*[_ReadTimeout("stalled")] * 50)
        harvester, clock = _harvester(
            transport, max_attempts=10, backoff_seconds=30.0, deadline_seconds=60.0
        )

        harvester.harvest_date("2026-08-17")

        self.assertLessEqual(sum(clock.slept), 60.0)
        self.assertFalse(harvester.last_harvest_complete)

    def test_request_timeout_is_clamped_to_remaining_budget(self):
        transport = _Transport(_Resp(_page([])))
        harvester, _ = _harvester(transport, timeout=180.0, deadline_seconds=30.0)

        harvester.harvest_date("2026-08-17")

        self.assertEqual([30.0], transport.timeouts)


class ConfiguredDefaults(unittest.TestCase):
    def test_default_timeout_clears_the_measured_slow_call(self):
        """60s was the old value. A *successful* call took 51.5s on 2026-08-17."""
        self.assertGreaterEqual(
            _mod.DEFAULT_TIMEOUT, 120.0,
            "default OAI timeout must leave headroom above the observed 51.5s success",
        )

    def test_retries_are_on_by_default(self):
        self.assertGreaterEqual(_mod.DEFAULT_MAX_ATTEMPTS, 2)

    def test_deadline_is_bounded(self):
        self.assertGreater(_mod.DEFAULT_DEADLINE, 0)
        self.assertLessEqual(
            _mod.DEFAULT_DEADLINE, 1800.0,
            "an unbounded harvest can eat the whole job budget",
        )


class MondayUsesRss(unittest.TestCase):
    """Source-level guard on the fix that actually prevents the outage.

    A behavioural test would need the full gatherer (feedparser, the agents
    package, network fakes for seven RSS categories). The regression is a single
    boolean, so it is pinned directly: `use_rss` must not be gated on the
    collection mode. Restoring `and mode != 'catchup'` puts Monday back on an
    OAI-only path with no fallback.
    """

    def _assign_source(self, name):
        src = (REPO_ROOT / "agents" / "gatherers" / "research_gatherer.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return ast.get_source_segment(src, node.value)
        self.fail(f"no assignment to `{name}` found in research_gatherer.py")

    def test_use_rss_is_not_gated_on_catchup_mode(self):
        expr = self._assign_source("use_rss")
        self.assertNotIn(
            "mode", expr,
            "use_rss must not depend on the collection mode: gating it on "
            "`mode != 'catchup'` made OAI-PMH the sole Monday transport, which "
            "is exactly how 2026-08-17 published zero arXiv papers",
        )
        self.assertIn("_is_current_collection", expr)

    def test_weekend_tail_is_best_effort(self):
        src = (REPO_ROOT / "agents" / "gatherers" / "research_gatherer.py").read_text()
        self.assertIn(
            "best_effort=True", src,
            "the Sat-Sun sweep must not be able to discard the papers RSS returned",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
