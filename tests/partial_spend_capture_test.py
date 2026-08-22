"""A call that fails mid-stream must still be billed for what it streamed.

`CostTracker.record_call` normally runs off the final response, so before this a
failed attempt contributed nothing to the run total -- despite the provider having
charged for every token it already emitted. On 2026-07-28 five analyzer batches
failed after streaming ~140k characters between them; none of that reached the
cost report.

The counts are available: `message_start` settles `input_tokens`, and every
`message_delta` re-sends the running `output_tokens`. This exercises the real SSE
loop against a stream that raises partway through, and asserts the tokens seen up
to that point are recorded.

Stdlib-only (unittest), matching the repo's other tests so it runs in CI without
pytest or any extra deps (httpx + anthropic only, for llm_client's imports):

  python3 -m unittest tests.partial_spend_capture_test -v
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.cost_tracker import CostTracker, get_tracker, reset_tracker
from agents.llm_client import AsyncAnthropicClient


class _Usage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Event:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


def _message_start(input_tokens, cache_read=0):
    return _Event(
        "message_start",
        message=_Event(
            "message",
            usage=_Usage(
                input_tokens=input_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=0,
            ),
        ),
    )


def _text_delta(text):
    return _Event("content_block_delta", delta=_Event("text_delta", text=text))


def _message_delta(output_tokens):
    return _Event("message_delta", usage=_Usage(output_tokens=output_tokens))


class _ExplodingStream:
    """Async-iterates the given events, then raises."""

    def __init__(self, events, error):
        self._events = events
        self._error = error

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event
            raise self._error

        return gen()


class _StreamCtx:
    def __init__(self, stream):
        self._stream = stream

    async def __aenter__(self):
        return self._stream

    async def __aexit__(self, *exc):
        return False


class PartialSpendCaptureTest(unittest.TestCase):
    def setUp(self):
        reset_tracker()
        self.tracker = get_tracker()

    def tearDown(self):
        reset_tracker()

    def _client(self, provider_id, model):
        client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
        client.provider_id = provider_id
        client.model = model
        client.log_requests = False
        client._request_semaphore = None
        client.mode = "anthropic"
        return client

    def test_mid_stream_failure_is_billed_for_what_it_streamed(self):
        client = self._client("gcp", "claude-5-opus-gcp")

        boom = RuntimeError("connection reset mid-stream")
        events = [
            _message_start(input_tokens=33_727, cache_read=1_200),
            _text_delta("some output"),
            _message_delta(output_tokens=12_000),
            _text_delta("more output"),
            _message_delta(output_tokens=21_478),
        ]

        class _Messages:
            def stream(self, **kwargs):
                return _StreamCtx(_ExplodingStream(events, boom))

        client._client = _Event("client", messages=_Messages())

        async def scenario():
            with self.assertRaises(RuntimeError):
                await client._create_message(
                    request_context={
                        "caller": "research_analyzer.batch_3",
                        "analysis_profile": "STANDARD",
                        "adaptive_effort": "xhigh",
                        "provider_model": "claude-5-opus-gcp",
                    },
                    model="claude-5-opus-gcp",
                    messages=[{"role": "user", "content": "x"}],
                )

        asyncio.run(scenario())

        self.assertEqual(len(self.tracker.calls), 1,
                         "the failed attempt should have been billed")
        rec = self.tracker.calls[0]
        self.assertEqual(rec.caller, "research_analyzer.batch_3")
        # Last figures seen before the stream died.
        self.assertEqual(rec.output_tokens, 21_478)
        self.assertEqual(rec.input_tokens, 33_727)
        self.assertEqual(rec.cache_read_tokens, 1_200)
        self.assertTrue(rec.partial,
                        "must be flagged as a floor, not an exact figure")

        cost = self.tracker.calculate_cost(rec)
        self.assertGreater(cost.total_cost, 0,
                           "billed tokens must produce a non-zero cost")

    def test_failure_before_any_token_records_nothing(self):
        """No usage seen => nothing to bill. Don't invent a row."""
        client = self._client("aws", "claude-5-opus-aws")

        class _Messages:
            def stream(self, **kwargs):
                return _StreamCtx(_ExplodingStream([], ConnectionError("refused")))

        client._client = _Event("client", messages=_Messages())

        async def scenario():
            with self.assertRaises(ConnectionError):
                await client._create_message(
                    request_context={"caller": "news_analyzer.filter"},
                    model="claude-5-opus-aws",
                    messages=[{"role": "user", "content": "x"}],
                )

        asyncio.run(scenario())

        self.assertEqual(self.tracker.calls, [],
                         "a call that streamed nothing has nothing to bill")

    def test_successful_call_is_not_double_billed(self):
        """The partial path must not fire when the stream completes normally."""
        client = self._client("gcp", "claude-5-opus-gcp")

        final = _Event("message", usage=_Usage(input_tokens=100, output_tokens=200))

        class _Stream:
            def __aiter__(self):
                async def gen():
                    for e in (_message_start(100), _message_delta(200)):
                        yield e

                return gen()

            async def get_final_message(self):
                return final

        class _Messages:
            def stream(self, **kwargs):
                return _StreamCtx(_Stream())

        client._client = _Event("client", messages=_Messages())

        async def scenario():
            return await client._create_message(
                request_context={"caller": "news_analyzer.batch_0"},
                model="claude-5-opus-gcp",
                messages=[{"role": "user", "content": "x"}],
            )

        result = asyncio.run(scenario())

        self.assertIs(result, final)
        # _create_message does not itself record on success -- the caller does, off the
        # final response. The point here is that the partial path stayed quiet.
        self.assertEqual(self.tracker.calls, [])


if __name__ == "__main__":
    unittest.main()
