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
