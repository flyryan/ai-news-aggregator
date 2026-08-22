"""Tests for the OpenRouter image client (image mode: "openrouter").

Context / why this exists
-------------------------
The RDSec proxy went away, so hero generation moves to OpenRouter. OpenRouter's
dedicated ``POST /api/v1/images`` endpoint (not chat/completions) is what
natively supports the three things the daily hero needs:

  1. ``aspect_ratio`` ("21:9" hero banners -- chat/completions ignores it),
  2. ``resolution`` tiers ("2K"),
  3. ``input_references`` for image-to-image generation (the skunk mascot).

These tests lock in:

  1. Default endpoint resolves to https://openrouter.ai/api/v1 and generate()
     POSTs to its /images path.
  2. Happy-path parsing: data[0].b64_json -> bytes, media_type honored,
     usage passed through.
  3. Body mapping: prompt/aspect_ratio/resolution/quality; quality omitted
     when unset; no input_references without a reference image.
  4. Reference image becomes an input_references data URL.
  5. Empty data array raises instead of silently returning nothing.
  6. Transient-failure retry semantics are inherited from the shared retry
     helper (same window as OpenAICompatibleClient).
  7. Schema accepts mode="openrouter" with optional endpoint + quality;
     openai-compatible still requires endpoint.

Stdlib-only (unittest + unittest.mock), matching the repo's other tests:

  python3 -m unittest tests.openrouter_image_client_test -v
"""

import asyncio
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# Make the repo root importable when run directly or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generators.image_client import (  # noqa: E402
    DEFAULT_MAX_ATTEMPTS,
    ImageClient,
    OpenRouterImageClient,
)
from agents.config.schema import ImageProviderConfig  # noqa: E402


def _make_client(**kwargs) -> OpenRouterImageClient:
    return OpenRouterImageClient(
        api_key="test-key",
        model="google/gemini-3-pro-image",
        **kwargs,
    )


def _ok_response() -> MagicMock:
    """A successful /api/v1/images response carrying base64 image bytes."""
    png = base64.b64encode(b"fake-png-bytes").decode()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "created": 1755000000,
            "data": [{"b64_json": png, "media_type": "image/png"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 4175,
                "total_tokens": 4275,
                "cost": 0.04,
            },
        }
    )
    return resp


def _client_ctx(post_side_effect):
    """Mock httpx.AsyncClient whose .post applies post_side_effect (list/callable)."""
    ctx = MagicMock()
    instance = MagicMock()
    instance.post = AsyncMock(side_effect=post_side_effect)
    ctx.__aenter__ = AsyncMock(return_value=instance)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, instance


def _generate_with(client, post_side_effect):
    """Run client.generate under mocked httpx/sleep; return (result, instance, sleep)."""
    ctx, instance = _client_ctx(post_side_effect)

    async def run():
        with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
             patch("generators.image_client.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await client.generate("a prompt", aspect_ratio="21:9",
                                           image_size="2K")
        return result, instance, sleep

    result, instance, sleep = asyncio.run(run())
    return result, instance, sleep


