"""OpenAI chat-completions transport for OpenRouter-native models (ox-alpha).

Why this exists
---------------
stealth/ox-alpha's endpoint declares supported_parameters
[reasoning, include_reasoning, ..., reasoning_effort] -- pure OpenAI-style.
Nothing Anthropic-shaped (`thinking`, `output_config`) is listed, so the
adaptive-thinking knobs this pipeline sends over the /messages compat layer
are undocumented for this provider and likely ignored: QUICK and DEEP calls
produced nearly identical thinking volume all day on 2026-08-22. OpenRouter's
documented control surface is the ``reasoning`` object on chat/completions,
whose effort enum includes max/xhigh/high/medium/low/minimal/none.

Locks in:
  1. Request bodies: system -> system-role message, reasoning.effort mapped
     from the Anthropic-style extra_body, stream + include_usage set, and NO
     Anthropic-only keys leaking onto the wire.
  2. Chunk accumulation: reasoning vs content deltas land as separate blocks,
     finish_reason maps to stop_reason (length -> max_tokens), usage maps
     prompt/completion -> input/output tokens, and error chunks raise.
  3. Transport anomalies (bare AssertionError from a dying connection) are
     classified RETRYABLE -- the 14:49 enrichment burst died on exactly that,
     unretried.
  4. Schema accepts llm.mode "openai-chat" end to end.

Stdlib-only unittest:

  python3 -m unittest tests.openai_chat_transport_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import json

from agents.llm_client import (  # noqa: E402
    _build_openai_chat_body,
    _openai_chat_new_state,
    _openai_chat_apply_chunk,
)
from agents.config.schema import LLMProviderConfig, LLMRouteConfig  # noqa: E402


class RequestBodyTest(unittest.TestCase):
    def test_system_becomes_system_role_and_effort_maps(self):
        body = _build_openai_chat_body(
            model="stealth/ox-alpha",
            system="You are a news analyst.",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1234,
            effort="xhigh",
        )
        self.assertEqual(body["model"], "stealth/ox-alpha")
        self.assertEqual(body["max_tokens"], 1234)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][0]["content"], "You are a news analyst.")
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})
        self.assertEqual(body["reasoning"], {"effort": "xhigh"})
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        # Anthropic-only keys must never reach the wire in this mode.
        for forbidden in ("thinking", "extra_body", "system"):
            self.assertNotIn(forbidden, body)

    def test_no_effort_omits_reasoning(self):
        body = _build_openai_chat_body(
            model="m", system=None, messages=[{"role": "user", "content": "x"}],
            max_tokens=10, effort=None,
        )
        self.assertNotIn("reasoning", body)
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])


class ChunkAccumulationTest(unittest.TestCase):
    def _state(self):
        return _openai_chat_new_state()

    def test_reasoning_and_content_deltas_separate_blocks(self):
        state = self._state()
        for chunk in (
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"reasoning": "think one "}}]},
            {"choices": [{"delta": {"reasoning": "think two"}}]},
            {"choices": [{"delta": {"content": "answer"}}]},
        ):
            _openai_chat_apply_chunk(chunk, state)
        self.assertEqual(state["thinking"], ["think one ", "think two"])
        self.assertEqual(state["text"], ["answer"])

    def test_finish_reason_length_maps_to_max_tokens(self):
        state = self._state()
        _openai_chat_apply_chunk(
            {"choices": [{"delta": {}, "finish_reason": "length"}]}, state)
        self.assertEqual(state["stop_reason"], "max_tokens")

        state = self._state()
        _openai_chat_apply_chunk(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]}, state)
        self.assertEqual(state["stop_reason"], "end_turn")

    def test_usage_chunk_maps_token_fields(self):
        state = self._state()
        _openai_chat_apply_chunk({
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 7,
            },
        }, state)
        self.assertEqual(state["usage"]["input_tokens"], 100)
        self.assertEqual(state["usage"]["output_tokens"], 200)
        self.assertEqual(state["usage"]["cache_read_input_tokens"], 5)
        self.assertEqual(state["usage"]["cache_creation_input_tokens"], 7)

    def test_error_chunk_raises_runtime_error(self):
        state = self._state()
        with self.assertRaises(RuntimeError) as ctx:
            _openai_chat_apply_chunk(
                {"error": {"message": "rate limited", "code": 429}}, state)
        self.assertIn("rate limited", str(ctx.exception))


class RetryClassificationTest(unittest.TestCase):
    def test_assertion_error_is_retryable(self):
        from agents.llm_client import AsyncLLMRouter

        reason = AsyncLLMRouter._retry_reason(AssertionError())
        self.assertIsNotNone(
            reason, "a bare AssertionError killed 11 enrichment calls at "
                    "2026-08-22T14:49 without any retry")


class SchemaModeTest(unittest.TestCase):
    def test_route_accepts_openai_chat_mode(self):
        cfg = LLMRouteConfig(id="openrouter", mode="openai-chat",
                             api_key="k", model="stealth/ox-alpha")
        self.assertEqual(cfg.mode, "openai-chat")

    def test_provider_accepts_openai_chat_mode(self):
        cfg = LLMProviderConfig(mode="openai-chat", api_key="k",
                                base_url="https://openrouter.ai/api",
                                model="stealth/ox-alpha")
        routes = cfg.get_route_configs()
        self.assertEqual(routes[0].mode, "openai-chat")


if __name__ == "__main__":
    unittest.main()


class ConsolidatedBlocksTest(unittest.TestCase):
    """The shim must consolidate deltas into <=2 blocks, Anthropic-style.

    2026-08-22: the shim emitted one content block per streamed delta;
    ox-alpha streams ~5-char deltas, so a single answer arrived as 145
    "text blocks" / 299 "thinking blocks" -- and call_with_thinking's
    "\\n".join(text_blocks) / "\\n\\n".join(thinking_blocks) (a no-op under
    Anthropic's 1-block shape) injected a separator after every fragment.
    Every async response was corrupted mid-token: the news filter parsed
    0 IDs out of an answer its own thinking had matched, and analyzer map
    batches dropped dozens of entries whose ids had been shredded.
    """

    def _state_with_deltas(self):
        state = _openai_chat_new_state()
        for piece in ('{"ai', '_artic', 'le_ids"'):
            _openai_chat_apply_chunk(
                {"choices": [{"delta": {"content": piece}}]}, state)
        for piece in ("think ", "piece ", "three"):
            _openai_chat_apply_chunk(
                {"choices": [{"delta": {"reasoning": piece}}]}, state)
        return state

    def test_many_deltas_consolidate_to_two_blocks(self):
        from agents.llm_client import _openai_chat_blocks
        blocks = _openai_chat_blocks(self._state_with_deltas())
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].type, "thinking")
        self.assertEqual(blocks[0].thinking, "think piece three")
        self.assertEqual(blocks[1].type, "text")
        self.assertEqual(blocks[1].text, '{"ai_article_ids"')

    def test_no_deltas_produce_no_blocks(self):
        from agents.llm_client import _openai_chat_blocks
        self.assertEqual(_openai_chat_blocks(_openai_chat_new_state()), [])

    def test_text_only_stream_is_a_single_text_block(self):
        from agents.llm_client import _openai_chat_blocks
        state = _openai_chat_new_state()
        _openai_chat_apply_chunk(
            {"choices": [{"delta": {"content": "hello"}}]}, state)
        blocks = _openai_chat_blocks(state)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].type, "text")
        self.assertEqual(blocks[0].text, "hello")


class _FakeSSEStream:
    """Async context manager yielding canned SSE lines, like httpx."""

    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def aiter_lines(self):
        async def gen():
            for line in self._lines:
                yield line
        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttpClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):
        return _FakeSSEStream(self._lines)


class EndToEndSSEAssemblyTest(unittest.TestCase):
    """Full loop: SSE deltas -> shim -> call_with_thinking's LLMResponse.

    Locks the actual corruption site end to end: LLMResponse.content must be
    byte-identical to what the model streamed -- no separators injected
    between deltas -- so JSON payloads and their ids survive intact.
    """

    PAYLOAD = '{"ai_article_ids": ["abc123def456", "7890123456ab"], "note": "ok"}'
    REASONING = "Let me check each item against the frontier bar."

    def _sse_lines(self):
        lines = []
        # Interleave reasoning and content deltas at awkward boundaries,
        # including mid-token splits inside string values.
        pieces = [self.REASONING[i:i + 4]
                  for i in range(0, len(self.REASONING), 4)]
        body_pieces = [self.PAYLOAD[i:i + 6]
                       for i in range(0, len(self.PAYLOAD), 6)]
        for i, piece in enumerate(pieces):
            lines.append('data: ' + json.dumps(
                {"choices": [{"delta": {"reasoning": piece}}]}))
        for piece in body_pieces:
            lines.append('data: ' + json.dumps(
                {"choices": [{"delta": {"content": piece}}]}))
        lines.append('data: ' + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]}))
        lines.append('data: ' + json.dumps(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}))
        lines.append("data: [DONE]")
        return lines

    def _client(self):
        from agents.llm_client import AsyncAnthropicClient
        client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
        client.provider_id = "openrouter"
        client.model = "stealth/ox-alpha"
        client.mode = "openai-chat"
        client.base_url = "https://openrouter.ai/api"
        client.log_requests = False
        client.metrics_path = None
        client.max_output_tokens = 128000
        client.adaptive_max_tokens = 65536
        client.timeout_seconds = 600.0
        client.sdk_max_retries = 2
        client.heartbeat_seconds = 60.0
        client.trust_env_proxy = False
        client.request_logging = False
        client._request_semaphore = None
        client._http_client = _FakeHttpClient(self._sse_lines())
        return client

    def test_response_content_survives_fragmented_stream_intact(self):
        from agents.cost_tracker import reset_tracker
        reset_tracker()
        client = self._client()

        async def scenario():
            return await client.call_with_thinking(
                messages=[{"role": "user", "content": "x"}],
                system="sys",
                caller="news_analyzer.filter",
            )

        response = asyncio.run(scenario())
        self.assertEqual(response.stop_reason, "end_turn")
        # Byte-for-byte: the join sites in call_with_thinking must see a
        # single text block so the newline join is a no-op, exactly as in
        # Anthropic mode.
        self.assertEqual(response.content, self.PAYLOAD)
        parsed = json.loads(response.content)
        self.assertEqual(parsed["ai_article_ids"],
                         ["abc123def456", "7890123456ab"])
        self.assertEqual(response.thinking, self.REASONING)


if __name__ == "__main__":
    unittest.main()
