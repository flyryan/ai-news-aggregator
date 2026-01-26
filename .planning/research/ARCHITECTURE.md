# Architecture: Multi-Provider LLM Integration

**Domain:** Multi-provider API configuration for LLM pipeline
**Researched:** 2026-01-24
**Dimension:** Architecture (config structure and client initialization)

## Executive Summary

The codebase currently uses a LiteLLM-style proxy (RDSec endpoint) with Bearer token authentication. To support open-source release, we need to configure the existing Anthropic SDK to work with three connection modes:

1. **Direct Anthropic API** - Standard SDK usage with `x-api-key` header (default Anthropic behavior)
2. **OpenAI-compatible proxy** - Continue using Anthropic SDK pointed at proxy with Bearer auth (current approach)
3. **Direct Google Gemini API** - For hero image generation (separate client)

The good news: The Anthropic SDK already supports all these modes through its initialization parameters. No major refactoring needed - just conditional configuration.

## Recommended Architecture

### Config File Structure

Use a single YAML config file (`config/providers.yaml`) with connection modes and credentials. Environment variables serve as fallbacks for backwards compatibility.

```yaml
# config/providers.yaml

# LLM Provider Configuration
# Supports: anthropic, openai_compatible
llm:
  # Connection mode determines auth and endpoint behavior
  mode: anthropic  # or: openai_compatible

  # Direct Anthropic API (mode: anthropic)
  # Uses x-api-key header authentication (SDK default)
  api_key: ${ANTHROPIC_API_KEY}  # env var interpolation
  model: claude-opus-4-5-20251101

  # OpenAI-compatible proxy (mode: openai_compatible)
  # Uses Bearer token authentication with custom base URL
  # base_url: https://api.rdsec.trendmicro.com/prod/aiendpoint
  # model: claude-4.5-opus-aws  # LiteLLM model naming

# Image Generation Provider Configuration
# Supports: gemini, rdsec, disabled
image:
  mode: disabled  # or: gemini, rdsec

  # Direct Google Gemini API (mode: gemini)
  # api_key: ${GEMINI_API_KEY}
  # model: gemini-3-pro-image-preview

  # RDSec endpoint (mode: rdsec) - internal TrendAI only
  # Uses same api_key as LLM if not specified
  # endpoint: https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions
  # model: gemini-3-pro-image
```

### Provider Templates

Include common configurations as templates in documentation/examples:

```yaml
# Example: Direct Anthropic API (recommended for most users)
llm:
  mode: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-opus-4-5-20251101

image:
  mode: gemini
  api_key: ${GEMINI_API_KEY}
  model: gemini-3-pro-image-preview
```

```yaml
# Example: LiteLLM proxy
llm:
  mode: openai_compatible
  base_url: http://localhost:4000/anthropic
  api_key: sk-litellm-key
  model: claude-opus-4-5-20251101

image:
  mode: disabled  # Or configure Gemini separately
```

```yaml
# Example: RDSec (TrendAI internal)
llm:
  mode: openai_compatible
  base_url: https://api.rdsec.trendmicro.com/prod/aiendpoint
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-4.5-opus-aws

image:
  mode: rdsec
  # Uses same api_key as LLM
```

## Component Boundaries

### Config Loader

New module: `config/provider_config.py`

| Responsibility | Implementation |
|----------------|----------------|
| Load YAML config | `yaml.safe_load()` with env var interpolation |
| Validate config | Pydantic models or dataclasses |
| Fallback to env vars | For backwards compatibility |
| Provide typed config | `ProviderConfig` dataclass |

```python
@dataclass
class LLMConfig:
    mode: Literal["anthropic", "openai_compatible"]
    api_key: str
    model: str
    base_url: Optional[str] = None  # Required for openai_compatible

@dataclass
class ImageConfig:
    mode: Literal["gemini", "rdsec", "disabled"]
    api_key: Optional[str] = None
    model: Optional[str] = None
    endpoint: Optional[str] = None
```

### Anthropic Client Initialization

The existing `AnthropicClient` class needs minimal changes. The Anthropic SDK supports both auth modes:

**Direct Anthropic (mode: anthropic)**
```python
# Standard SDK usage - uses x-api-key header automatically
client = anthropic.Anthropic(
    api_key=config.api_key,  # Sets x-api-key header
    # No base_url = uses https://api.anthropic.com
)
```

