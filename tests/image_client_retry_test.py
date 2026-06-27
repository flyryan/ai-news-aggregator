"""Regression tests for OpenAICompatibleClient transient-failure retry behavior.

Context / why this exists
-------------------------
- 2026-06-26: a single "Server disconnected without sending a response"
  (httpx.RemoteProtocolError) dropped the entire daily hero with no retry.
  Fix d0f05dd added retry-with-backoff (it claimed "verified with 7 mocked
  scenarios" but no test was ever committed -- this file fixes that gap).
- 2026-06-27: the hero failed AGAIN. The 3-attempt/~25s window was too shallow:
  the RDSec proxy fast-failed (~3s each) on all 3 tries inside ~25s, yet a
  manual regen ~3.5h later succeeded first try. The provider blip simply
  outlasted the tiny retry window. Fix: widen to 7 attempts with capped
  exponential backoff so the retry window spans ~2.5-3 minutes.

These tests lock in:
  1. The retry window is multi-minute (catches a future accidental shrink).
  2. A disconnect-x3-then-success rides through (the exact 2026-06-27 scenario).
  3. Backoff is capped per-step at retry_max_delay.
  4. Non-retryable 4xx (auth/validation) still fails fast on the first attempt.
  5. A sustained outage exhausts all attempts then raises.
  6. 5xx / 429 are retried then succeed.

Stdlib-only (unittest + unittest.mock), matching the repo's other tests so it
runs in CI without pytest or any extra deps:

  python3 -m unittest tests.image_client_retry_test -v
"""

import asyncio
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# Make the repo root importable when run directly or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generators.image_client import (  # noqa: E402
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BASE_DELAY,
    DEFAULT_RETRY_MAX_DELAY,
    OpenAICompatibleClient,
)


def _make_client(**kwargs) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key="test-key",
        endpoint="https://proxy.example/v1",
        model="gemini-3-pro-image",
        **kwargs,
    )


def _ok_response() -> MagicMock:
    """A successful chat/completions response carrying a base64 image data URL."""
    png = base64.b64encode(b"fake-png-bytes").decode()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "images": [
                            {"image_url": {"url": f"data:image/png;base64,{png}"}}
                        ]
                    }
                }
            ]
        }
    )
    return resp


def _client_ctx(post_side_effect):
    """Mock httpx.AsyncClient whose .post applies post_side_effect (list/callable)."""
    ctx = MagicMock()
    instance = MagicMock()
    instance.post = AsyncMock(side_effect=post_side_effect)
    ctx.__aenter__ = AsyncMock(return_value=instance)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, instance


class RetryWindowConstantsTest(unittest.TestCase):
    def test_retry_window_is_multi_minute(self):
        """The 2026-06-27 regression: a ~25s window let a transient blip drop the
        hero. Lock in a window that spans at least ~2 minutes of backoff."""
        self.assertGreaterEqual(
            DEFAULT_MAX_ATTEMPTS, 6, "too few attempts; a blip will outlast the window"
        )
        self.assertGreaterEqual(
            DEFAULT_RETRY_MAX_DELAY, 30.0, "backoff cap too small for a real outage"
        )

        c = _make_client()
        total = 0.0
        for attempt in range(1, c.max_attempts):  # attempts 1..N-1 each sleep
            base = min(c.retry_base_delay * (2 ** (attempt - 1)), c.retry_max_delay)
            total += base
        self.assertGreaterEqual(
            total, 120.0, f"retry window only spans ~{total:.0f}s; want >=120s"
        )

    def test_backoff_is_capped(self):
        c = _make_client(retry_base_delay=3.0, retry_max_delay=60.0)
        # Without a cap, attempt 8 would be 3 * 2^7 = 384s.
        for attempt in range(1, c.max_attempts + 3):
            d = c._backoff_delay(attempt)
            self.assertLessEqual(
                d,
                c.retry_max_delay + c.retry_base_delay / 2 + 1e-9,
                f"attempt {attempt} backoff {d} exceeded cap",
            )


class RetryBehaviorTest(unittest.TestCase):
    def test_disconnect_then_success_rides_through(self):
        """Three instant disconnects (like 2026-06-27) followed by success must
        return a valid image rather than dropping the hero."""
        disconnect = httpx.RemoteProtocolError(
            "Server disconnected without sending a response."
        )
        side_effects = [disconnect, disconnect, disconnect, _ok_response()]
        ctx, _ = _client_ctx(side_effects)

        async def run():
            c = _make_client()
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()) as sleep:
                result = await c.generate("a prompt")
            return result, sleep

        result, sleep = asyncio.run(run())
        self.assertEqual(result.image_data, b"fake-png-bytes")
        # 3 failures => 3 backoff sleeps before the 4th (successful) attempt.
        self.assertEqual(sleep.await_count, 3)

    def test_non_retryable_4xx_fails_fast(self):
        """A 401 auth error must NOT be retried -- fail on attempt 1."""
        req = httpx.Request("POST", "https://proxy.example/v1/chat/completions")
        resp = httpx.Response(401, request=req, text="unauthorized")
        err = httpx.HTTPStatusError("401", request=req, response=resp)

        failing = MagicMock()
        failing.raise_for_status = MagicMock(side_effect=err)
        ctx, instance = _client_ctx([failing])

        async def run():
            c = _make_client()
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()) as sleep:
                with self.assertRaises(RuntimeError):
                    await c.generate("a prompt")
            return instance, sleep

        instance, sleep = asyncio.run(run())
        self.assertEqual(instance.post.await_count, 1, "4xx should not be retried")
        self.assertEqual(sleep.await_count, 0)

    def test_sustained_outage_exhausts_then_raises(self):
        disconnect = httpx.RemoteProtocolError("Server disconnected.")
        ctx, instance = _client_ctx([disconnect] * DEFAULT_MAX_ATTEMPTS)

        async def run():
            c = _make_client()
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()) as sleep:
                with self.assertRaises(RuntimeError):
                    await c.generate("a prompt")
            return instance, sleep

        instance, sleep = asyncio.run(run())
        self.assertEqual(instance.post.await_count, DEFAULT_MAX_ATTEMPTS)
        # One sleep between each failed attempt, none after the final failure.
        self.assertEqual(sleep.await_count, DEFAULT_MAX_ATTEMPTS - 1)

    def test_5xx_retried_then_success(self):
        req = httpx.Request("POST", "https://proxy.example/v1/chat/completions")
        resp503 = httpx.Response(503, request=req, text="unavailable")
        err503 = httpx.HTTPStatusError("503", request=req, response=resp503)
        failing = MagicMock()
        failing.raise_for_status = MagicMock(side_effect=err503)

        side_effects = [failing, _ok_response()]
        ctx, instance = _client_ctx(side_effects)

        async def run():
            c = _make_client()
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()) as sleep:
                result = await c.generate("a prompt")
            return result, instance, sleep

        result, instance, sleep = asyncio.run(run())
        self.assertEqual(result.image_data, b"fake-png-bytes")
        self.assertEqual(instance.post.await_count, 2)
        self.assertEqual(sleep.await_count, 1)


if __name__ == "__main__":
    unittest.main()
