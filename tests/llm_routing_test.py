"""Focused tests for Opus 5 adaptive thinking and async LLM routing."""

import asyncio
import os
import unittest

import httpx
from pydantic import ValidationError

from agents.config.schema import LLMProviderConfig, LLMRouteConfig
from agents.llm_client import (
    AsyncAnthropicClient,
    AsyncLLMRouter,
    LLMResponse,
    ThinkingLevel,
    _uses_adaptive_thinking,
)


class FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class FakeTextBlock:
    type = "text"
    text = "ok"


class FakeAnthropicResponse:
    content = [FakeTextBlock()]
    usage = FakeUsage()
    model = "claude-5-opus-aws"
    stop_reason = "end_turn"


class FakeRouteClient:
    def __init__(self, provider_id, model=None, failures=None):
        self.provider_id = provider_id
        self.model = model or f"claude-5-opus-{provider_id}"
        self.max_concurrent_requests = 8
        self.failures = list(failures or [])
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        return LLMResponse(
            content=self.provider_id,
            thinking=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            model=self.model,
        )

    async def call_with_thinking(self, **kwargs):
        return await self.call(**kwargs)

    async def close(self):
        return None


class HTTP400(Exception):
    status_code = 400


class LLMRouteConfigTests(unittest.TestCase):
    def test_single_model_config_normalizes_to_one_route(self):
        config = LLMProviderConfig(
            mode="openai-compatible",
            api_key="test-key",
            base_url="https://proxy.example.com/",
            model="claude-5-opus-aws",
        )

        routes = config.get_route_configs()

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].id, "claude-5-opus-aws")
        self.assertEqual(routes[0].model, "claude-5-opus-aws")
        self.assertEqual(routes[0].mode, "openai-compatible")
        self.assertEqual(routes[0].api_key, "test-key")
        self.assertEqual(routes[0].base_url, "https://proxy.example.com")

    def test_multi_route_config_inherits_root_fields(self):
        config = LLMProviderConfig(
            mode="openai-compatible",
            api_key="root-key",
            base_url="https://proxy.example.com",
            model="claude-5-opus-aws",
            routes=[
                LLMRouteConfig(id="aws", model="claude-5-opus-aws"),
                LLMRouteConfig(id="gcp", model="claude-5-opus-gcp"),
                LLMRouteConfig(id="anthropic", model="claude-5-opus-anthropic"),
            ],
        )

        routes = config.get_route_configs()

        self.assertEqual([route.id for route in routes], ["aws", "gcp", "anthropic"])
        self.assertEqual([route.mode for route in routes], ["openai-compatible"] * 3)
        self.assertEqual([route.api_key for route in routes], ["root-key"] * 3)
        self.assertEqual(
            [route.model for route in routes],
            [
                "claude-5-opus-aws",
                "claude-5-opus-gcp",
                "claude-5-opus-anthropic",
            ],
        )

    def test_empty_routes_fail_clearly(self):
        with self.assertRaises(ValidationError) as error:
            LLMProviderConfig(api_key="test-key", routes=[])

        self.assertIn("llm.routes must not be empty", str(error.exception))

    def test_prior_generation_opus_aliases_still_use_adaptive_thinking(self):
        """Opus 4.7/4.8 keep the adaptive contract after the Opus 5 switch.

        These aliases are no longer the configured routes, but the pipeline
        must still drive them correctly on rollback.
        """
        for model in (
            "claude-4.8-opus-aws",
            "claude-4.8-opus-gcp",
            "claude-4.8-opus-anthropic",
            "claude-opus-4-8",
            "claude-4.7-opus-gcp",
        ):
            with self.subTest(model=model):
                self.assertTrue(_uses_adaptive_thinking(model))

    def test_opus_5_aliases_use_adaptive_thinking(self):
        """Opus 5 names carry no adjacent major.minor digit pair.

        The original regex (r'(\\d+)[-.](\\d+)') found no match in
        "claude-5-opus-gcp" and fell through to the manual-thinking path,
        which the rdsec proxy accepts while silently returning no thinking
        blocks -- a whole report at degraded quality with no error raised.
        """
        for model in (
            "claude-5-opus-aws",
            "claude-5-opus-gcp",
            "claude-5-opus-anthropic",
            "claude-opus-5",
            "claude-5-sonnet-gcp",
            "claude-fable-5",
            "claude-mythos-5",
        ):
            with self.subTest(model=model):
                self.assertTrue(_uses_adaptive_thinking(model))

    def test_context_window_suffix_does_not_break_detection(self):
        """"[1m]" must not be parsed as a "1.m"-style version pair."""
        for model in ("claude-5-opus-gcp[1m]", "claude-5-sonnet-gcp[1m]", "claude-fable-5[1m]"):
            with self.subTest(model=model):
                self.assertTrue(_uses_adaptive_thinking(model))

    def test_legacy_manual_thinking_models_still_detected(self):
        """Opus 4.6 and earlier keep the manual path, incl. dated snapshots."""
        for model in (
            "claude-4.6-opus-aws",
            "claude-opus-4-6",
            "claude-opus-4-6-20251101",
            "claude-4.5-opus",
            "claude-sonnet-4-5",
            "claude-haiku-4-5",
        ):
            with self.subTest(model=model):
                self.assertFalse(_uses_adaptive_thinking(model))

    def test_unknown_model_names_fail_open_to_adaptive(self):
        """Unrecognized names must fail toward adaptive.

        Wrong-adaptive is a loud 400; wrong-manual is silent quality loss.
        """
        for model in ("some-future-model", "mock-llm", "claude-opus-6"):
            with self.subTest(model=model):
                self.assertTrue(_uses_adaptive_thinking(model))

    def test_non_claude_versions_never_read_as_legacy_claude(self):
        """A vendor's own low version number is not a Claude generation.

        2026-09-03: meta/muse-spark-1.3-contributor parsed as "1.3" (< 4.7),
        took the manual-thinking branch, and a whole run was sent with no
        reasoning effort at all (provider default medium) under a 57344
        ceiling -- silently. Non-Claude routes only have effort, so they are
        adaptive whatever their version says.
        """
        for model in (
            "meta/muse-spark-1.3-contributor",
            "meta/muse-spark-1.3",
            "meta/muse-spark-1.2",
            "z-ai/GLM-5.3-Flash",
            "deepseek/deepseek-v4-flash",
            "openai/gpt-5.4-mini",
            "vendor/model-2.0",
        ):
            with self.subTest(model=model):
                self.assertTrue(_uses_adaptive_thinking(model))


