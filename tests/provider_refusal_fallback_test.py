"""A host-side content block is a refusal, and a refusal falls back once.

Why this exists
---------------
2026-11-13: the daily run died five times in a row on one TechCrunch item.
Z.AI -- the only host serving ``z-ai/glm-5.3-flashx`` on OpenRouter -- answered
it with HTTP 200, an empty stream, zero tokens, ``finish_reason: "stop"`` and
``native_finish_reason: "sensitive"``. The client read only ``finish_reason``,
so the block looked like a normal empty answer; the analyzer bisected a batch
of 25 down to that one source, retried it three times and raised
``AnalysisRecoveryExhausted``, aborting the whole pipeline.

Locks in:
  1. ``native_finish_reason`` of ``sensitive``/``content_filter`` maps to
     ``stop_reason == "refusal"`` even when ``finish_reason`` is ``stop``, and
     the refusing upstream host is recorded.
  2. A refused openai-chat call is retried exactly once on the configured
     refusal fallback: another model id, with OpenRouter told to skip the
     configured hosts *and* the host that just refused.
  3. No fallback configured: one request, the refusal is returned as-is.
  4. The fallback also refuses: the refusal is returned (never invented text).
  5. The fallback's cost row names the model that actually answered.
  6. The analyzer stops retrying a refused single source after one attempt.

Offline (no network, no pipeline):

  python3 -m unittest tests.provider_refusal_fallback_test -v
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.llm_client import (  # noqa: E402
    AsyncAnthropicClient,
    _build_openai_chat_body,
    _openai_chat_apply_chunk,
    _openai_chat_new_state,
    _openai_chat_stop_reason,
)

PRIMARY = "z-ai/glm-5.3-flashx"
FALLBACK = "z-ai/GLM-5.3-Flash"


def _refusal_lines(provider="Z.AI"):
    """The exact shape Z.AI sent: one chunk, no deltas, no usage."""
    return [
        "data: " + json.dumps({
            "provider": provider,
            "choices": [{"delta": {}, "finish_reason": "stop",
                         "native_finish_reason": "sensitive"}],
        }),
        "data: [DONE]",
    ]


def _answer_lines(text='{"ok": true}', provider="Crusoe"):
    return [
        "data: " + json.dumps({"provider": provider,
                               "choices": [{"delta": {"content": text}}]}),
        "data: " + json.dumps({"provider": provider,
                               "choices": [{"delta": {}, "finish_reason": "stop",
                                            "native_finish_reason": "stop"}]}),
        "data: " + json.dumps({"usage": {"prompt_tokens": 11, "completion_tokens": 7}}),
        "data: [DONE]",
    ]


class _FakeSSEStream:
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


class _ScriptedHttpClient:
    """Serves one canned stream per request and keeps every request body."""

    def __init__(self, streams):
        self._streams = list(streams)
        self.bodies = []

    def stream(self, method, url, **kwargs):
        self.bodies.append(kwargs.get("json"))
        return _FakeSSEStream(self._streams.pop(0))


def _client(streams, fallback_model=None, ignore=()):
    client = AsyncAnthropicClient.__new__(AsyncAnthropicClient)
    client.provider_id = "openrouter"
    client.model = PRIMARY
    client.mode = "openai-chat"
    client.base_url = "https://openrouter.ai/api"
    client.log_requests = False
    client.metrics_path = None
    client.max_output_tokens = 131072
    client.adaptive_max_tokens = 131072
    client.timeout = 600.0
    client.stream_stall_seconds = 120.0
    client.heartbeat_seconds = 60.0
    client._request_semaphore = None
    client.refusal_fallback_model = fallback_model
    client.refusal_fallback_ignore_providers = list(ignore)
    client._http_client = _ScriptedHttpClient(streams)
    return client


def _call(client):
    from agents.cost_tracker import reset_tracker
    reset_tracker()

    async def scenario():
        return await client.call_with_thinking(
            messages=[{"role": "user", "content": "x"}],
            system="sys",
            caller="news_analyzer.batch_1abbba",
        )

    return asyncio.run(scenario())


class NativeFinishReasonTest(unittest.TestCase):
    def test_sensitive_with_finish_stop_is_a_refusal(self):
        state = _openai_chat_new_state()
        _openai_chat_apply_chunk(json.loads(_refusal_lines()[0][len("data: "):]), state)
        self.assertEqual(state["stop_reason"], "refusal")
        self.assertEqual(state["upstream_provider"], "Z.AI")

    def test_native_content_filter_is_a_refusal(self):
        self.assertEqual(_openai_chat_stop_reason("stop", "content_filter"), "refusal")
        self.assertEqual(_openai_chat_stop_reason("content_filter", None), "refusal")

    def test_ordinary_finish_reasons_are_unchanged(self):
        self.assertEqual(_openai_chat_stop_reason("stop", "stop"), "end_turn")
        self.assertEqual(_openai_chat_stop_reason("stop", None), "end_turn")
        self.assertEqual(_openai_chat_stop_reason("length", "length"), "max_tokens")
        self.assertEqual(_openai_chat_stop_reason("error", None), "error")
        self.assertIsNone(_openai_chat_stop_reason(None, None))

    def test_a_later_plain_stop_chunk_does_not_erase_the_refusal(self):
        state = _openai_chat_new_state()
        _openai_chat_apply_chunk({"choices": [{"delta": {}, "finish_reason": "stop",
                                               "native_finish_reason": "sensitive"}]}, state)
        _openai_chat_apply_chunk({"choices": [{"delta": {}, "finish_reason": "stop",
                                               "native_finish_reason": "stop"}]}, state)
        self.assertEqual(state["stop_reason"], "refusal")


class RequestBodyProviderPreferencesTest(unittest.TestCase):
    def test_provider_preferences_ride_the_body_only_when_given(self):
        plain = _build_openai_chat_body(PRIMARY, None, [], 10, None)
        self.assertNotIn("provider", plain)
        body = _build_openai_chat_body(PRIMARY, None, [], 10, None,
                                       provider={"ignore": ["Z.AI"]})
        self.assertEqual(body["provider"], {"ignore": ["Z.AI"]})


class RefusalFallbackTest(unittest.TestCase):
    def setUp(self):
        # The env overrides win over configuration; keep the host's out.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("LLM_REFUSAL_FALLBACK_MODEL", None)
        os.environ.pop("LLM_REFUSAL_FALLBACK_IGNORE_PROVIDERS", None)

    def test_refusal_is_retried_once_on_the_fallback_without_the_refusing_host(self):
        client = _client([_refusal_lines(), _answer_lines()],
                         fallback_model=FALLBACK, ignore=["some-other-host"])
        response = _call(client)

        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(response.content, '{"ok": true}')
        self.assertEqual(response.model, FALLBACK)

        first, second = client._http_client.bodies
        self.assertEqual(first["model"], PRIMARY)
        self.assertNotIn("provider", first)
        self.assertEqual(second["model"], FALLBACK)
        self.assertEqual(second["provider"], {"ignore": ["some-other-host", "Z.AI"]})
        # Same prompt, same effort: only the destination changes.
        self.assertEqual(second["messages"], first["messages"])
        self.assertEqual(second.get("reasoning"), first.get("reasoning"))

    def test_fallback_cost_row_names_the_model_that_answered(self):
        from agents.cost_tracker import get_tracker
        client = _client([_refusal_lines(), _answer_lines()], fallback_model=FALLBACK)
        _call(client)
        rows = [r for r in get_tracker().calls if r.output_tokens]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model, FALLBACK)

    def test_without_a_fallback_the_refusal_is_returned_after_one_request(self):
        client = _client([_refusal_lines()])
        response = _call(client)
        self.assertEqual(response.stop_reason, "refusal")
        self.assertEqual(response.content, "")
        self.assertEqual(len(client._http_client.bodies), 1)

    def test_a_refusing_fallback_returns_the_refusal(self):
        client = _client([_refusal_lines(), _refusal_lines("Novita")],
                         fallback_model=FALLBACK)
        response = _call(client)
        self.assertEqual(response.stop_reason, "refusal")
        self.assertEqual(response.content, "")
        self.assertEqual(len(client._http_client.bodies), 2)

    def test_environment_overrides_configuration(self):
        os.environ["LLM_REFUSAL_FALLBACK_MODEL"] = "other/model"
        os.environ["LLM_REFUSAL_FALLBACK_IGNORE_PROVIDERS"] = "z-ai, Foo"
        client = _client([_refusal_lines(), _answer_lines()],
                         fallback_model=FALLBACK, ignore=["configured"])
        _call(client)
        second = client._http_client.bodies[1]
        self.assertEqual(second["model"], "other/model")
        self.assertEqual(second["provider"], {"ignore": ["z-ai", "Foo", "Z.AI"]})

    def test_an_ordinary_answer_makes_one_request(self):
        client = _client([_answer_lines()], fallback_model=FALLBACK)
        response = _call(client)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(len(client._http_client.bodies), 1)


class RefusalFallbackConfigTest(unittest.TestCase):
    def test_routes_inherit_and_override_the_root_fallback(self):
        from agents.config.schema import LLMProviderConfig
        config = LLMProviderConfig(
            mode="openai-chat", api_key="k", base_url="https://openrouter.ai/api",
            model=PRIMARY, refusal_fallback_model=FALLBACK,
            refusal_fallback_ignore_providers=["z-ai"],
            routes=[{"id": "a", "model": PRIMARY},
                    {"id": "b", "model": PRIMARY, "refusal_fallback_model": "x/y",
                     "refusal_fallback_ignore_providers": []}],
        )
        a, b = config.get_route_configs()
        self.assertEqual((a.refusal_fallback_model, a.refusal_fallback_ignore_providers),
                         (FALLBACK, ["z-ai"]))
        self.assertEqual((b.refusal_fallback_model, b.refusal_fallback_ignore_providers),
                         ("x/y", []))

    def test_unset_means_no_fallback(self):
        from agents.config.schema import LLMProviderConfig
        config = LLMProviderConfig(mode="openai-chat", api_key="k",
                                   base_url="https://openrouter.ai/api", model=PRIMARY)
        (route,) = config.get_route_configs()
        self.assertIsNone(route.refusal_fallback_model)


if __name__ == "__main__":
    unittest.main()