class EndpointAndBodyTest(unittest.TestCase):
    def test_default_endpoint_posts_to_images_path(self):
        c = _make_client()
        self.assertEqual(c.endpoint, "https://openrouter.ai/api/v1")

        _, instance, _ = _generate_with(c, [_ok_response()])
        url = instance.post.await_args.args[0]
        self.assertEqual(url, "https://openrouter.ai/api/v1/images")

    def test_explicit_endpoint_honored(self):
        c = _make_client(endpoint="https://openrouter.example/api/v1/")
        self.assertEqual(c.endpoint, "https://openrouter.example/api/v1")

        _, instance, _ = _generate_with(c, [_ok_response()])
        url = instance.post.await_args.args[0]
        self.assertEqual(url, "https://openrouter.example/api/v1/images")

    def test_body_maps_prompt_ratio_resolution_quality(self):
        c = _make_client(quality="high")

        _, instance, _ = _generate_with(c, [_ok_response()])
        body = instance.post.await_args.kwargs["json"]
        headers = instance.post.await_args.kwargs["headers"]

        self.assertEqual(body["model"], "google/gemini-3-pro-image")
        self.assertEqual(body["prompt"], "a prompt")
        self.assertEqual(body["aspect_ratio"], "21:9")
        self.assertEqual(body["resolution"], "2K")
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("input_references", body)
        # Bearer auth, like the rest of the pipeline's proxy traffic.
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_quality_omitted_when_unset(self):
        c = _make_client()

        _, instance, _ = _generate_with(c, [_ok_response()])
        body = instance.post.await_args.kwargs["json"]
        self.assertNotIn("quality", body)

    def test_reference_image_becomes_input_reference_data_url(self):
        skunk = b"skunk-png-bytes"
        expected_b64 = base64.b64encode(skunk).decode()

        _, instance, _ = _generate_with(
            _make_client(), [_ok_response()]
        )
        # Regenerate with a reference image to inspect its body specifically.
        ctx2, instance2 = _client_ctx([_ok_response()])

        async def run():
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx2), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()):
                await _make_client().generate(
                    "a prompt", reference_image=skunk, aspect_ratio="21:9",
                    image_size="2K"
                )

        asyncio.run(run())
        refs = instance2.post.await_args.kwargs["json"]["input_references"]
        self.assertEqual(len(refs), 1)
        url = refs[0]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(url.split(",", 1)[1], expected_b64)


class ResponseParsingTest(unittest.TestCase):
    def test_happy_path_parses_image_media_type_usage(self):
        result, _, _ = _generate_with(_make_client(), [_ok_response()])
        self.assertEqual(result.image_data, b"fake-png-bytes")
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(result.usage["cost"], 0.04)
        self.assertEqual(result.model, "google/gemini-3-pro-image")

    def test_missing_media_type_defaults_to_png(self):
        png = base64.b64encode(b"fake-png-bytes").decode()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"data": [{"b64_json": png}]})
        result, _, _ = _generate_with(_make_client(), [resp])
        self.assertEqual(result.mime_type, "image/png")

    def test_empty_data_raises(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"data": []})
        with self.assertRaises(RuntimeError):
            _generate_with(_make_client(), [resp])

    def test_no_usage_is_none_not_zero(self):
        png = base64.b64encode(b"fake-png-bytes").decode()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"data": [{"b64_json": png}]})
        result, _, _ = _generate_with(_make_client(), [resp])
        self.assertIsNone(result.usage)


class RetryInheritanceTest(unittest.TestCase):
    def test_transient_disconnect_retried_then_success(self):
        disconnect = httpx.RemoteProtocolError(
            "Server disconnected without sending a response."
        )
        result, instance, sleep = _generate_with(
            _make_client(), [disconnect, disconnect, _ok_response()]
        )
        self.assertEqual(result.image_data, b"fake-png-bytes")
        self.assertEqual(instance.post.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    def test_sustained_outage_exhausts_attempts(self):
        disconnect = httpx.RemoteProtocolError("Server disconnected.")
        ctx, instance = _client_ctx([disconnect] * DEFAULT_MAX_ATTEMPTS)

        async def run():
            c = _make_client()
            with patch("generators.image_client.httpx.AsyncClient", return_value=ctx), \
                 patch("generators.image_client.asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(RuntimeError):
                    await c.generate("a prompt")
            return instance

        instance = asyncio.run(run())
        self.assertEqual(instance.post.await_count, DEFAULT_MAX_ATTEMPTS)


class FactoryAndSchemaTest(unittest.TestCase):
    def test_factory_returns_openrouter_client(self):
        config = ImageProviderConfig(mode="openrouter", api_key="k")
        client = ImageClient.from_config(config)
        self.assertIsInstance(client, OpenRouterImageClient)
        self.assertEqual(client.endpoint, "https://openrouter.ai/api/v1")
        self.assertEqual(client.model, config.model)

    def test_schema_accepts_openrouter_mode_and_quality(self):
        config = ImageProviderConfig(
            mode="openrouter", api_key="k", quality="high"
        )
        self.assertEqual(config.quality, "high")

    def test_schema_still_requires_endpoint_for_openai_compatible(self):
        with self.assertRaises(Exception):
            ImageProviderConfig(mode="openai-compatible", api_key="k")


if __name__ == "__main__":
    unittest.main()
