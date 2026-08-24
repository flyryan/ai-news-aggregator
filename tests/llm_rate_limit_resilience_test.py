"""Transport resiliency and the publish gate, after the 2026-08-24 429 outage.

What happened
-------------
OpenRouter's shared free-tier pool for ``stealth/ox-alpha`` browned out. 40 of
57 LLM calls returned 429 in ~0.4s each; 50 of them inside a single minute.
Every analyzer's reduce pass and every link enrichment failed. All four category
summaries were replaced by the 47-char placeholder, the executive summary lost
its internal links -- and the run reported ``[ok]`` on all 11 phases, exited 0,
and the workflow committed the gutted report over a complete one.

Three defects made that possible, one per section below:

  1. NO TRANSPORT RETRY on the openai-chat path. ``LLM_MAX_RETRIES`` is handed
     to ``anthropic.AsyncAnthropic``, which is not in the call path for
     ``mode: openai-chat`` (that path drives raw httpx). The only 429-aware
     retry lived in ``AsyncLLMRouter``, which is not even constructed for a
     single route. A 429 was therefore a hard, immediate failure.

  2. NO RATE LIMIT, only a concurrency cap. Those bound the same thing only
     while requests are slow: when each rejection returns in 0.4s it frees its
     slot instantly, so 16 slots produced bursts far above the provider's
     published 20/min -- the faster we were limited, the harder we hammered.

  3. NO PUBLISH GATE ON CATEGORY SUMMARIES. ``validate_report.py`` existed and
     ran in CI, but only checked the executive summary, topics and item counts,
     all of which survived. The placeholder summaries sailed through.

Stdlib-only unittest:

  python3 -m unittest tests.llm_rate_limit_resilience_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402

from agents.llm_client import (  # noqa: E402
    _RateLimiter,
    _llm_backoff_delay,
    _retry_after_seconds,
    _transient_retry_reason,
    AsyncAnthropicClient,
    AsyncLLMRouter,
)
from agents.base import CategoryReport  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_validate_report():
    """Load scripts/validate_report.py by path (scripts/ is not a package)."""
    path = REPO_ROOT / "scripts" / "validate_report.py"
    spec = importlib.util.spec_from_file_location("validate_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_error(status: int, headers=None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError(f"returned {status}", request=request, response=response)


class RetryClassificationTest(unittest.TestCase):
    """What counts as transient. Retrying a prompt error just burns rate budget."""

    def test_429_is_retryable(self):
        self.assertEqual(_transient_retry_reason(_http_error(429)), "http_429")

    def test_5xx_is_retryable(self):
        self.assertEqual(_transient_retry_reason(_http_error(503)), "http_503")

    def test_client_errors_are_not_retryable(self):
        for status in (400, 401, 403, 404, 422):
            self.assertIsNone(
                _transient_retry_reason(_http_error(status)),
                f"HTTP {status} must not be retried",
            )

    def test_transport_anomalies_are_retryable(self):
        # A dying connection surfaces as a bare AssertionError from httpcore/h11.
        self.assertEqual(_transient_retry_reason(AssertionError()), "AssertionError")
        self.assertEqual(
            _transient_retry_reason(httpx.ConnectError("boom")), "ConnectError"
        )

    def test_router_and_transport_agree(self):
        # One definition, two consumers: a divergence here means the router
        # could fail over on something the transport refuses to retry.
        for error in (_http_error(429), _http_error(500), AssertionError()):
            self.assertEqual(
                AsyncLLMRouter._retry_reason(error), _transient_retry_reason(error)
            )


class BackoffTest(unittest.TestCase):
    def test_backoff_grows_and_is_capped(self):
        delays = [_llm_backoff_delay(i, 5.0, 90.0) for i in range(1, 8)]
        # Exponential up to the cap, jitter never exceeding base/2.
        self.assertGreaterEqual(delays[0], 5.0)
        self.assertLess(delays[0], 7.5)
        self.assertGreaterEqual(delays[3], 40.0)
        for delay in delays:
            self.assertLessEqual(delay, 90.0 + 2.5)

    def test_jitter_desynchronises_callers(self):
        # Analyzer batches fail together against a shared pool; identical
        # backoff would resynchronise them into the same herd on every round.
        samples = {round(_llm_backoff_delay(2, 5.0, 90.0), 4) for _ in range(50)}
        self.assertGreater(len(samples), 1, "backoff must carry jitter")

    def test_retry_after_header_is_honoured_and_clamped(self):
        self.assertEqual(
            _retry_after_seconds(_http_error(429, {"retry-after": "12"}), 90.0), 12.0
        )
        self.assertEqual(
            _retry_after_seconds(_http_error(429, {"retry-after": "99999"}), 90.0), 90.0
        )

    def test_retry_after_absent_or_junk_falls_back(self):
        self.assertIsNone(_retry_after_seconds(_http_error(429), 90.0))
        self.assertIsNone(
            _retry_after_seconds(_http_error(429, {"retry-after": "Wed, 21 Oct"}), 90.0)
        )


class RateLimiterTest(unittest.TestCase):
    """The governor that a concurrency cap could not provide."""

    def test_disabled_limiter_is_a_no_op(self):
        async def run():
            limiter = _RateLimiter(0)
            self.assertFalse(limiter.enabled)
            started = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - started

        self.assertLess(asyncio.run(run()), 0.05)

    def test_requests_are_paced(self):
        async def run():
            limiter = _RateLimiter(120)  # 2/s
            started = time.monotonic()
            for _ in range(4):
                await limiter.acquire()
            return time.monotonic() - started

        # Burst of 1, then 3 more at 0.5s apart.
        self.assertGreaterEqual(asyncio.run(run()), 1.4)

    def test_concurrent_fanout_is_paced_not_simultaneous(self):
        # This is the actual 2026-08-24 shape: many analyzer batches launching
        # at once. A full-bucket token bucket would let them all straight
        # through, which is why burst capacity defaults to 1.
        async def run():
            limiter = _RateLimiter(120)
            started = time.monotonic()
            await asyncio.gather(*[limiter.acquire() for _ in range(5)])
            return time.monotonic() - started

        self.assertGreaterEqual(asyncio.run(run()), 1.9)


class _FakeClient(AsyncAnthropicClient):
    """AsyncAnthropicClient with the network replaced, to drive the retry loop."""

    def __init__(self, outcomes, **kwargs):
        super().__init__(
            api_key="test-key",
            base_url="https://openrouter.ai/api",
            model="stealth/ox-alpha",
            mode="openai-chat",
            max_concurrent_requests=0,
            **kwargs,
        )
        self.outcomes = list(outcomes)
        self.calls = 0

    async def _create_message(self, request_context=None, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        # BaseException, not Exception: asyncio.CancelledError has not been an
        # Exception subclass since 3.8, and checking the narrower type here
        # silently *returned* a CancelledError instead of raising it.
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TransportRetryTest(unittest.TestCase):
    """The retry that did not exist on the openai-chat path."""

    def _client(self, outcomes, attempts=4):
        # Zero delays keep the test fast; the schedule itself is covered above.
        return _FakeClient(outcomes, retry_max_attempts=attempts)

    def test_429_then_success_recovers(self):
        client = self._client([_http_error(429), _http_error(429), "ok"])
        client.retry_base_delay = 0.0
        client.retry_max_delay = 0.0
        result = asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(result, "ok")
        self.assertEqual(client.calls, 3)

    def test_exhausted_attempts_raise_the_last_error(self):
        client = self._client([_http_error(429)] * 4, attempts=4)
        client.retry_base_delay = 0.0
        client.retry_max_delay = 0.0
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 4)

    def test_non_retryable_error_fails_immediately(self):
        client = self._client([_http_error(400), "ok"])
        client.retry_base_delay = 0.0
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 1, "a 400 must not consume retry budget")

    def test_cancellation_is_never_retried(self):
        client = self._client([asyncio.CancelledError(), "ok"])
        client.retry_base_delay = 0.0
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 1)

    def test_single_route_config_still_gets_transport_retry(self):
        # The regression in one line: with one route AsyncLLMRouter is never
        # constructed, so the client itself must carry the retry.
        client = _FakeClient(["ok"])
        self.assertGreater(
            client.retry_max_attempts, 1,
            "a single-route client must retry transient failures itself",
        )

    def test_routed_mode_disables_per_client_retry(self):
        # In routed mode, failing over to a sibling route must happen promptly;
        # leaving per-client retry on would burn a multi-minute backoff window
        # before the router ever tried route B.
        client = _FakeClient(["ok"], retry_max_attempts=1)
        self.assertEqual(client.retry_max_attempts, 1)


class LivenessAwareRetryTest(unittest.TestCase):
    """A 429 while the provider is demonstrably serving must not cost budget.

    2026-08-24 12:27: `social_analyzer.batch_3` hit attempt 5/6 while
    `reddit_analyzer.batch_4` was 60s into a healthy stream on the same
    provider, 5,575 thinking chars in. ox-alpha is a popular free model on a
    shared pool, so 429 is the normal background condition -- counting every one
    against a fixed budget fails healthy calls for bad timing.
    """

    def _client(self, outcomes, **kw):
        c = _FakeClient(outcomes, retry_max_attempts=3, **kw)
        c.retry_base_delay = 0.0
        c.retry_max_delay = 0.0
        c.retry_contended_delay = 0.5
        return c

    def test_contended_429s_do_not_consume_budget(self):
        # Ten 429s -- far past the budget of 3 -- but the provider keeps proving
        # it is alive, so the call must survive and eventually succeed.
        client = self._client([_http_error(429)] * 10 + ["ok"])

        async def scenario():
            async def keep_alive():
                # Stand in for a concurrent caller streaming tokens.
                for _ in range(60):
                    client._mark_provider_alive()
                    await asyncio.sleep(0.05)
            task = asyncio.create_task(keep_alive())
            try:
                return await client._create_message_with_retries(
                    request_context={"caller": "social_analyzer.batch_3"}
                )
            finally:
                task.cancel()

        self.assertEqual(asyncio.run(scenario()), "ok")
        self.assertEqual(client.calls, 11)

    def test_silent_provider_still_gives_up_on_budget(self):
        # Nothing ever proves the provider is alive, so the budget applies and
        # the call fails after exactly retry_max_attempts tries.
        client = self._client([_http_error(429)] * 10)
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 3)

    def test_stale_liveness_counts_as_silent(self):
        # Proof of life from before the window must not excuse a failure.
        client = self._client([_http_error(429)] * 10)
        client.retry_liveness_window = 1.0
        client._provider_alive_at = time.monotonic() - 600  # long expired
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 3)

    def test_wall_clock_deadline_bounds_contended_retries(self):
        # Forgiven retries must still be bounded, or a permanently contended
        # call would spin forever.
        client = self._client([_http_error(429)] * 500)
        client.retry_max_elapsed = 0.6
        client.retry_contended_delay = 0.1

        async def scenario():
            client._mark_provider_alive()
            async def keep_alive():
                for _ in range(200):
                    client._mark_provider_alive()
                    await asyncio.sleep(0.05)
            task = asyncio.create_task(keep_alive())
            try:
                await client._create_message_with_retries(request_context={"caller": "t"})
            finally:
                task.cancel()

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(scenario())
        self.assertGreater(client.calls, 3, "must outlast the plain attempt budget")
        self.assertLess(client.calls, 400, "deadline must stop it")

    def test_success_marks_provider_alive(self):
        client = self._client(["ok"])
        asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertTrue(client._provider_recently_alive())

    def test_non_retryable_still_fails_fast_even_when_contended(self):
        client = self._client([_http_error(400), "ok"])
        client._mark_provider_alive()
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client._create_message_with_retries(request_context={"caller": "t"}))
        self.assertEqual(client.calls, 1)

    def test_liveness_is_shared_across_concurrent_callers(self):
        # The whole point: one client instance serves every analyzer, so a
        # success on ANY caller is proof for a struggling one.
        client = self._client(["ok"])
        self.assertFalse(client._provider_recently_alive())
        client._mark_provider_alive()
        self.assertTrue(client._provider_recently_alive())


class PublishGateTest(unittest.TestCase):
    """The check that would have caught 2026-08-24 before it published."""

    def setUp(self):
        self.validator = _load_validate_report()

    def _report(self, **overrides):
        report = {
            "date": "2026-08-24",
            "executive_summary": "A" * 3000,
            "top_topics": [{"name": "t"}],
            "total_items_analyzed": 1173,
            "total_items_collected": 1176,
            "categories": {
                name: {"count": 100, "category_summary": "S" * 1500}
                for name in ("news", "research", "social", "reddit")
            },
        }
        report.update(overrides)
        return report

    def test_healthy_report_passes(self):
        result = self.validator.validate(self._report(), "2026-08-24")
        self.assertTrue(result["valid"], result["failures"])

    def test_placeholder_category_summary_blocks_publish(self):
        categories = {
            name: {
                "count": 100,
                "category_summary": self.validator.PLACEHOLDER_CATEGORY_SUMMARY,
            }
            for name in ("news", "research", "social", "reddit")
        }
        result = self.validator.validate(self._report(categories=categories), "2026-08-24")
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["failures"]), 4)
        self.assertIn("placeholder", result["failures"][0])

    def test_empty_category_summary_blocks_publish(self):
        categories = {"news": {"count": 12, "category_summary": ""}}
        result = self.validator.validate(self._report(categories=categories), "2026-08-24")
        self.assertFalse(result["valid"])

    def test_empty_category_with_no_items_is_not_a_failure(self):
        # A quiet news day is not a broken run.
        categories = {"news": {"count": 0, "category_summary": ""}}
        result = self.validator.validate(self._report(categories=categories), "2026-08-24")
        self.assertTrue(result["valid"], result["failures"])

    def test_degradations_warn_but_do_not_block(self):
        # A thinner report is honest; a missing summary is not.
        result = self.validator.validate(
            self._report(degradations=["research: 2/6 analysis batches failed after retry"]),
            "2026-08-24",
        )
        self.assertTrue(result["valid"], result["failures"])
        self.assertTrue(any("degraded:" in w for w in result["warnings"]))

    def test_placeholder_constant_is_pinned_to_the_analyzer(self):
        # The validator hardcodes the string the reduce phase falls back to.
        # If base.py's fallback text changes and this does not, the gate goes
        # blind to exactly the failure it was written for.
        base_source = (REPO_ROOT / "agents" / "base.py").read_text(encoding="utf-8")
        self.assertIn(
            f'"{self.validator.PLACEHOLDER_CATEGORY_SUMMARY}"',
            base_source,
            "validate_report.PLACEHOLDER_CATEGORY_SUMMARY must match agents/base.py",
        )


class DegradationPlumbingTest(unittest.TestCase):
    """Degradation has to travel with the data, not only reach a log line."""

    def test_category_report_carries_and_serialises_degradations(self):
        report = CategoryReport(
            category="research",
            top_items=[],
            all_items=[],
            category_summary="x",
            themes=[],
            cross_signals=[],
            total_collected=0,
            degradations=["reduce_rank failed (HTTPStatusError)"],
        )
        self.assertTrue(report.degraded)
        self.assertEqual(report.to_dict()["degradations"], ["reduce_rank failed (HTTPStatusError)"])

    def test_clean_report_is_not_degraded(self):
        report = CategoryReport(
            category="news",
            top_items=[],
            all_items=[],
            category_summary="x",
            themes=[],
            cross_signals=[],
            total_collected=0,
        )
        self.assertFalse(report.degraded)
        self.assertEqual(report.to_dict()["degradations"], [])

    def test_degradations_survive_a_round_trip(self):
        # Checkpoint resume rehydrates reports from disk; degradation must not
        # be laundered away by a --resume.
        original = CategoryReport(
            category="social",
            top_items=[],
            all_items=[],
            category_summary="x",
            themes=[],
            cross_signals=[],
            total_collected=0,
            degradations=["3/4 analysis batches failed after retry"],
        )
        restored = CategoryReport.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.degradations, original.degradations)
        self.assertTrue(restored.degraded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