**OpenAI-compatible proxy (mode: openai_compatible)**
```python
# Current approach - custom httpx client with Bearer auth
http_client = httpx.Client(
    auth=BearerAuth(config.api_key),
    timeout=httpx.Timeout(timeout)
)

client = anthropic.Anthropic(
    base_url=config.base_url,
    api_key="dummy",  # Overridden by Bearer auth
    http_client=http_client
)
```

**Key insight:** The existing `BearerAuth` custom auth handler only needs to be used when `mode: openai_compatible`. For direct Anthropic, use standard SDK initialization.

### Gemini Client Options

Three approaches for hero image generation:

**Option 1: Google Gen AI SDK (Recommended)**
```python
from google import genai
from google.genai import types

client = genai.Client(api_key=config.api_key)

response = client.models.generate_content(
    model='gemini-3-pro-image-preview',
    contents=[prompt],
    config=types.GenerateContentConfig(
        response_modalities=['IMAGE']
    )
)
```

**Option 2: Direct REST API (Current RDSec approach)**
```python
# OpenAI-compatible chat completions format
response = requests.post(
    endpoint,
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "model": "gemini-3-pro-image",
        "messages": [...],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "21:9"}
    }
)
```

**Option 3: Hybrid (Best compatibility)**
- Use Google Gen AI SDK when `mode: gemini`
- Use REST API when `mode: rdsec`
- Skip generation when `mode: disabled`

**Recommendation:** Option 3 (Hybrid) - maintains current RDSec compatibility while adding native Gemini support.

## Data Flow

```
┌─────────────────┐
│ config/         │
│ providers.yaml  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ProviderConfig  │  ← Loads config, validates, interpolates env vars
│ (config loader) │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌─────────┐
│  LLM   │ │  Image  │
│ Config │ │ Config  │
└────┬───┘ └────┬────┘
     │          │
     ▼          ▼
┌─────────────────┐  ┌──────────────────┐
│ AnthropicClient │  │  HeroGenerator   │
│ (conditional    │  │  (conditional    │
│  auth mode)     │  │   provider)      │
└─────────────────┘  └──────────────────┘
```

## Patterns to Follow

### Pattern 1: Conditional Client Factory

Create clients based on mode, not hardcoded assumptions.

```python
def create_llm_client(config: LLMConfig) -> anthropic.Anthropic:
    """Factory function that creates the appropriate client based on mode."""

    if config.mode == "anthropic":
        # Standard Anthropic SDK - x-api-key auth
        return anthropic.Anthropic(api_key=config.api_key)

    elif config.mode == "openai_compatible":
        # Custom httpx client with Bearer auth
        http_client = httpx.Client(
            auth=BearerAuth(config.api_key),
            timeout=httpx.Timeout(300.0)
        )
        return anthropic.Anthropic(
            base_url=config.base_url,
            api_key="dummy",
            http_client=http_client
        )
```

### Pattern 2: Graceful Degradation

Handle missing optional features without blocking pipeline.

```python
async def run_phase_4_7_hero_image(self):
    """Generate hero image if configured, skip gracefully if not."""

    if self.image_config.mode == "disabled":
        logger.info("Hero image generation disabled - skipping")
        return None

    try:
        generator = create_image_generator(self.image_config)
        return await generator.generate(...)
    except Exception as e:
        logger.warning(f"Hero image generation failed: {e}")
        return None  # Don't fail the pipeline
```

### Pattern 3: Environment Variable Fallback

Support both config file and env vars for backwards compatibility.

```python
def load_provider_config(config_path: Optional[Path] = None) -> ProviderConfig:
    """Load config from YAML file, falling back to environment variables."""

    if config_path and config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f)
            # Interpolate ${VAR} patterns
            return parse_config(interpolate_env_vars(raw))

    # Fallback to env vars (backwards compatibility)
    return ProviderConfig(
        llm=LLMConfig(
            mode="openai_compatible" if os.environ.get("ANTHROPIC_API_BASE") else "anthropic",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url=os.environ.get("ANTHROPIC_API_BASE"),
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-5-20251101")
        ),
        image=ImageConfig(mode="disabled")  # Default to disabled for OSS
    )
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Hardcoded Endpoints

**What:** Embedding provider-specific URLs directly in code
**Why bad:** Requires code changes to switch providers
**Instead:** Use config-driven endpoint selection

```python
# BAD
ENDPOINT = "https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions"

