"""Tests for the replay capture layer.

The behaviours locked in here are the capture-layer contract in
``docs/replay-schema.md``: coalescing, monotonic timestamps, bounded buffers,
never breaking the pipeline, the kill switch, and never storing signature
deltas.
"""

import os
import unittest
from unittest import mock

from agents.replay_recorder import (
    DELTA_TEXT,
    DELTA_THINKING,
    ReplayRecorder,
    get_recorder,
    reset_recorder,
)


class FakeClock:
    """Manually advanced clock so timing assertions are exact, not flaky.

    Offsets accumulate as integer milliseconds and are converted to seconds
    once; accumulating in float seconds makes `int(elapsed * 1000)` land on
    199 instead of 200.
    """

    def __init__(self, start: float = 1_000_000.0):
        self.start = start
        self.offset_ms = 0

    def __call__(self) -> float:
        return self.start + self.offset_ms / 1000.0

    def advance_ms(self, ms: int) -> None:
        self.offset_ms += ms


class FakeUsage:
    input_tokens = 120
    output_tokens = 45
    cache_read_input_tokens = 7
    cache_creation_input_tokens = 3


class FakeResponse:
    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


def _new_recorder(clock=None, **env) -> ReplayRecorder:
    """Build a recorder with an explicit env, since flags are read in __init__."""
    with mock.patch.dict(os.environ, env, clear=False):
        return ReplayRecorder(clock=clock or FakeClock())


def _only_call(recorder: ReplayRecorder) -> dict:
    return recorder.snapshot()["calls"][0]


class CoalescingTests(unittest.TestCase):
    def test_same_kind_within_window_merges(self):
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="80")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})

        recorder.record_delta(call_id, DELTA_TEXT, "Hello")
        clock.advance_ms(30)
        recorder.record_delta(call_id, DELTA_TEXT, " world")

        deltas = _only_call(recorder)["deltas"]
        self.assertEqual(deltas["text"], ["Hello world"])
        self.assertEqual(deltas["kind"], [DELTA_TEXT])
        self.assertEqual(len(deltas["t"]), 1)

    def test_same_kind_outside_window_splits(self):
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="80")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})

        recorder.record_delta(call_id, DELTA_TEXT, "Hello")
        clock.advance_ms(200)
        recorder.record_delta(call_id, DELTA_TEXT, " world")

        deltas = _only_call(recorder)["deltas"]
        self.assertEqual(deltas["text"], ["Hello", " world"])
        self.assertEqual(deltas["t"], [0, 200])

    def test_different_kinds_never_merge(self):
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="80")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "orchestrator.summary"})

        recorder.record_delta(call_id, DELTA_THINKING, "let me think")
        clock.advance_ms(5)
        recorder.record_delta(call_id, DELTA_TEXT, "## Answer")

        deltas = _only_call(recorder)["deltas"]
        self.assertEqual(deltas["kind"], [DELTA_THINKING, DELTA_TEXT])
        self.assertEqual(deltas["text"], ["let me think", "## Answer"])

    def test_window_is_anchored_to_entry_start_not_last_append(self):
        # A continuous stream must still split, or the typewriter degenerates
        # into one giant blob.
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="80")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})

        for _ in range(10):
            recorder.record_delta(call_id, DELTA_TEXT, "x")
            clock.advance_ms(30)

        deltas = _only_call(recorder)["deltas"]
        self.assertGreater(len(deltas["t"]), 1)
        self.assertEqual("".join(deltas["text"]), "x" * 10)

    def test_char_counts_track_every_delta(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "research_analyzer.batch_2"})

        recorder.record_delta(call_id, DELTA_THINKING, "abc")
        recorder.record_delta(call_id, DELTA_TEXT, "defg")

        call = _only_call(recorder)
        self.assertEqual(call["thinking_chars"], 3)
        self.assertEqual(call["text_chars"], 4)
        self.assertEqual(call["delta_events"], 2)


class MonotonicTests(unittest.TestCase):
    def test_clock_regression_is_clamped(self):
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="0")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "orchestrator.topics"})

        clock.advance_ms(500)
        recorder.record_delta(call_id, DELTA_TEXT, "a")
        clock.advance_ms(-400)  # NTP step backwards
        recorder.record_delta(call_id, DELTA_TEXT, "b")
        clock.advance_ms(50)
        recorder.record_delta(call_id, DELTA_TEXT, "c")

        times = _only_call(recorder)["deltas"]["t"]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[0], 500)
        self.assertEqual(times[1], 500)

    def test_wait_ms_measured_between_queue_and_start(self):
        clock = FakeClock()
        recorder = _new_recorder(clock)
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})
        clock.advance_ms(180)
        recorder.mark_started(call_id)

        call = _only_call(recorder)
        self.assertEqual(call["queued_ms"], 0)
        self.assertEqual(call["start_ms"], 180)
        self.assertEqual(call["wait_ms"], 180)


