# Technology Stack: Multi-Provider LLM API Configuration

**Project:** AI News Aggregator - Open Source Release
**Researched:** 2026-01-24
**Research Type:** Stack dimension for multi-provider configuration

## Executive Summary

The codebase currently uses a LiteLLM proxy (RDSec endpoint) with Bearer authentication via custom httpx transport. For open-sourcing, we need a configuration system that supports three connection modes without adding abstraction libraries:

1. **Direct Anthropic API** - Standard `x-api-key` header authentication
2. **OpenAI-compatible proxy** - For users running their own LiteLLM, vLLM, or similar
3. **Direct Google Gemini API** - For hero image generation

**Recommendation:** Use a YAML configuration file (`config/providers.yaml`) with environment variable interpolation for secrets. This approach is validated by LiteLLM's proven pattern and aligns with the existing codebase's YAML usage for `model_releases.yaml`.

## Recommended Configuration Structure

### Provider Configuration File

**File:** `config/providers.yaml`

```yaml
# AI News Aggregator - Provider Configuration
# Copy this file to config/providers.yaml and configure your providers

# =============================================================================
# LLM Provider (for news analysis, summaries, etc.)
# =============================================================================
llm:
  # Connection mode: "anthropic" | "openai_compatible"
  mode: anthropic

  # Direct Anthropic API (mode: anthropic)
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    # Optional: override default base URL (omit for api.anthropic.com)
    # base_url: https://api.anthropic.com
    model: claude-sonnet-4-20250514
    # Extended thinking requires Anthropic native API
    extended_thinking: true

  # OpenAI-compatible proxy (mode: openai_compatible)
  # Use for LiteLLM, vLLM, Ollama, or any OpenAI-compatible endpoint
  openai_compatible:
    api_key: ${LLM_API_KEY}
    base_url: ${LLM_BASE_URL}
    model: ${LLM_MODEL:claude-sonnet-4-20250514}
    # Auth style: "bearer" (Authorization: Bearer) or "api_key" (x-api-key header)
    auth_style: bearer
    # Extended thinking may not work through proxies
    extended_thinking: false

# =============================================================================
# Image Generation Provider (for hero images)
# =============================================================================
image:
  # Connection mode: "gemini" | "openai_compatible" | "disabled"
  mode: gemini

  # Direct Google Gemini API (mode: gemini)
  gemini:
    api_key: ${GEMINI_API_KEY}
    model: gemini-2.0-flash-exp

  # OpenAI-compatible image API (mode: openai_compatible)
  # Use for custom image generation endpoints
  openai_compatible:
    api_key: ${IMAGE_API_KEY}
    base_url: ${IMAGE_BASE_URL}
    model: ${IMAGE_MODEL}

# =============================================================================
# RDSec Proxy (Trend Micro internal - legacy/enterprise mode)
# =============================================================================
# This section is for Trend Micro internal use. External users should ignore.
rdsec:
  enabled: false
  api_key: ${RDSEC_API_KEY}
  llm_base_url: https://api.anthropic.com  # RDSec LiteLLM proxy
  llm_model: claude-4.5-opus-aws
  image_endpoint: https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions
  image_model: gemini-3-pro-image
```

### Environment Variables

**File:** `.env.example`

```bash
# =============================================================================
# AI News Aggregator - Environment Variables
# =============================================================================
# Copy to .env and fill in your values. Never commit .env to version control.

# -----------------------------------------------------------------------------
# LLM Provider Configuration
# -----------------------------------------------------------------------------

# Option 1: Direct Anthropic API (recommended for extended thinking)
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Option 2: OpenAI-compatible proxy (LiteLLM, vLLM, Ollama, etc.)
# LLM_API_KEY=your-proxy-api-key
# LLM_BASE_URL=http://localhost:4000/v1
# LLM_MODEL=claude-sonnet-4-20250514

# -----------------------------------------------------------------------------
# Image Generation
# -----------------------------------------------------------------------------

# Option 1: Direct Google Gemini API
GEMINI_API_KEY=your-gemini-api-key

# Option 2: OpenAI-compatible image endpoint
# IMAGE_API_KEY=your-image-api-key
# IMAGE_BASE_URL=http://localhost:8080/v1
# IMAGE_MODEL=dall-e-3

# -----------------------------------------------------------------------------
# Trend Micro Internal (RDSec) - ignore for external use
# -----------------------------------------------------------------------------
# RDSEC_API_KEY=your-rdsec-bearer-token

# -----------------------------------------------------------------------------
# Data Collection (unchanged)
# -----------------------------------------------------------------------------
TWITTERAPI_IO_KEY=your-twitterapi-io-key
COLLECTION_SCHEDULE=0 6 * * *
ENABLE_CRON=false
LOOKBACK_HOURS=24
TZ=America/New_York
```

