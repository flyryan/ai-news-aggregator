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
"""

import sys
from pathlib import Path

import pytest

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


@pytest.fixture
def tracker():
    reset_tracker()
    yield get_tracker()
    reset_tracker()


@pytest.mark.asyncio
async def test_mid_stream_failure_is_billed_for_what_it_streamed(tracker, monkeypatch):
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "gcp"
    client.model = "claude-5-opus-gcp"
    client.log_requests = False
    client._request_semaphore = None

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

    with pytest.raises(RuntimeError):
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

    assert len(tracker.calls) == 1, "the failed attempt should have been billed"
    rec = tracker.calls[0]
    assert rec.caller == "research_analyzer.batch_3"
    # Last figures seen before the stream died.
    assert rec.output_tokens == 21_478
    assert rec.input_tokens == 33_727
    assert rec.cache_read_tokens == 1_200
    assert rec.partial is True, "must be flagged as a floor, not an exact figure"

    cost = tracker.calculate_cost(rec)
    assert cost.total_cost > 0, "billed tokens must produce a non-zero cost"


@pytest.mark.asyncio
async def test_failure_before_any_token_records_nothing(tracker):
    """No usage seen => nothing to bill. Don't invent a row."""
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "aws"
    client.model = "claude-5-opus-aws"
    client.log_requests = False
    client._request_semaphore = None

    class _Messages:
        def stream(self, **kwargs):
            return _StreamCtx(_ExplodingStream([], ConnectionError("refused")))

    client._client = _Event("client", messages=_Messages())

    with pytest.raises(ConnectionError):
        await client._create_message(
            request_context={"caller": "news_analyzer.filter"},
            model="claude-5-opus-aws",
            messages=[{"role": "user", "content": "x"}],
        )

    assert tracker.calls == [], "a call that streamed nothing has nothing to bill"


@pytest.mark.asyncio
async def test_successful_call_is_not_double_billed(tracker):
    """The partial path must not fire when the stream completes normally."""
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "gcp"
    client.model = "claude-5-opus-gcp"
    client.log_requests = False
    client._request_semaphore = None

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

    result = await client._create_message(
        request_context={"caller": "news_analyzer.batch_0"},
        model="claude-5-opus-gcp",
        messages=[{"role": "user", "content": "x"}],
    )

    assert result is final
    # _create_message does not itself record on success -- the caller does, off the
    # final response. The point here is that the partial path stayed quiet.
    assert tracker.calls == []