class CapTests(unittest.TestCase):
    def test_per_call_cap_sets_truncated_and_stops_growing(self):
        clock = FakeClock()
        recorder = _new_recorder(clock, LLM_REPLAY_COALESCE_MS="0", LLM_REPLAY_MAX_DELTAS="5")
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})

        for i in range(20):
            recorder.record_delta(call_id, DELTA_TEXT, f"{i}")
            clock.advance_ms(10)

        call = _only_call(recorder)
        self.assertEqual(len(call["deltas"]["t"]), 5)
        self.assertTrue(call["truncated"])
        self.assertEqual(call["dropped_deltas"], 15)
        # Counters stay truthful about what the model actually produced.
        self.assertEqual(call["delta_events"], 20)
        self.assertTrue(recorder.snapshot()["truncated"])

    def test_global_cap_stops_growth_across_calls(self):
        clock = FakeClock()
        recorder = _new_recorder(
            clock, LLM_REPLAY_COALESCE_MS="0", LLM_REPLAY_MAX_TOTAL_DELTAS="3"
        )
        recorder.begin_run("2026-07-28")
        first = recorder.start_call(1, {"caller": "a.batch_1"})
        second = recorder.start_call(2, {"caller": "b.batch_1"})

        for _ in range(5):
            recorder.record_delta(first, DELTA_TEXT, "x")
            clock.advance_ms(10)
        for _ in range(5):
            recorder.record_delta(second, DELTA_TEXT, "y")
            clock.advance_ms(10)

        snapshot = recorder.snapshot()
        stored = sum(len(c["deltas"]["t"]) for c in snapshot["calls"])
        self.assertEqual(stored, 3)
        self.assertTrue(snapshot["truncated"])


class FailSafeTests(unittest.TestCase):
    def test_internal_exception_disables_recorder_without_propagating(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")

        # Force an internal failure on the hot path.
        recorder._calls = None  # type: ignore[assignment]

        with self.assertLogs("agents.replay_recorder", level="WARNING") as logs:
            self.assertIsNone(recorder.record_delta("c001", DELTA_TEXT, "boom"))

        self.assertEqual(len(logs.output), 1)
        self.assertIsNone(recorder.start_call(2, {"caller": "later"}))
        self.assertIsNone(recorder.mark_started("c001"))
        self.assertIsNone(recorder.finish_call("c001", response=FakeResponse()))
        snapshot = recorder.snapshot()
        self.assertFalse(snapshot["enabled"])
        self.assertIsNotNone(snapshot["disabled_reason"])

    def test_unknown_call_id_is_ignored(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")

        recorder.record_delta("c999", DELTA_TEXT, "orphan")
        recorder.finish_call("c999", response=FakeResponse())
        recorder.mark_started(None)

        self.assertEqual(recorder.snapshot()["calls"], [])
        self.assertTrue(recorder.snapshot()["enabled"])

    def test_snapshot_still_returns_data_after_disable(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "orchestrator.summary"})
        recorder.record_delta(call_id, DELTA_TEXT, "partial")
        recorder._disable(RuntimeError("simulated"))

        snapshot = recorder.snapshot()
        self.assertEqual(len(snapshot["calls"]), 1)
        self.assertEqual(snapshot["calls"][0]["deltas"]["text"], ["partial"])


class KillSwitchTests(unittest.TestCase):
    def test_capture_disabled_makes_everything_a_noop(self):
        recorder = _new_recorder(LLM_REPLAY_CAPTURE="false")

        self.assertIsNone(recorder.begin_run("2026-07-28"))
        self.assertIsNone(recorder.start_call(1, {"caller": "news_analyzer.batch_1"}))
        self.assertIsNone(recorder.record_delta("c001", DELTA_TEXT, "hi"))
        self.assertIsNone(recorder.finish_call("c001", response=FakeResponse()))

        snapshot = recorder.snapshot()
        self.assertFalse(snapshot["capture_enabled"])
        self.assertEqual(snapshot["calls"], [])
        # An off recorder is not a broken one.
        self.assertIsNone(snapshot["disabled_reason"])

    def test_capture_enabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_REPLAY_CAPTURE", None)
            self.assertTrue(ReplayRecorder().capture_enabled)


class PrivacyTests(unittest.TestCase):
    def test_only_output_deltas_are_stored(self):
        # The recorder has no API for prompt content at all -- the strongest
        # form of "no prompts, ever". Guard that the surface stays that way.
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "news_analyzer.batch_1"})
        recorder.record_delta(call_id, DELTA_TEXT, "model output")

        stored = "".join(_only_call(recorder)["deltas"]["text"])
        self.assertEqual(stored, "model output")

    def test_signature_deltas_are_never_recorded_by_the_stream_loop(self):
        # The SSE loop only forwards text_delta/thinking_delta; assert that a
        # signature_delta event reaching it produces no stored delta.
        import asyncio

        from agents import llm_client

        class FakeDelta:
            def __init__(self, dtype, **fields):
                self.type = dtype
                for key, value in fields.items():
                    setattr(self, key, value)

        class FakeEvent:
            def __init__(self, delta):
                self.type = "content_block_delta"
                self.delta = delta

        class FakeStream:
            def __init__(self, events):
                self._events = events

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                async def gen():
                    for event in self._events:
                        yield event

                return gen()

            async def get_final_message(self):
                return FakeResponse()

        events = [
            FakeEvent(FakeDelta("thinking_delta", thinking="reasoning")),
            FakeEvent(FakeDelta("signature_delta", signature="AAAA-SECRET-BLOB")),
            FakeEvent(FakeDelta("text_delta", text="visible")),
        ]

        recorder = reset_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "orchestrator.summary"})

        client = llm_client.AsyncAnthropicClient.__new__(llm_client.AsyncAnthropicClient)
        client.mode = "anthropic"
        client._client = mock.Mock()
        client._client.messages.stream = mock.Mock(return_value=FakeStream(events))

        asyncio.run(client._stream_message(progress={"replay_call_id": call_id}))

        stored = "".join(recorder.snapshot()["calls"][0]["deltas"]["text"])
        self.assertNotIn("SECRET", stored)
        self.assertIn("reasoning", stored)
        self.assertIn("visible", stored)
        reset_recorder()


