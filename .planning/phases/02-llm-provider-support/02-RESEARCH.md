# Phase 2: LLM Provider Support - Research

**Researched:** 2026-01-24
**Domain:** LLM API authentication, extended thinking, proxy compatibility
**Confidence:** HIGH

## Summary

This phase enables users to connect to LLM providers either directly (Anthropic API with `x-api-key` header) or through OpenAI-compatible proxies (LiteLLM, etc. with Bearer token). The current codebase already uses Bearer token authentication via a custom httpx transport. The key changes are:

1. Adding mode-based authentication switching (x-api-key vs Bearer)
2. Validating extended thinking support by mode
3. Updating the client factory to respect mode configuration

The Anthropic SDK natively supports custom http_client injection, making auth switching straightforward. Extended thinking is fully supported through LiteLLM's Anthropic passthrough mode, but NOT through OpenAI-compatible chat/completions endpoints (thinking blocks are stripped).

**Primary recommendation:** Modify `AnthropicClient` to accept a `mode` parameter that switches between `x-api-key` header auth (direct Anthropic) and Bearer token auth (proxy). For extended thinking, fail clearly if proxy mode doesn't return thinking blocks - quality depends on it.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | 0.50+ | Anthropic SDK | Official SDK, native extended thinking support |
| httpx | 0.27+ | HTTP client | Used by Anthropic SDK, supports custom auth |
| pydantic | 2.x | Config validation | Already in use for schema validation |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None additional | - | - | All dependencies already in project |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Custom httpx auth | default_headers param | Less clean but works; custom auth allows dynamic behavior |
| Mode in client | Separate client classes | More code duplication, harder to maintain |

**Installation:**
```bash
# No new dependencies needed - all already installed
pip install anthropic httpx pydantic  # Already present
```

## Architecture Patterns

### Recommended Project Structure
```
agents/
├── config/
│   └── schema.py          # LLMProviderConfig with mode field (DONE)
├── llm_client.py          # Modify to support mode-based auth switching
└── ...
```

### Pattern 1: Mode-Based Authentication Switching

**What:** Single client class that switches auth mechanism based on mode
**When to use:** When the same client needs to work with different auth schemes
**Example:**
```python
# Source: Anthropic SDK docs + httpx auth patterns
import httpx
from anthropic import Anthropic

class ApiKeyAuth(httpx.Auth):
    """Auth handler for x-api-key header (direct Anthropic)."""
    def __init__(self, api_key: str):
        self.api_key = api_key

    def auth_flow(self, request: httpx.Request):
        request.headers["x-api-key"] = self.api_key
        yield request


class BearerAuth(httpx.Auth):
    """Auth handler for Bearer token (OpenAI-compatible proxies)."""
    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def create_http_client(mode: str, api_key: str, timeout: float) -> httpx.Client:
    """Create httpx client with mode-appropriate auth."""
    if mode == "anthropic":
        auth = ApiKeyAuth(api_key)
    else:  # openai-compatible
        auth = BearerAuth(api_key)

    return httpx.Client(auth=auth, timeout=httpx.Timeout(timeout))
```

### Pattern 2: Factory Method with Config

**What:** `from_config()` class method that builds client from configuration
**When to use:** When client initialization depends on validated config
**Example:**
```python
# Source: Current codebase pattern (already implemented in 01-02)
@classmethod
def from_config(cls, config: 'LLMProviderConfig') -> 'AnthropicClient':
    """Create client from configuration with mode-based auth."""
    return cls(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout=config.timeout,
        mode=config.mode  # NEW: pass mode to constructor
    )
```

### Pattern 3: Extended Thinking Validation

**What:** Check for thinking blocks in response and fail if expected but missing
**When to use:** When extended thinking is essential for quality (this pipeline)
**Example:**
```python
# Validate extended thinking response
def validate_thinking_response(response, budget_tokens: int, mode: str):
    """Ensure thinking blocks are present when expected."""
    has_thinking = any(
        block.type == "thinking"
        for block in response.content
    )

    if budget_tokens > 0 and not has_thinking:
        if mode == "openai-compatible":
            raise RuntimeError(
                f"Extended thinking requested (budget={budget_tokens}) but no thinking "
                f"blocks returned. Your proxy may not support Anthropic passthrough mode. "
                f"See: https://docs.litellm.ai/docs/pass_through/anthropic_completion"
            )
        else:
            raise RuntimeError(
                f"Extended thinking requested but no thinking blocks returned. "
                f"Check model compatibility and API response."
            )
```

### Anti-Patterns to Avoid

- **Inferring mode from endpoint URL:** Trust explicit mode config, not URL patterns
- **Degrading to no-thinking mode:** Extended thinking is essential - fail clearly instead
- **Validating API key format:** Different providers use different formats - let API reject bad keys
- **Adding /v1 automatically:** Users may have proxies with different path structures

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP auth | Manual header injection | httpx.Auth subclass | Cleaner, works with all requests automatically |
| API key validation | Regex patterns | Let API reject | Too many valid formats across providers |
| Endpoint normalization | Auto-append /v1 | Trust user config | Proxy paths vary, user knows their setup |

**Key insight:** The Anthropic SDK already handles most complexity. Focus on auth switching and validation, not reimplementing API behavior.

## Common Pitfalls

### Pitfall 1: OpenAI-Compatible Endpoints Don't Return Thinking Blocks

**What goes wrong:** Extended thinking is requested but response only contains text blocks
**Why it happens:** OpenAI chat/completions format doesn't have a thinking block type - even Anthropic's own compatibility layer strips thinking
**How to avoid:** Use Anthropic passthrough mode (LiteLLM `/anthropic/v1/messages`) or direct Anthropic API
**Warning signs:** Response has no thinking blocks despite `budget_tokens > 0`

