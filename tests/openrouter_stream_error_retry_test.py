"""In-band OpenRouter SSE error chunks are retryable.

Why this exists
---------------
2026-09-04: the production run lost 3 of 11 link-enrichment calls to an error
object delivered *inside* an otherwise healthy 200 stream --
``{"error": {"code": 504, "message": "Upstream idle timeout exceeded"}}`` --
after minutes of reasoning had already been spent. None of the three were
retried. The transport retry loop was working perfectly; it simply could not
see them: ``_openai_chat_apply_chunk`` raised a bare ``RuntimeError``, and
``_transient_retry_reason`` classifies on ``status_code``, which a bare
RuntimeError does not have. An upstream timeout -- the single most retryable
failure there is -- was therefore treated exactly like a prompt error and the
enrichment was dropped from the published report.

Locks in:
  1. An error chunk raises ``OpenRouterStreamError`` (still a RuntimeError, so
     nothing that catches broadly changes behaviour) carrying the chunk's code
     as ``status_code``.
  2. ``_transient_retry_reason`` classifies it like any HTTP status: 504 and
     429 retry, 400 does not, and a chunk with no code is not retried either
     (an unclassifiable failure must not become an infinite retry).
  3. The message text ``OpenRouter stream error (code=...): ...`` is unchanged
     -- it is what the incident was grepped out of the run log by.
  4. The retry actually happens end to end: a 504 stream error inside
     ``_create_message`` is retried by ``_create_message_with_retries`` and the
     next attempt's response is returned; a 400 one still fails after exactly
     one attempt.

Stdlib-only unittest (no network, no pipeline):

  python3 -m unittest tests.openrouter_stream_error_retry_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from agents.llm_client import (  # noqa: E402
    AsyncAnthropicClient,
    OpenRouterStreamError,
    _openai_chat_apply_chunk,
    _openai_chat_new_state,
    _transient_retry_reason,
)


class ErrorChunkClassificationTest(unittest.TestCase):
    """The exact chunk that killed three enrichment calls, and its neighbours."""

    def _raise_error_chunk(self, error_obj):
        """Feed one ``{"error": {...}}`` chunk through the accumulator."""
        with self.assertRaises(OpenRouterStreamError) as caught:
            _openai_chat_apply_chunk({"error": error_obj}, _openai_chat_new_state())
        return caught.exception

    def test_upstream_idle_timeout_is_a_retryable_504(self):
        error = self._raise_error_chunk(
            {"code": 504, "message": "Upstream idle timeout exceeded"}
        )
        # Still a RuntimeError: callers that catch broadly must not change.
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(error.status_code, 504)
        self.assertEqual(
            str(error),
            "OpenRouter stream error (code=504): Upstream idle timeout exceeded",
        )
        self.assertEqual(_transient_retry_reason(error), "http_504")

    def test_string_code_is_parsed(self):
        # OpenRouter has sent both shapes; a quoted code must not silently
        # become an unclassifiable failure.
        error = self._raise_error_chunk({"code": "429", "message": "rate limited"})
        self.assertEqual(error.status_code, 429)
        self.assertEqual(_transient_retry_reason(error), "http_429")

    def test_client_error_chunk_is_not_retried(self):
        error = self._raise_error_chunk({"code": 400, "message": "bad request"})
        self.assertEqual(error.status_code, 400)
        self.assertIsNone(
            _transient_retry_reason(error),
            "a 400 inside the stream must fail fast, exactly like a 400 response",
        )

    def test_codeless_chunk_is_not_retried(self):
        error = self._raise_error_chunk({"message": "no code"})
        self.assertIsNone(error.status_code)
        self.assertIsNone(_transient_retry_reason(error))
        self.assertEqual(str(error), "OpenRouter stream error (code=None): no code")


def _retry_client(outcomes):
    """An AsyncAnthropicClient with `_create_message` replaced, zero delays.

    Built with ``__new__`` plus the attributes the retry loop reads, the way
    tests/openai_chat_transport_test.py does: the loop is the unit under test,
    so nothing else -- credentials, http client, semaphore -- needs to exist.
    """
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "openrouter"
    client.model = "z-ai/GLM-5.3-Flash"
    client.mode = "openai-chat"
    client.retry_max_attempts = 3
    # Zero delays: this asserts *whether* a retry happens, not the schedule
    # (tests/llm_rate_limit_resilience_test.py owns the backoff behaviour).
    client.retry_base_delay = 0.0
    client.retry_max_delay = 0.0
    client.retry_contended_delay = 0.0
    client.retry_max_elapsed = 30.0
    client.retry_liveness_window = 180.0
    client._provider_alive_at = 0.0
    client.calls = 0

    remaining = list(outcomes)

    async def fake_create_message(request_context=None, **kwargs):
        client.calls += 1
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client._create_message = fake_create_message
    return client


class RetryLoopIntegrationTest(unittest.TestCase):
    """The point of the whole change: the enrichment call survives the 504."""

    def test_504_stream_error_is_retried_and_the_next_attempt_wins(self):
        response = SimpleNamespace(content="enriched summary")
        client = _retry_client([
            OpenRouterStreamError(
                "OpenRouter stream error (code=504): Upstream idle timeout exceeded",
                status_code=504,
            ),
            response,
        ])

        with self.assertLogs("agents.llm_client", level="WARNING") as logs:
            result = asyncio.run(
                client._create_message_with_retries(
                    request_context={"caller": "link_enricher.executive"}
                )
            )

        self.assertIs(result, response)
        self.assertEqual(client.calls, 2, "the 504 chunk must cost one retry, not the call")
        # The operator-visible evidence: this morning's log had no such line.
        self.assertIn("reason=http_504", "\n".join(logs.output))

    def test_400_stream_error_still_fails_after_one_attempt(self):
        client = _retry_client([
            OpenRouterStreamError(
                "OpenRouter stream error (code=400): bad request",
                status_code=400,
            ),
            SimpleNamespace(content="never reached"),
        ])

        with self.assertRaises(OpenRouterStreamError):
            asyncio.run(
                client._create_message_with_retries(
                    request_context={"caller": "link_enricher.executive"}
                )
            )
        self.assertEqual(client.calls, 1, "a prompt error must not burn retry budget")


if __name__ == "__main__":
    unittest.main()
