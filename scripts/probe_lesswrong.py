"""Is LessWrong's GraphQL answerable from the current egress?

Used by the CI Mullvad step to decide whether a relay's exit IP is worth
keeping. LessWrong's Vercel edge blocks by IP reputation: from 2026-08-05 the
shared exit of us-atl-wg-001 started drawing 429s on the first, cookie-less
request, and the browser warm-up fallback could not clear the challenge either
— four days of 0 LessWrong posts, all published as "success". The same request
from an unblocked IP answers in under a second, so a cheap direct probe is a
reliable relay-quality signal.

This intentionally mirrors the gatherer's *direct* request path (same UA, same
requests fingerprint): if the probe passes, the pipeline's first try succeeds
and the browser fallback is never needed.

Exit codes: 0 = reachable, 1 = blocked (429/403/challenge), 2 = network error.

Usage: python3 scripts/probe_lesswrong.py [socks5h://host:port]
"""

import json
import sys

import requests

GRAPHQL_URL = "https://www.lesswrong.com/graphql"

# Keep identical to DEFAULT_USER_AGENT in scripts/lesswrong_cookie_fetch.py —
# tests/lesswrong_probe_test.py pins them equal. A probe that presents a
# different fingerprint than the gatherer answers a different question.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

QUERY = '{ posts(input: { terms: { view: "new", limit: 1 } }) { results { _id } } }'


def classify(status_code: int, text: str) -> str:
    """ok | blocked | invalid, from a GraphQL response."""
    if status_code == 200:
        try:
            payload = json.loads(text)
        except ValueError:
            # The Vercel challenge interstitial can be served with a 200.
            return "blocked"
        if isinstance(payload, dict) and payload.get("data") and not payload.get("errors"):
            return "ok"
        return "invalid"
    if status_code in (403, 429):
        return "blocked"
    return "invalid"


def main() -> int:
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        response = requests.post(
            GRAPHQL_URL,
            json={"query": QUERY},
            headers={"User-Agent": USER_AGENT},
            proxies=proxies,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - any transport failure means "rotate"
        print(f"probe: network error: {exc}", file=sys.stderr)
        return 2

    verdict = classify(response.status_code, response.text)
    print(f"probe: HTTP {response.status_code} -> {verdict}")
    return {"ok": 0, "blocked": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