From official Anthropic docs (Context7):
> You can enable extended thinking capabilities by adding the thinking parameter. While this will improve Claude's reasoning for complex tasks, the OpenAI SDK won't return Claude's detailed thought process.

### Pitfall 2: Wrong Endpoint Path for LiteLLM Passthrough

**What goes wrong:** Extended thinking doesn't work even with LiteLLM
**Why it happens:** Using `/v1/chat/completions` instead of `/anthropic/v1/messages`
**How to avoid:** For extended thinking with LiteLLM, use passthrough endpoint: `http://proxy:4000/anthropic` (no /v1)
**Warning signs:** LiteLLM logs show request going to chat completions

From LiteLLM docs (Context7):
> Base URL Replacement: `https://api.anthropic.com` -> `http://0.0.0.0:4000/anthropic`

### Pitfall 3: Temperature Must Be 1.0 for Extended Thinking

**What goes wrong:** API error when using extended thinking
**Why it happens:** Extended thinking requires `temperature=1.0` (Anthropic constraint)
**How to avoid:** Force temperature to 1.0 when thinking is enabled (current code already does this)
**Warning signs:** API error mentioning temperature incompatibility

From Anthropic docs (Context7):
> Thinking is not compatible with `temperature` or `top_k` modifications

### Pitfall 4: Missing `anthropic-version` Header

**What goes wrong:** API errors or unexpected behavior with direct Anthropic API
**Why it happens:** Anthropic API requires version header
**How to avoid:** SDK adds this automatically; for raw requests use `anthropic-version: 2023-06-01`
**Warning signs:** 400 errors mentioning version

## Code Examples

Verified patterns from official sources:

### Direct Anthropic API Call with x-api-key
```bash
# Source: https://platform.claude.com/docs/en/api
curl https://api.anthropic.com/v1/messages \
  --header "x-api-key: $ANTHROPIC_API_KEY" \
  --header "anthropic-version: 2023-06-01" \
  --header "content-type: application/json" \
  --data '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### LiteLLM Proxy with Bearer Token + Extended Thinking
```bash
# Source: https://docs.litellm.ai/docs/pass_through/anthropic_completion
curl http://0.0.0.0:4000/anthropic/v1/messages \
  --header "Authorization: Bearer $LITELLM_KEY" \
  --header "content-type: application/json" \
  --data '{
    "model": "claude-3-7-sonnet-20250219",
    "max_tokens": 16000,
    "thinking": {"type": "enabled", "budget_tokens": 10000},
    "messages": [{"role": "user", "content": "Complex question..."}]
  }'
```

### Python SDK with Custom Base URL (Passthrough)
```python
# Source: https://github.com/berriai/litellm (Context7)
from anthropic import Anthropic

client = Anthropic(
    base_url="http://0.0.0.0:4000/anthropic",  # LiteLLM passthrough
    api_key="sk-anything"  # Proxy key
)

response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 10000},
    messages=[{"role": "user", "content": "..."}]
)
```

### Async Client with Custom Auth
```python
# Source: Anthropic SDK + httpx patterns
import httpx
from anthropic import AsyncAnthropic

async_http_client = httpx.AsyncClient(
    auth=ApiKeyAuth(api_key),  # or BearerAuth for proxy
    timeout=httpx.Timeout(300.0)
)

client = AsyncAnthropic(
    base_url="https://api.anthropic.com",
    api_key="dummy",  # Overridden by custom auth
    http_client=async_http_client
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Only Bearer auth | Mode-based auth switching | Phase 2 | Supports direct Anthropic users |
| Warn on no thinking | Fail on no thinking | Phase 2 | Quality preserved, clear error for users |
| Infer mode from URL | Explicit mode field | Phase 1 config | Clearer, more reliable |

**Deprecated/outdated:**
- None in this domain - extended thinking is current (2024+)

## Open Questions

Things that couldn't be fully resolved:

1. **Proxy-specific extended thinking behavior**
   - What we know: LiteLLM passthrough works, OpenAI-compat doesn't return thinking
   - What's unclear: Other proxies (AWS Bedrock proxy, custom proxies) behavior
   - Recommendation: Default to failing if thinking blocks missing; let users report specific proxy issues

2. **API key format warnings**
   - What we know: User requested warning if key format doesn't match mode
   - What's unclear: What patterns to check (sk-ant-* for Anthropic? Various proxy formats?)
   - Recommendation: Per CONTEXT.md decision, warn but proceed. Use simple heuristics (sk-ant- for anthropic mode)

## Sources

### Primary (HIGH confidence)
- `/anthropics/anthropic-sdk-python` (Context7) - SDK authentication, http_client configuration
- `/websites/platform_claude_en` (Context7) - Extended thinking API, x-api-key header format
- `/berriai/litellm` (Context7) - Proxy passthrough mode, thinking parameter support
- https://platform.claude.com/docs/en/api/openai-sdk (WebFetch) - OpenAI SDK compatibility limitations

### Secondary (MEDIUM confidence)
- `/websites/litellm_ai` (Context7) - Additional LiteLLM documentation

### Tertiary (LOW confidence)
- WebSearch results for proxy compatibility - Various community implementations

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Using existing Anthropic SDK, documented patterns
- Architecture: HIGH - Patterns verified with Context7 and official docs
- Pitfalls: HIGH - Explicitly documented in official sources

**Research date:** 2026-01-24
**Valid until:** 2026-02-24 (30 days - stable domain)
