"""Streaming LLM calls are bounded end to end, and a slow failure still retries.

Why this exists
---------------
2026-09-29: both the scheduled 3 AM ET run and a manual re-run failed in
Phase 2 with no report published. Around 200 calls per run were healthy
(p50 ~50s), but a handful of z-ai/GLM-5.3-Flash streams on OpenRouter ran
20-40 minutes and then died, and a single dead batch aborts the run
(``AnalysisRecoveryExhausted``). Three defects in ``agents/llm_client.py``
turned a provider hiccup into a failed day:

  1. No end-to-end bound. ``_stream_timeout`` put ``LLM_TIMEOUT_SECONDS`` in
     httpx's ``pool`` slot, which only bounds waiting for a pooled
     connection. ``reddit_analyzer.batch_22`` streamed ~228k chars of
     runaway text for 39 minutes until OpenRouter cut it with a 502.
  2. The stall clock watched bytes, not output. OpenRouter sends SSE
     keep-alive comments while the upstream is silent; they reset httpx's
     read timeout but are (correctly) skipped by the parser. So
     ``social_analyzer.batch_12`` sat at ``stall=2377s`` and never timed out.
  3. The retry deadline started before the first attempt. When that attempt
     itself outlived ``LLM_RETRY_MAX_ELAPSED_SECONDS`` (900s) its failure got
     zero retries: ``reason=http_502 after 900s (1 attempts); retry deadline
     exhausted``.

Locks in:
  * An attempt that is still streaming past ``LLM_TIMEOUT_SECONDS`` is
    cancelled with ``LLMAttemptTimeout`` -- on both transports.
  * An attempt whose model output has been silent for
    ``LLM_STREAM_STALL_SECONDS`` is cancelled with ``LLMStreamStalled`` even
    while keep-alive bytes keep arriving.
  * Both are transient, so the transport retry loop and the analyzers'
    recovery path treat them like any other timeout.
  * The retry deadline is measured from the first failure, so a slow first
    failure still gets retried.

Stdlib-only unittest (no network, no pipeline):

  python3 -m unittest tests.llm_stream_watchdog_test -v
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.llm_client import (  # noqa: E402
    AsyncAnthropicClient,
    LLMAttemptTimeout,
    LLMStreamStalled,
    OpenRouterStreamError,
    _transient_retry_reason,
)


def _content_line(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


class _ScriptedSSEStream:
    """httpx-like streaming response driven by an async line generator."""

    def __init__(self, line_factory):
        self._line_factory = line_factory
        self.status_code = 200

    def aiter_lines(self):
        return self._line_factory()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ScriptedHttpClient:
    def __init__(self, line_factory):
        self._line_factory = line_factory

    def stream(self, method, url, **kwargs):
        return _ScriptedSSEStream(self._line_factory)


def _openai_client(line_factory, *, timeout=5.0, stall=5.0):
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "openrouter"
    client.model = "z-ai/GLM-5.3-Flash"
    client.mode = "openai-chat"
    client.base_url = "https://openrouter.ai/api"
    client.timeout = timeout
    client.stream_stall_seconds = stall
    client._provider_alive_at = 0.0
    client._http_client = _ScriptedHttpClient(line_factory)
    return client


def _run_stream(client, progress=None):
    return asyncio.run(client._stream_message(
        progress=progress,
        model=client.model,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=1024,
    ))


class AttemptDeadlineTest(unittest.TestCase):
    """Defect 1: a runaway stream that never goes quiet must still end."""

    def test_runaway_openai_stream_is_cut_at_the_attempt_timeout(self):
        async def runaway():
            # Healthy-looking output forever: the stall clock never fires,
            # so only a real end-to-end bound can stop it.
            while True:
                yield _content_line("loop ")
                await asyncio.sleep(0.01)

        client = _openai_client(runaway, timeout=0.3, stall=5.0)
        started = time.monotonic()
        with self.assertRaises(LLMAttemptTimeout):
            _run_stream(client, progress={})
        self.assertLess(time.monotonic() - started, 2.0)

    def test_anthropic_transport_is_bounded_too(self):
        client = _openai_client(None, timeout=0.3, stall=5.0)
        client.mode = "anthropic"

        async def endless(progress, **kwargs):
            while True:
                progress["last_chunk_at"] = time.time()
                await asyncio.sleep(0.01)

        client._stream_message_anthropic = endless
        with self.assertRaises(LLMAttemptTimeout):
            _run_stream(client, progress=None)

    def test_a_healthy_stream_is_returned_unchanged(self):
        async def healthy():
            yield _content_line('{"ok": true}')
            yield "data: " + json.dumps(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]"

        client = _openai_client(healthy, timeout=5.0, stall=5.0)
        response = _run_stream(client, progress=None)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(response.content[0].text, '{"ok": true}')


class OutputStallTest(unittest.TestCase):
    """Defect 2: keep-alive bytes are not model output."""

    def test_keepalives_do_not_hide_a_silent_upstream(self):
        async def silent_after_first_token():
            yield _content_line("partial")
            while True:
                # What OpenRouter sends while the upstream says nothing.
                yield ": OPENROUTER PROCESSING"
                await asyncio.sleep(0.01)

        client = _openai_client(silent_after_first_token, timeout=10.0, stall=0.2)
        started = time.monotonic()
        with self.assertRaises(LLMStreamStalled):
            _run_stream(client, progress=None)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_no_first_token_counts_as_a_stall(self):
        async def never_starts():
            while True:
                yield ": OPENROUTER PROCESSING"
                await asyncio.sleep(0.01)

        client = _openai_client(never_starts, timeout=10.0, stall=0.2)
        with self.assertRaises(LLMStreamStalled):
            _run_stream(client, progress={})


class ClassificationTest(unittest.TestCase):
    def test_watchdog_errors_are_transient(self):
        self.assertEqual(
            _transient_retry_reason(LLMAttemptTimeout("x")), "LLMAttemptTimeout")
        self.assertEqual(
            _transient_retry_reason(LLMStreamStalled("x")), "LLMStreamStalled")


class RetryDeadlineStartsAtFirstFailureTest(unittest.TestCase):
    """Defect 3: a first attempt slower than the retry window still retries."""

    def test_slow_first_failure_is_retried(self):
        client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
        client.provider_id = "openrouter"
        client.model = "z-ai/GLM-5.3-Flash"
        client.mode = "openai-chat"
        client.retry_max_attempts = 3
        client.retry_base_delay = 0.0
        client.retry_max_delay = 0.0
        client.retry_contended_delay = 0.0
        # Shorter than the first attempt below -- the 2026-09-29 shape, where
        # a 2340s attempt met a 900s retry window.
        client.retry_max_elapsed = 0.1
        client.retry_liveness_window = 180.0
        client._provider_alive_at = 0.0
        client.calls = 0
        response = SimpleNamespace(content="analysis")

        async def fake_create_message(request_context=None, **kwargs):
            client.calls += 1
            if client.calls == 1:
                await asyncio.sleep(0.25)
                raise OpenRouterStreamError(
                    "OpenRouter stream ended with finish_reason=error", status_code=502)
            return response

        client._create_message = fake_create_message
        with self.assertLogs("agents.llm_client", level="WARNING"):
            result = asyncio.run(client._create_message_with_retries(
                request_context={"caller": "reddit_analyzer.batch_22"}))
        self.assertIs(result, response)
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