# GOOD
endpoint = config.image.endpoint or DEFAULTS[config.image.mode]
```

### Anti-Pattern 2: Auth Mode Detection by Heuristics

**What:** Guessing auth mode from URL patterns
**Why bad:** Fragile, breaks with new providers
**Instead:** Explicit mode configuration

```python
# BAD
if "rdsec" in base_url:
    use_bearer_auth()
elif "anthropic" in base_url:
    use_api_key_auth()

# GOOD
if config.mode == "openai_compatible":
    use_bearer_auth()
else:
    use_api_key_auth()
```

### Anti-Pattern 3: Tight SDK Coupling

**What:** Spreading SDK-specific code throughout codebase
**Why bad:** Hard to swap providers, hard to test
**Instead:** Wrapper classes with consistent interface

The current `AnthropicClient` wrapper is a good example - it abstracts the SDK details.

## Implementation Approach

### Minimal Changes to Existing Code

The goal is configuration changes, not architectural rewrites.

**Files to modify:**

| File | Change |
|------|--------|
| `agents/llm_client.py` | Add factory function, conditional auth |
| `generators/hero_generator.py` | Add Gemini SDK option, mode handling |
| `agents/orchestrator.py` | Load config at startup, pass to components |
| `run_pipeline.py` | Add `--config` CLI option |

**New files:**

| File | Purpose |
|------|---------|
| `config/provider_config.py` | Config loader with env var interpolation |
| `config/providers.yaml.example` | Template with all provider options |

### Backwards Compatibility

Existing deployments using env vars should continue working:

```python
# Detection logic
if config_path.exists():
    # Use config file
    config = load_from_yaml(config_path)
elif os.environ.get("ANTHROPIC_API_BASE"):
    # Legacy: OpenAI-compatible mode (RDSec)
    config = legacy_env_config(mode="openai_compatible")
else:
    # New default: Direct Anthropic
    config = legacy_env_config(mode="anthropic")
```

## Scalability Considerations

Not directly applicable for this project (single-instance deployment), but the config pattern supports:

| Concern | Approach |
|---------|----------|
| Multiple providers | Config supports different modes per component |
| Secrets rotation | Env var interpolation allows external secret injection |
| Testing | Easy to mock config for unit tests |

## Sources

**HIGH confidence (official documentation):**
- [Anthropic Python SDK README](https://github.com/anthropics/anthropic-sdk-python) - Client initialization, base_url, http_client customization
- [Google Gen AI Python SDK](https://github.com/googleapis/python-genai) - Client initialization, image generation API

**MEDIUM confidence (verified with official docs):**
- [LiteLLM Anthropic Provider](https://docs.litellm.ai/docs/providers/anthropic) - OpenAI-compatible proxy configuration
- [LiteLLM Anthropic Passthrough](https://docs.litellm.ai/docs/pass_through/anthropic_completion) - Native format through proxy

**Existing codebase (verified):**
- `/Users/ryand/Code/AATF/ai-news-aggregator/agents/llm_client.py` - Current Bearer auth implementation
- `/Users/ryand/Code/AATF/ai-news-aggregator/generators/hero_generator.py` - Current RDSec image generation

## Roadmap Implications

**Phase structure recommendation:**

1. **Config Infrastructure** (first)
   - Create `ProviderConfig` loader
   - Add env var interpolation
   - Validate config schema
   - *Rationale:* Foundation for all other changes

2. **LLM Client Refactor** (second)
   - Factory function for client creation
   - Conditional auth mode
   - Backwards compatibility testing
   - *Rationale:* Core functionality, must work before other phases

3. **Image Provider Refactor** (third)
   - Add Google Gen AI SDK support
   - Graceful skip when disabled
   - Mode switching logic
   - *Rationale:* Optional feature, can be deferred

4. **Documentation & Examples** (last)
   - Provider templates
   - README for external audience
   - .env.example updates
   - *Rationale:* Depends on implementation being stable

**Research flags:**
- Phase 2: May need research on Anthropic SDK behavior with non-standard base URLs
- Phase 3: Google Gen AI SDK image generation is relatively new, verify API stability

---

*Architecture research complete. Ready for roadmap creation.*