class AsyncLLMRouterTests(unittest.TestCase):
    def test_round_robin_rotation(self):
        async def run():
            router = AsyncLLMRouter([
                FakeRouteClient("aws"),
                FakeRouteClient("gcp"),
                FakeRouteClient("anthropic"),
            ])

            providers = []
            for _ in range(6):
                response = await router.call(messages=[{"role": "user", "content": "hi"}])
                providers.append(response.content)

            self.assertEqual(
                providers,
                ["aws", "gcp", "anthropic", "aws", "gcp", "anthropic"],
            )

        asyncio.run(run())

    def test_per_provider_cap_is_applied_from_environment(self):
        async def run():
            previous = os.environ.get("LLM_MAX_CONCURRENT_REQUESTS")
            os.environ["LLM_MAX_CONCURRENT_REQUESTS"] = "3"
            router = None
            try:
                config = LLMProviderConfig(
                    mode="openai-compatible",
                    api_key="test-key",
                    base_url="https://proxy.example.com",
                    model="claude-5-opus-aws",
                    routes=[
                        LLMRouteConfig(id="aws", model="claude-5-opus-aws"),
                        LLMRouteConfig(id="gcp", model="claude-5-opus-gcp"),
                        LLMRouteConfig(id="anthropic", model="claude-5-opus-anthropic"),
                    ],
                )
                router = AsyncLLMRouter.from_config(config)

                self.assertIsInstance(router, AsyncLLMRouter)
                self.assertEqual(
                    [client.max_concurrent_requests for client in router.clients],
                    [3, 3, 3],
                )
                self.assertEqual(router.max_total_concurrent_requests, 9)
            finally:
                if router is not None:
                    await router.close()
                if previous is None:
                    os.environ.pop("LLM_MAX_CONCURRENT_REQUESTS", None)
                else:
                    os.environ["LLM_MAX_CONCURRENT_REQUESTS"] = previous

        asyncio.run(run())

    def test_three_providers_at_cap_eight_allow_twenty_four_total_requests(self):
        async def run():
            config = LLMProviderConfig(
                mode="openai-compatible",
                api_key="test-key",
                base_url="https://proxy.example.com",
                model="claude-5-opus-aws",
                routes=[
                    LLMRouteConfig(
                        id="aws",
                        model="claude-5-opus-aws",
                        max_concurrent_requests=8,
                    ),
                    LLMRouteConfig(
                        id="gcp",
                        model="claude-5-opus-gcp",
                        max_concurrent_requests=8,
                    ),
                    LLMRouteConfig(
                        id="anthropic",
                        model="claude-5-opus-anthropic",
                        max_concurrent_requests=8,
                    ),
                ],
            )
            router = AsyncLLMRouter.from_config(config)
            try:
                self.assertIsInstance(router, AsyncLLMRouter)
                self.assertEqual(
                    [client.max_concurrent_requests for client in router.clients],
                    [8, 8, 8],
                )
                self.assertEqual(router.max_total_concurrent_requests, 24)
            finally:
                await router.close()

        asyncio.run(run())

    def test_retryable_failure_falls_back_to_another_provider(self):
        async def run():
            aws = FakeRouteClient("aws", failures=[httpx.ConnectError("boom")])
            gcp = FakeRouteClient("gcp")
            anthropic = FakeRouteClient("anthropic")
            router = AsyncLLMRouter([aws, gcp, anthropic])

            response = await router.call(messages=[{"role": "user", "content": "hi"}])

            self.assertEqual(response.content, "gcp")
            self.assertEqual(len(aws.calls), 1)
            self.assertEqual(len(gcp.calls), 1)
            self.assertEqual(gcp.calls[0]["routing_context"]["attempt"], 2)
            self.assertEqual(gcp.calls[0]["routing_context"]["fallback_from"], "aws")
            self.assertEqual(gcp.calls[0]["routing_context"]["retry_reason"], "ConnectError")

        asyncio.run(run())

    def test_client_error_does_not_cross_provider_retry(self):
        async def run():
            aws = FakeRouteClient("aws", failures=[HTTP400("bad request")])
            gcp = FakeRouteClient("gcp")
            router = AsyncLLMRouter([aws, gcp])

            with self.assertRaises(HTTP400):
                await router.call(messages=[{"role": "user", "content": "hi"}])

            self.assertEqual(len(aws.calls), 1)
            self.assertEqual(len(gcp.calls), 0)

        asyncio.run(run())

    def test_opus_47_request_uses_top_level_adaptive_thinking(self):
        async def run():
            captured_kwargs = {}
            captured_context = {}
            client = AsyncAnthropicClient(
                api_key="test-key",
                base_url="https://proxy.example.com",
                model="claude-5-opus-aws",
                mode="openai-compatible",
                max_retries=0,
            )

            async def fake_create_message(request_context=None, **kwargs):
                captured_kwargs.update(kwargs)
                captured_context.update(request_context or {})
                return FakeAnthropicResponse()

            client._create_message = fake_create_message
            try:
                await client.call_with_thinking(
                    messages=[{"role": "user", "content": "summarize"}],
                    profile=ThinkingLevel.QUICK,
                    caller="test.adaptive",
                )
            finally:
                await client.close()

            self.assertEqual(
                captured_kwargs["thinking"],
                {"type": "adaptive", "display": "summarized"},
            )
            self.assertNotIn("budget_tokens", captured_kwargs["thinking"])
            self.assertEqual(
                captured_kwargs["extra_body"],
                {"output_config": {"effort": "high"}},
            )
            self.assertNotIn("temperature", captured_kwargs)
            self.assertEqual(captured_context["thinking_type"], "adaptive")
            self.assertNotIn("profile", captured_context)
            self.assertEqual(captured_context["analysis_profile"], "QUICK")
            self.assertEqual(captured_context["adaptive_effort"], "high")
            self.assertEqual(captured_context["response_max_tokens"], 65536)

        asyncio.run(run())

    def test_opus_47_plain_call_uses_top_level_adaptive_thinking(self):
        async def run():
            captured_kwargs = {}
            captured_context = {}
            client = AsyncAnthropicClient(
                api_key="test-key",
                base_url="https://proxy.example.com",
                model="claude-5-opus-gcp",
                mode="openai-compatible",
                max_retries=0,
            )

            async def fake_create_message(request_context=None, **kwargs):
                captured_kwargs.update(kwargs)
                captured_context.update(request_context or {})
                return FakeAnthropicResponse()

            client._create_message = fake_create_message
            try:
                await client.call(
                    messages=[{"role": "user", "content": "classify"}],
                    caller="test.plain",
                )
            finally:
                await client.close()

            self.assertEqual(
                captured_kwargs["thinking"],
                {"type": "adaptive", "display": "summarized"},
            )
            self.assertNotIn("budget_tokens", captured_kwargs["thinking"])
            self.assertEqual(
                captured_kwargs["extra_body"],
                {"output_config": {"effort": "high"}},
            )
            self.assertNotIn("temperature", captured_kwargs)
            self.assertEqual(captured_context["kind"], "adaptive_message")
            self.assertEqual(captured_context["thinking_type"], "adaptive")
            self.assertNotIn("profile", captured_context)
            self.assertEqual(captured_context["analysis_profile"], "plain")
            self.assertEqual(captured_context["adaptive_effort"], "high")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