class StreamWiringTests(unittest.TestCase):
    """Guards the llm_client integration, not the recorder in isolation."""

    def _build_client(self, log_requests, semaphore):
        import asyncio

        from agents import llm_client

        class SlowStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                async def gen():
                    if False:  # pragma: no cover -- no events needed here
                        yield None

                return gen()

            async def get_final_message(self):
                await asyncio.sleep(0.05)
                return FakeResponse()

        client = llm_client.AsyncAnthropicClient.__new__(llm_client.AsyncAnthropicClient)
        client.mode = "anthropic"
        client._client = mock.Mock()
        client._client.messages.stream = mock.Mock(return_value=SlowStream())
        client.log_requests = log_requests
        client._request_semaphore = semaphore
        client._request_lock = asyncio.Lock()
        client._metrics_lock = asyncio.Lock()
        client._queued_requests = 0
        client._active_requests = 0
        client._request_sequence = 0
        client.max_concurrent_requests = 1
        client.heartbeat_seconds = 0
        client.metrics_path = None
        client.provider_id = "aws"
        client.model = "claude-5-opus-aws"
        client.mode = "anthropic"
        client.timeout = 240
        client.max_retries = 2
        client.trust_env_proxy = False
        return client

    def _run_three_serialized(self, log_requests):
        import asyncio

        async def main():
            recorder = reset_recorder()
            recorder.begin_run("2026-07-28")
            client = self._build_client(log_requests, asyncio.Semaphore(1))
            await asyncio.gather(
                *[
                    client._create_message(request_context={"caller": f"a.batch_{i}"})
                    for i in range(3)
                ]
            )
            return sorted(c["wait_ms"] for c in recorder.snapshot()["calls"])

        try:
            return asyncio.run(main())
        finally:
            reset_recorder()

    def test_wait_ms_measures_semaphore_queueing_when_logging_enabled(self):
        waits = self._run_three_serialized(log_requests=True)
        self.assertEqual(waits[0], 0)
        self.assertGreater(waits[2], 0)

    def test_wait_ms_measures_semaphore_queueing_when_logging_disabled(self):
        # Replay capture must not depend on LLM_LOG_REQUESTS: the early-return
        # path in _create_message has to instrument queueing too.
        waits = self._run_three_serialized(log_requests=False)
        self.assertEqual(waits[0], 0)
        self.assertGreater(waits[2], 0)