## Authentication Patterns by Provider

| Provider | Auth Header | Auth Style | SDK Support |
|----------|-------------|------------|-------------|
| Anthropic Direct | `x-api-key: {key}` | Native | `anthropic` SDK |
| OpenAI-compatible | `Authorization: Bearer {key}` | Bearer | `openai` SDK or `httpx` |
| Google Gemini | `x-goog-api-key: {key}` | Native | `google-genai` SDK |
| RDSec Proxy | `Authorization: Bearer {key}` | Bearer | Custom `httpx` transport |

### Implementation Notes

**Direct Anthropic (current SDK behavior):**
```python
# The anthropic SDK handles x-api-key automatically
client = anthropic.Anthropic(api_key=api_key)
```

**OpenAI-compatible with Bearer auth (current custom implementation):**
```python
# Custom httpx auth for Bearer token
class BearerAuth(httpx.Auth):
    def __init__(self, token: str):
        self.token = token
    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request

http_client = httpx.AsyncClient(auth=BearerAuth(api_key))
client = anthropic.AsyncAnthropic(
    base_url=base_url,
    api_key="dummy",  # Overridden by BearerAuth
    http_client=http_client
)
```

**Direct Google Gemini:**
```python
from google import genai
client = genai.Client(api_key=api_key)
# Or via environment variable: GEMINI_API_KEY
```

## Configuration Loading Pattern

### Recommended Implementation

```python
# agents/config.py
import os
import re
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Literal

@dataclass
class LLMConfig:
    mode: Literal["anthropic", "openai_compatible"]
    api_key: str
    base_url: Optional[str]
    model: str
    auth_style: Literal["bearer", "api_key"]
    extended_thinking: bool

@dataclass
class ImageConfig:
    mode: Literal["gemini", "openai_compatible", "disabled"]
    api_key: Optional[str]
    base_url: Optional[str]
    model: Optional[str]

@dataclass
class ProviderConfig:
    llm: LLMConfig
    image: ImageConfig

def load_config(config_dir: Path) -> ProviderConfig:
    """Load provider configuration with environment variable interpolation."""
    config_path = config_dir / "providers.yaml"

    if not config_path.exists():
        # Fall back to environment variables only (backward compatible)
        return _config_from_env()

    with open(config_path) as f:
        raw = f.read()

    # Interpolate ${VAR} and ${VAR:default} patterns
    def replace_env(match):
        var = match.group(1)
        if ':' in var:
            name, default = var.split(':', 1)
            return os.environ.get(name, default)
        return os.environ.get(var, '')

    interpolated = re.sub(r'\$\{([^}]+)\}', replace_env, raw)
    config = yaml.safe_load(interpolated)

    return _parse_config(config)
```

## Extended Thinking Considerations

**Critical constraint:** Extended thinking is an Anthropic-specific feature that requires the native Anthropic API format. It will NOT work through:
- Generic OpenAI-compatible proxies
- LiteLLM (unless specifically configured for Anthropic passthrough)
- Any proxy that normalizes to OpenAI format

**Recommendation:**
- For `mode: anthropic` - Enable extended thinking (default)
- For `mode: openai_compatible` - Disable extended thinking by default, document the limitation
- Provide clear error message if user enables extended thinking with incompatible mode

```python
if config.llm.extended_thinking and config.llm.mode != "anthropic":
    logger.warning(
        "Extended thinking requires direct Anthropic API. "
        "Disabling extended thinking for openai_compatible mode."
    )
    config.llm.extended_thinking = False
```

## Migration Path from Current Code

### Current State (RDSec-specific)
```python
# Current: Hardcoded RDSec assumptions
self.api_key = os.environ.get('ANTHROPIC_API_KEY')
self.base_url = os.environ.get('ANTHROPIC_API_BASE')
# Always uses Bearer auth via custom httpx transport
```

### Target State (Multi-provider)
```python
# Target: Config-driven provider selection
config = load_config(config_dir)

if config.llm.mode == "anthropic":
    # Direct Anthropic - use SDK natively
    client = anthropic.AsyncAnthropic(api_key=config.llm.api_key)
elif config.llm.mode == "openai_compatible":
    if config.llm.auth_style == "bearer":
        # Bearer auth - use custom transport (current pattern)
        client = _create_bearer_client(config)
    else:
        # API key auth - use SDK with base_url
        client = anthropic.AsyncAnthropic(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url
        )
```

## Backward Compatibility

