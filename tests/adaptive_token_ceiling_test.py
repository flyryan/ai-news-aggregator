"""The adaptive ceiling must not silently halve the model's output budget.

`adaptive_max_tokens` is min(LLM_ADAPTIVE_MAX_TOKENS, max_output_tokens),
and it -- not providers.yaml's max_output_tokens -- is what bounds a normal
call. So raising max_output_tokens alone changes nothing; the env default
has to move too.

This bit twice. On adaptive Claude, max_tokens is a shared thinking+output
budget, and the 2026-06-20 research summary clipped mid-word at
"...models the **US vs." with the ceiling at 65536. z-ai/GLM-5.3-Flash
(adopted 2026-08-27) has the same shape for a different reason: it is a
reasoning model called through chat/completions, where reasoning tokens
count as completion tokens. Its own limit is 131072, so a 65536 ceiling
handed DEEP/ULTRATHINK calls half the budget the model would allow.

Locks in:
  1. Given the model's real ceiling, the adaptive budget reaches it
     rather than stopping at the old 65536 default.
  2. The min() clamp still protects a proxy that genuinely caps lower --
     the fix must not let a request exceed what the endpoint accepts.
  3. The schema admits 131072 and still rejects beyond it.

Stdlib-only unittest (needs the venv for httpx/pydantic):

  python3 -m unittest tests.adaptive_token_ceiling_test -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agents.llm_client import AsyncAnthropicClient  # noqa: E402

GLM_MAX_COMPLETION_TOKENS = 131072


def _client(max_output_tokens, adaptive_env):
    env = {
        "LLM_ADAPTIVE_MAX_TOKENS": str(adaptive_env),
        "ANTHROPIC_API_KEY": "test-key",
        "ANTHROPIC_API_BASE": "https://openrouter.ai/api",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        return AsyncAnthropicClient(
            api_key="test-key",
            base_url="https://openrouter.ai/api",
            model="z-ai/GLM-5.3-Flash",
            mode="openai-chat",
            max_output_tokens=max_output_tokens,
        )


class AdaptiveCeilingTest(unittest.TestCase):
    def test_adaptive_budget_reaches_the_models_real_limit(self):
        client = _client(GLM_MAX_COMPLETION_TOKENS, GLM_MAX_COMPLETION_TOKENS)
        self.assertEqual(client.adaptive_max_tokens, GLM_MAX_COMPLETION_TOKENS)

    def test_old_default_would_have_halved_it(self):
        # Documents the regression: raising max_output_tokens alone is not
        # enough, because the env default is the binding constraint.
        client = _client(GLM_MAX_COMPLETION_TOKENS, 65536)
        self.assertEqual(client.adaptive_max_tokens, 65536)
        self.assertLess(client.adaptive_max_tokens, client.max_output_tokens)

    def test_clamp_still_respects_a_lower_endpoint_limit(self):
        # A proxy that really caps at 48000 (OpenRouter's Reka endpoint)
        # must never be sent 131072.
        client = _client(48000, GLM_MAX_COMPLETION_TOKENS)
        self.assertEqual(client.adaptive_max_tokens, 48000)


class SchemaBoundTest(unittest.TestCase):
    def test_schema_accepts_the_glm_ceiling_and_rejects_beyond(self):
        from pydantic import ValidationError

        from agents.config.schema import LLMProviderConfig

        base = dict(
            mode="openai-chat",
            api_key="k",
            base_url="https://openrouter.ai/api",
            model="z-ai/GLM-5.3-Flash",
        )
        cfg = LLMProviderConfig(
            max_output_tokens=GLM_MAX_COMPLETION_TOKENS, **base
        )
        self.assertEqual(cfg.max_output_tokens, GLM_MAX_COMPLETION_TOKENS)

        with self.assertRaises(ValidationError):
            LLMProviderConfig(
                max_output_tokens=GLM_MAX_COMPLETION_TOKENS + 1, **base
            )


if __name__ == "__main__":
    unittest.main()
