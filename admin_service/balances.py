"""Live vendor credit balances, and the burn-down they imply.

Both probes are free and documented as not consuming credit
(agents/gatherers/reddit_gatherer.py:419-434,
agents/gatherers/social_gatherer.py:184-189), so the panel can read them on
demand rather than ingesting artifacts.

A single reading cannot produce a trend, so each probe is appended to the
store. Days-to-zero appears once there are two readings far enough apart to
mean something.

This exists because ScrapeCreators credits are finite and their exhaustion is
silent: when they run out Reddit collection stops, and the published
collection_status would still read success.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from .store import AdminStore

__all__ = ["fetch_balances"]

logger = logging.getLogger("admin_service.balances")

SCRAPECREATORS_URL = "https://api.scrapecreators.com/v1/account/credit-balance"
TWITTERAPI_URL = "https://api.twitterapi.io/oapi/my/info"

# Below this many days of runway, the source is close enough to dying that it
# needs attention now rather than at the next review.
URGENT_DAYS = 30


def _burn_rate(history: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Credits per day and days remaining, from the newest and oldest readings.

    Returns (None, None) rather than guessing when the data cannot support a
    forecast: one reading, too short a span, or a balance that went up (a
    top-up). A confident number built on noise is worse than no number.
    """
    if len(history) < 2:
        return None, None

    newest, oldest = history[0], history[-1]
    try:
        t_new = datetime.fromisoformat(newest["ts"])
        t_old = datetime.fromisoformat(oldest["ts"])
    except (KeyError, ValueError):
        return None, None

    elapsed_days = (t_new - t_old).total_seconds() / 86400
    if elapsed_days < 0.5:
        return None, None

    consumed = oldest["balance"] - newest["balance"]
    if consumed <= 0:
        return None, None

    per_day = consumed / elapsed_days
    if per_day <= 0:
        return None, None
    return round(per_day, 1), round(newest["balance"] / per_day, 1)


def _probe_scrapecreators(key: str) -> tuple[int | None, str]:
    if not key:
        return None, "SCRAPECREATORS_API_KEY not configured"
    try:
        response = httpx.get(SCRAPECREATORS_URL, headers={"x-api-key": key}, timeout=15)
        if response.status_code != 200:
            return None, f"probe returned HTTP {response.status_code}"
        payload = response.json()
        value = payload.get("creditCount", payload.get("credits_remaining"))
        return (int(value), "") if value is not None else (None, "no balance in response")
    except Exception as exc:  # noqa: BLE001 - a probe failure must not break the page
        return None, type(exc).__name__


def _probe_twitter(key: str) -> tuple[int | None, str]:
    if not key:
        return None, "TWITTERAPI_IO_KEY not configured"
    try:
        response = httpx.get(TWITTERAPI_URL, headers={"x-api-key": key}, timeout=15)
        if response.status_code != 200:
            return None, f"probe returned HTTP {response.status_code}"
        payload = response.json()
        data = payload.get("data") or payload
        value = data.get("recharge_credits")
        return (int(value), "") if value is not None else (None, "no balance in response")
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


def fetch_balances(
    store: AdminStore,
    *,
    scrapecreators_key: str = "",
    twitter_key: str = "",
) -> list[dict[str, Any]]:
    probes = (
        ("scrapecreators", "ScrapeCreators (Reddit)", "credits",
         _probe_scrapecreators(scrapecreators_key)),
        ("twitterapi", "TwitterAPI.io (Twitter)", "credits",
         _probe_twitter(twitter_key)),
    )

    results: list[dict[str, Any]] = []
    for vendor, label, unit, (balance, error) in probes:
        usd = None
        if balance is not None:
            # TwitterAPI bills $1 per 100,000 recharge credits.
            usd = round(balance / 100000, 2) if vendor == "twitterapi" else None
            store.record_balance(vendor, balance, usd)

        history = store.balance_history(vendor, limit=90)
        per_day, days_left = _burn_rate(history)

        results.append({
            "vendor": vendor,
            "label": label,
            "unit": unit,
            "balance": balance,
            "balance_usd": usd,
            "error": error,
            "history": list(reversed(history)),  # oldest first, for charting
            "burn_per_day": per_day,
            "days_remaining": days_left,
            "urgent": days_left is not None and days_left < URGENT_DAYS,
        })

    return results
