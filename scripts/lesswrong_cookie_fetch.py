#!/usr/bin/env python3
"""Fetch and cache LessWrong cookies by letting Playwright execute the site JS first.

The key trick: a raw requests POST to /graphql now gets the Vercel Security
Checkpoint (HTTP 429), but the same POST from a real browser context succeeds.
This module warms a headless Playwright browser on lesswrong.com, validates a
GraphQL query from inside the page context, then exports cookies for reuse by
requests-based callers.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

LESSWRONG_HOME = "https://www.lesswrong.com"
LESSWRONG_GRAPHQL = "https://www.lesswrong.com/graphql"
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "lesswrong_cookies.json"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
LOGGER = logging.getLogger(__name__)

TEST_QUERY = """
query GetPosts($after: Date, $before: Date) {
  posts(input: { terms: { view: "new", after: $after, before: $before, limit: 100 } }) {
    results {
      _id
      title
      slug
      postedAt
      contents { html }
      user { displayName username }
      baseScore
      voteCount
    }
  }
}
""".strip()

TEST_VARIABLES = {"after": "2026-03-27", "before": "2026-03-28"}
BROWSER_FETCH_FN = "(args) => fetch(\"https://www.lesswrong.com/graphql\", {method: \"POST\", headers: {\"content-type\": \"application/json\"}, credentials: \"include\", body: JSON.stringify({query: args.query, variables: args.variables})}).then(async r => ({status: r.status, text: await r.text()}))"


class LessWrongCookieError(RuntimeError):
    """Raised when cookies could not be obtained or validated."""


@dataclass
class CookieCache:
    created_at: float
    user_agent: str
    cookies: List[Dict[str, Any]]


class LessWrongClient:
    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE_PATH,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        headless: bool = True,
    ) -> None:
        self.cache_path = Path(cache_path).expanduser()
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.headless = headless

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        variables = variables or {}

        direct_response = self._try_graphql_request(query, variables, [], self.user_agent)
        if direct_response is not None and self._response_looks_valid(direct_response):
            return direct_response.json()

        cached = self.load_cached_cookies()
        if cached:
            response = self._try_graphql_request(query, variables, cached.cookies, cached.user_agent)
            if self._response_looks_valid(response):
                return response.json()

        fresh = self.solve_and_cache_cookies()
        response = self._try_graphql_request(query, variables, fresh.cookies, fresh.user_agent)
        if self._response_looks_valid(response):
            return response.json()

        browser_result = self._graphql_request_via_new_browser(query, variables)
        if self._browser_result_looks_valid(browser_result):
            return json.loads(browser_result["text"])

        status_code = response.status_code if response is not None else "unknown"
        response_text = response.text[:500] if response is not None else "no response"
        raise LessWrongCookieError(
            f"GraphQL request still failed after browser solve: HTTP {status_code} {response_text}"
        )

    def fetch_posts(self, after: str, before: str) -> List[Dict[str, Any]]:
        payload = self.graphql(TEST_QUERY, {"after": after, "before": before})
        return payload["data"]["posts"]["results"]

    def load_cached_cookies(self) -> Optional[CookieCache]:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text())
            cookies = data.get("cookies") or []
            if not cookies:
                return None
            return CookieCache(
                created_at=float(data.get("created_at", 0)),
                user_agent=data.get("user_agent", self.user_agent),
                cookies=cookies,
            )
        except Exception:
            return None

    def solve_and_cache_cookies(self) -> CookieCache:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache = self._solve_challenge_with_browser()
        self.cache_path.write_text(json.dumps(asdict(cache), indent=2))
        return cache

    def _solve_challenge_with_browser(self) -> CookieCache:
        deadline = time.time() + self.timeout_seconds

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                proxy=self._playwright_proxy_config(),
            )
            context = browser.new_context(user_agent=self.user_agent)
            page = context.new_page()
            page.set_default_timeout(10_000)
            page.goto(LESSWRONG_HOME, wait_until="domcontentloaded")

            while time.time() < deadline:
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass

                browser_result = self._graphql_request_via_browser(page, TEST_QUERY, TEST_VARIABLES)
                if self._browser_result_looks_valid(browser_result):
                    cookies = context.cookies()
                    browser.close()
                    return CookieCache(
                        created_at=time.time(),
                        user_agent=self.user_agent,
                        cookies=cookies,
                    )

                time.sleep(2)

            browser.close()

        raise LessWrongCookieError(
            f"Timed out after {self.timeout_seconds}s waiting for LessWrong/Vercel challenge to clear"
        )

    def _graphql_request_via_browser(self, page, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        return page.evaluate(BROWSER_FETCH_FN, {"query": query, "variables": variables})

    def _graphql_request_via_new_browser(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                proxy=self._playwright_proxy_config(),
            )
            context = browser.new_context(user_agent=self.user_agent)
            page = context.new_page()
            page.goto(LESSWRONG_HOME, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightTimeoutError:
                pass
            result = self._graphql_request_via_browser(page, query, variables)
            browser.close()
            return result

    def _graphql_request(
        self,
        query: str,
        variables: Dict[str, Any],
        cookies: List[Dict[str, Any]],
        user_agent: str,
    ) -> requests.Response:
        session = requests.Session()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain") or ".lesswrong.com"
            if name and value is not None:
                session.cookies.set(name, value, domain=domain)

        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": LESSWRONG_HOME,
            "Referer": f"{LESSWRONG_HOME}/",
        }

        return session.post(
            LESSWRONG_GRAPHQL,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=45,
        )

    def _try_graphql_request(
        self,
        query: str,
        variables: Dict[str, Any],
        cookies: List[Dict[str, Any]],
        user_agent: str,
    ) -> Optional[requests.Response]:
        try:
            return self._graphql_request(query, variables, cookies, user_agent)
        except requests.exceptions.RequestException as exc:
            LOGGER.debug("LessWrong direct GraphQL request failed: %s", exc)
            return None

    @staticmethod
    def _proxy_url_from_env() -> Optional[str]:
        for name in (
            "LESSWRONG_PROXY_URL",
            "PIPELINE_PROXY_URL",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return None

    @classmethod
    def _playwright_proxy_config(cls) -> Optional[Dict[str, str]]:
        proxy_url = cls._proxy_url_from_env()
        if not proxy_url:
            return None

        parsed = urlsplit(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            return {"server": proxy_url}

        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"

        proxy_config = {"server": urlunsplit((parsed.scheme, host, "", "", ""))}
        if parsed.username:
            proxy_config["username"] = unquote(parsed.username)
        if parsed.password:
            proxy_config["password"] = unquote(parsed.password)
        return proxy_config

    @staticmethod
    def _response_looks_valid(response: Optional[requests.Response]) -> bool:
        if response is None:
            return False
        if response.status_code != 200:
            return False
        try:
            payload = response.json()
        except Exception:
            return False
        return isinstance(payload, dict) and "data" in payload and not payload.get("errors")

    @staticmethod
    def _browser_result_looks_valid(result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict) or result.get("status") != 200:
            return False
        try:
            payload = json.loads(result.get("text", ""))
        except Exception:
            return False
        return isinstance(payload, dict) and "data" in payload and not payload.get("errors")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch/cache LessWrong cookies and test GraphQL access")
    parser.add_argument("--after", default=TEST_VARIABLES["after"], help="Start date YYYY-MM-DD")
    parser.add_argument("--before", default=TEST_VARIABLES["before"], help="End date YYYY-MM-DD")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH), help="Cookie cache path")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible window")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON payload")
    args = parser.parse_args()

    client = LessWrongClient(cache_path=Path(args.cache_path), headless=not args.headed)
    payload = client.graphql(TEST_QUERY, {"after": args.after, "before": args.before})

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        posts = payload["data"]["posts"]["results"]
        print(f"Fetched {len(posts)} posts from LessWrong")
        for post in posts[:10]:
            author = (post.get("user") or {}).get("displayName") or (post.get("user") or {}).get("username") or "unknown"
            print(f"- {post.get('title', '<untitled>')} ({author})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