To maintain backward compatibility with existing RDSec deployments:

1. **No `providers.yaml`** - Fall back to environment variables with current behavior
2. **`ANTHROPIC_API_BASE` set** - Assume OpenAI-compatible mode with Bearer auth
3. **Only `ANTHROPIC_API_KEY` set** - Assume direct Anthropic mode

```python
def _config_from_env() -> ProviderConfig:
    """Backward-compatible config from environment variables."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    base_url = os.environ.get('ANTHROPIC_API_BASE')
    model = os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')

    if base_url:
        # Legacy RDSec mode - OpenAI compatible with Bearer
        mode = "openai_compatible"
        auth_style = "bearer"
        extended_thinking = True  # RDSec supports it
    else:
        # Direct Anthropic
        mode = "anthropic"
        auth_style = "api_key"
        extended_thinking = True

    return ProviderConfig(
        llm=LLMConfig(
            mode=mode,
            api_key=api_key,
            base_url=base_url,
            model=model,
            auth_style=auth_style,
            extended_thinking=extended_thinking
        ),
        image=_image_config_from_env()
    )
```

## Dependencies

### Current Dependencies (no changes needed)
| Package | Version | Purpose |
|---------|---------|---------|
| `anthropic` | >=0.40.0 | Claude API client |
| `httpx` | >=0.27.0 | Custom HTTP transport for Bearer auth |
| `PyYAML` | >=6.0 | Configuration file parsing |
| `python-dotenv` | >=1.0.0 | Environment variable loading |

### Optional Dependencies (for specific providers)
| Package | Version | Purpose | When Needed |
|---------|---------|---------|-------------|
| `google-genai` | >=1.0.0 | Direct Gemini API | `image.mode: gemini` |
| `openai` | >=1.0.0 | OpenAI-compatible endpoints | If preferring OpenAI SDK over httpx |

**Recommendation:** Add `google-genai` as optional dependency for direct Gemini support:
```
# requirements.txt
google-genai>=1.0.0  # Optional: for direct Gemini image generation
```

## Alternatives Considered

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **YAML config file** | Declarative, supports complex config, aligns with existing patterns | Another file to manage | **Selected** |
| **Environment variables only** | Simple, 12-factor compliant | Gets messy with multiple providers | Kept for backward compat |
| **JSON config** | Widely supported | Less readable than YAML, no comments | Rejected |
| **Python config module** | Type-safe | Not user-editable | Rejected |
| **Add LiteLLM as dependency** | Unified interface | Adds complexity, we're already behind a LiteLLM proxy | Rejected |

## Sources

- [Anthropic SDK Python - Custom HTTP Client](https://github.com/anthropics/anthropic-sdk-python) (HIGH confidence - official docs via Context7)
- [LiteLLM Configuration Patterns](https://docs.litellm.ai/docs/proxy/configs) (HIGH confidence - official docs via Context7)
- [Google Gen AI Python SDK](https://github.com/googleapis/python-genai) (HIGH confidence - official docs via Context7)
- [Multi-Provider LLM Orchestration Guide 2026](https://dev.to/ash_dubai/multi-provider-llm-orchestration-in-production-a-2026-guide-1g10) (MEDIUM confidence - community guide)
- [OpenAI Compatibility - Gemini API](https://ai.google.dev/gemini-api/docs/openai) (HIGH confidence - official Google docs)

## Confidence Assessment

| Area | Level | Reason |
|------|-------|--------|
| YAML config pattern | HIGH | Validated by LiteLLM, matches existing codebase patterns |
| Auth header differences | HIGH | Verified via official SDK documentation |
| Extended thinking constraint | HIGH | Known Anthropic-specific feature, verified in SDK |
| Environment variable interpolation | HIGH | Standard pattern, used by LiteLLM and others |
| Backward compatibility approach | HIGH | Based on direct analysis of current codebase |

## Roadmap Implications

### Suggested Implementation Order

1. **Phase 1: Config Infrastructure**
   - Create `ProviderConfig` dataclass and loader
   - Add `providers.yaml.example` template
   - Implement environment variable fallback

2. **Phase 2: LLM Client Refactor**
   - Refactor `AnthropicClient` to use config
   - Add direct Anthropic mode (removes custom httpx for that path)
   - Keep Bearer auth mode for OpenAI-compatible

3. **Phase 3: Image Provider Refactor**
   - Add `google-genai` optional dependency
   - Refactor `HeroGenerator` to support both Gemini modes
   - Add "disabled" mode for users without image generation

4. **Phase 4: Documentation & Testing**
   - Update README with provider configuration guide
   - Add configuration validation on startup
   - Test all three modes end-to-end