class FinishCallTests(unittest.TestCase):
    def test_ok_outcome_records_usage_and_stop_reason(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "orchestrator.summary"})
        recorder.mark_started(call_id)
        recorder.finish_call(call_id, response=FakeResponse("end_turn"))

        call = _only_call(recorder)
        self.assertEqual(call["outcome"], "ok")
        self.assertEqual(call["stop_reason"], "end_turn")
        self.assertEqual(call["input_tokens"], 120)
        self.assertEqual(call["output_tokens"], 45)
        self.assertEqual(call["cache_read_tokens"], 7)

    def test_max_tokens_and_refusal_map_to_outcomes(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")

        truncated_id = recorder.start_call(1, {"caller": "a"})
        recorder.finish_call(truncated_id, response=FakeResponse("max_tokens"))
        refused_id = recorder.start_call(2, {"caller": "b"})
        recorder.finish_call(refused_id, response=FakeResponse("refusal"))

        outcomes = [c["outcome"] for c in recorder.snapshot()["calls"]]
        self.assertEqual(outcomes, ["truncated", "refused"])

    def test_call_cancelled_while_queued_credits_the_wait(self):
        # A request killed before it cleared the semaphore spent its whole life
        # queued; reporting wait_ms=0 would invert that on the Gantt.
        clock = FakeClock()
        recorder = _new_recorder(clock)
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "a.batch_1"})
        clock.advance_ms(300)
        recorder.finish_call(call_id, error=Exception("cancelled"))

        call = _only_call(recorder)
        self.assertEqual(call["wait_ms"], 300)
        self.assertEqual(call["start_ms"], 300)
        self.assertEqual(call["end_ms"], 300)

    def test_first_terminal_state_wins(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "a"})
        recorder.finish_call(call_id, response=FakeResponse("end_turn"))
        recorder.finish_call(call_id, error=Exception("teardown blew up"))

        call = _only_call(recorder)
        self.assertEqual(call["outcome"], "ok")
        self.assertIsNone(call["error_type"])

    def test_error_records_failed_outcome(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(1, {"caller": "a"})
        recorder.finish_call(call_id, error=TimeoutError("gateway idle"))

        call = _only_call(recorder)
        self.assertEqual(call["outcome"], "failed")
        self.assertEqual(call["error_type"], "TimeoutError")


class IdentityTests(unittest.TestCase):
    def test_call_ids_are_globally_sequential_and_zero_padded(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")

        ids = [recorder.start_call(1, {"caller": f"a.batch_{i}"}) for i in range(3)]

        self.assertEqual(ids, ["c001", "c002", "c003"])

    def test_ids_do_not_collide_across_clients_reusing_request_ids(self):
        # Each routed client numbers its own requests from 1; the recorder's id
        # must stay unique regardless.
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")

        first = recorder.start_call(1, {"caller": "a", "provider_id": "aws"})
        second = recorder.start_call(1, {"caller": "b", "provider_id": "gcp"})

        self.assertNotEqual(first, second)

    def test_context_metadata_is_promoted(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        recorder.start_call(
            4,
            {
                "caller": "research_analyzer.batch_3",
                "provider_id": "gcp",
                "provider_model": "claude-5-opus-gcp",
                "analysis_profile": "STANDARD",
                "adaptive_effort": "xhigh",
                "attempt": 2,
            },
        )

        call = _only_call(recorder)
        self.assertEqual(call["caller"], "research_analyzer.batch_3")
        self.assertEqual(call["provider_id"], "gcp")
        self.assertEqual(call["provider_model"], "claude-5-opus-gcp")
        self.assertEqual(call["analysis_profile"], "STANDARD")
        self.assertEqual(call["adaptive_effort"], "xhigh")
        self.assertEqual(call["context"]["attempt"], 2)

    def test_begin_run_sets_timebase_and_clears_buffers(self):
        recorder = _new_recorder()
        recorder.begin_run("2026-07-28")
        recorder.start_call(1, {"caller": "a"})
        recorder.begin_run("2026-07-29")

        snapshot = recorder.snapshot()
        self.assertEqual(snapshot["date"], "2026-07-29")
        self.assertEqual(snapshot["calls"], [])
        self.assertIsNotNone(snapshot["t0"])


class SingletonTests(unittest.TestCase):
    def test_get_recorder_returns_same_instance(self):
        reset_recorder()
        self.assertIs(get_recorder(), get_recorder())

    def test_reset_recorder_replaces_the_global(self):
        first = reset_recorder()
        second = reset_recorder()
        self.assertIsNot(first, second)
        self.assertIs(get_recorder(), second)


if __name__ == "__main__":
    unittest.main()
