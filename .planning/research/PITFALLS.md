# Domain Pitfalls: Multi-Connection Mode Support

**Domain:** Internal tool to open-source release with multiple LLM API connection modes
**Researched:** 2026-01-24
**Overall Confidence:** HIGH (verified with official Anthropic docs and SDK)

---

## Critical Pitfalls

Mistakes that cause rewrites or major issues.

---

### Pitfall 1: Authentication Header Mismatch

**What goes wrong:** The Anthropic SDK uses `x-api-key` header by default, but proxy endpoints (like LiteLLM, RDSec) require Bearer token authentication. Users configure "direct Anthropic" but the code sends the wrong auth header, causing 401 errors.

**Why it happens:** The current codebase uses a custom `BearerAuth` class to inject Bearer tokens. When switching to direct Anthropic API, this override must be disabled because direct Anthropic expects `x-api-key` header format.

**Consequences:**
- Silent authentication failures (401 Unauthorized)
- Users assume their API key is invalid
- Debugging is hard because both modes "look like" Anthropic API

**Warning signs:**
- 401 errors when users report valid API keys
- Works with proxy, fails with direct Anthropic
- Response headers show `x-api-key` being rejected

**Prevention:**
```python
# Connection mode should determine auth strategy
if connection_mode == "direct_anthropic":
    # Let SDK handle native x-api-key auth
    client = Anthropic(api_key=key)
elif connection_mode == "openai_compatible":
    # Use Bearer auth for OpenAI-compatible proxies
    http_client = httpx.Client(auth=BearerAuth(key))
    client = Anthropic(http_client=http_client, api_key="dummy")
```

**Which phase should address it:** Phase 1 (Connection Abstraction Layer)

---

### Pitfall 2: Extended Thinking Not Supported by Proxy

**What goes wrong:** Users configure an OpenAI-compatible proxy (their own LiteLLM, OpenRouter, etc.), then extended thinking silently fails or returns empty thinking blocks. The `thinking` parameter is Anthropic-native and not translated by most proxies.

**Why it happens:** Extended thinking requires:
1. `thinking: { type: "enabled", budget_tokens: N }` parameter
2. Model that supports thinking (specific Anthropic models)
3. Proxy that forwards thinking parameter correctly

Most OpenAI-compatible proxies strip unknown parameters or don't support the thinking response format.

**Consequences:**
- Analysis quality degrades silently (no thinking = worse reasoning)
- Users don't know thinking is disabled
- Cost tracking shows normal output tokens but no thinking tokens

**Warning signs:**
- `response.content` has no `thinking` blocks
- Output quality drops compared to direct Anthropic
- Token usage lower than expected for thinking-enabled calls

**Prevention:**
1. Validate connection mode supports extended thinking at startup
2. Log warning when thinking is requested but mode doesn't support it
3. Consider graceful degradation: use higher max_tokens without thinking
4. Document which modes support extended thinking

```python
def validate_thinking_support(connection_mode: str) -> bool:
    """Check if connection mode supports extended thinking."""
    THINKING_SUPPORTED = {"direct_anthropic", "rdsec_proxy"}
    if connection_mode not in THINKING_SUPPORTED:
        logger.warning(
            f"Extended thinking not supported in {connection_mode} mode. "
            "Analysis quality may be reduced."
        )
        return False
    return True
```

**Which phase should address it:** Phase 1 (validation), Phase 2 (graceful degradation)

---

### Pitfall 3: Model Name Translation Failure

**What goes wrong:** Users configure direct Anthropic with the proxy model name (`claude-4.5-opus-aws`) which doesn't exist in the public Anthropic API. The API returns "model not found" errors.

**Why it happens:**
- Internal proxies use custom model names (e.g., `-aws`, `-bedrock` suffixes)
- Direct Anthropic requires exact model IDs like `claude-opus-4-5-20251101`
- No validation catches the mismatch before API call

**Consequences:**
- Immediate failure on first API call
- Users don't know which model names are valid
- Hard to debug because error message may not be clear

**Warning signs:**
- "model not found" or "invalid model" errors
- Model names contain `-aws`, `-gcp`, `-bedrock` suffixes with direct mode
- Works with proxy but fails with direct

**Prevention:**
1. Define model name mapping per connection mode
2. Validate model names at configuration time
3. Provide clear documentation of valid model names per mode

```python
MODEL_MAPPINGS = {
    "direct_anthropic": {
        "claude-opus-4.5": "claude-opus-4-5-20251101",
        "claude-sonnet-4.5": "claude-sonnet-4-5-20250929",
        # ...
    },
    "rdsec_proxy": {
        "claude-opus-4.5": "claude-4.5-opus-aws",
        # ...
    }
}

def resolve_model_name(connection_mode: str, requested_model: str) -> str:
    """Translate user-friendly model name to API-specific name."""
    mapping = MODEL_MAPPINGS.get(connection_mode, {})
    return mapping.get(requested_model, requested_model)
```

**Which phase should address it:** Phase 1 (model resolution), Phase 3 (validation)

---

### Pitfall 4: Breaking Existing Proxy Users

**What goes wrong:** Refactoring for multi-mode support changes default behavior, environment variable names, or configuration structure. Existing proxy users (the primary audience today) find their setup broken after upgrading.

**Why it happens:**
- New config structure replaces old env vars
- Default connection mode set to "direct_anthropic" instead of preserving proxy
- Authentication logic changes affect existing Bearer token flow

**Consequences:**
- Production deployments fail after upgrade
- User trust eroded
- Support burden increases

**Warning signs:**
- Existing `.env` files stop working
- New required config fields with no defaults
- Behavior changes without version bump or migration guide

**Prevention:**
1. **Keep existing env vars working** - add new config as opt-in
2. **Auto-detect connection mode** from existing config when possible
3. **Provide migration guide** documenting changes
4. **Deprecation warnings** before removing old config paths

```python
# Legacy support: if old vars exist, map to new config
if os.environ.get("ANTHROPIC_API_BASE") and not os.environ.get("CONNECTION_MODE"):
    # Existing user - preserve their setup
    if "rdsec" in os.environ["ANTHROPIC_API_BASE"].lower():
        os.environ["CONNECTION_MODE"] = "rdsec_proxy"
    else:
        os.environ["CONNECTION_MODE"] = "openai_compatible"
```

**Which phase should address it:** Phase 3 (validation/migration), but design for it in Phase 1

---

## Moderate Pitfalls

Mistakes that cause delays or technical debt.

---

### Pitfall 5: Secrets Leakage in Open Source Release

**What goes wrong:** Internal endpoints, API keys, or configuration values get committed to the public repo. Even after deletion, secrets remain in git history.

**Why it happens:**
- `.env.example` contains actual values instead of placeholders
- Internal URLs hardcoded in source (not just config)
- Git history not cleaned before public release

**Warning signs:**
- `.env.example` has values that look real (long API keys, specific URLs)
- Hardcoded URLs in Python files (search for `https://`)
- Comments referencing internal systems

**Prevention:**
1. Audit all files for hardcoded values before release
2. Use `git-secrets` or similar pre-commit hooks
3. Review `.env.example` - only placeholder values
4. Search codebase for internal domains (`trendmicro`, `rdsec`, etc.)

```bash
# Pre-release audit
grep -r "trendmicro\|rdsec\|api\.anthropic" --include="*.py" .
grep -E "[a-zA-Z0-9]{32,}" .env.example  # Long strings that might be keys
```

**Which phase should address it:** Phase 4 (Documentation & Release)

---

### Pitfall 6: Google Gemini Auth Differs Entirely

**What goes wrong:** Hero image generation uses Google Gemini API (via RDSec). When supporting direct Gemini, the auth pattern is completely different (Google API key, not Anthropic patterns).

**Why it happens:**
- Gemini uses `GOOGLE_API_KEY` or `GEMINI_API_KEY` env var
- Different SDK (`google-genai` vs `anthropic`)
- Different endpoint structure

**Consequences:**
- Hero generation fails silently or with cryptic errors
- Users confused about which keys go where
- Config validation misses Gemini-specific requirements

**Warning signs:**
- Hero images fail while text analysis works
- "Invalid API key" errors from Google
- Gemini SDK import errors

**Prevention:**
1. Treat Gemini as a separate connection mode/config section
2. Clear documentation distinguishing Anthropic vs Gemini config
3. Validate Gemini config independently at startup

```python
# Separate config sections
anthropic_config = {
    "connection_mode": "direct_anthropic",
    "api_key": os.environ["ANTHROPIC_API_KEY"],
    "base_url": "https://api.anthropic.com"
}

gemini_config = {
    "api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
    # Note: Gemini doesn't use base_url in same way
}
```

**Which phase should address it:** Phase 2 (separate from Anthropic config)

---

### Pitfall 7: Missing Capability Documentation

**What goes wrong:** Users don't know which features work in which connection mode. They configure OpenAI-compatible mode expecting extended thinking, then get degraded results without understanding why.

**Why it happens:**
- Feature matrix not documented
- No runtime warnings about unsupported features
- Assumption that "Anthropic-compatible" means full feature parity

**Consequences:**
- User frustration
- Bug reports for "expected" behavior
- Incorrect usage patterns

**Prevention:**
Create explicit capability matrix in documentation:

| Feature | Direct Anthropic | RDSec Proxy | OpenAI-Compatible |
|---------|------------------|-------------|-------------------|
| Extended Thinking | Yes | Yes | No |
| Streaming | Yes | Yes | Varies |
| Tool Use | Yes | Yes | Varies |
| Prompt Caching | Yes | Varies | No |

**Which phase should address it:** Phase 4 (Documentation)

---

### Pitfall 8: Base URL Trailing Slash Inconsistency

**What goes wrong:** Some users configure `https://api.anthropic.com/` (with trailing slash), others `https://api.anthropic.com` (without). SDK behavior differs, causing path construction bugs like `https://api.anthropic.com//v1/messages`.

**Why it happens:**
- No normalization of user input
- Different examples show different formats
- SDK may or may not handle this gracefully

**Warning signs:**
- 404 errors with double slashes in URL
- Works for some users, not others
- URL in error message shows `//v1/` pattern

**Prevention:**
```python
def normalize_base_url(url: str) -> str:
    """Ensure consistent base URL format."""
    return url.rstrip("/")
```

**Which phase should address it:** Phase 1 (config validation)

---

## Minor Pitfalls

Mistakes that cause annoyance but are fixable.

---

### Pitfall 9: Timeout Differences Across Modes

**What goes wrong:** Extended thinking requests take 60+ seconds. Default timeouts work for proxy (which may have its own timeout handling) but fail for direct API calls.

**Prevention:** Make timeout configurable, document recommended values for thinking-enabled calls (300+ seconds).

**Which phase should address it:** Phase 2 (make configurable)

---

### Pitfall 10: Cost Tracking Model Mismatch

**What goes wrong:** Cost tracker uses hardcoded pricing for specific model names. When model names change (proxy vs direct), cost calculations become wrong.

**Prevention:** Use model family detection or make pricing configurable.

**Which phase should address it:** Phase 2 (after core connection work)

---

### Pitfall 11: Error Message Confusion

**What goes wrong:** Errors from different connection modes have different formats. Users can't tell if the error is from their proxy, the SDK, or the underlying API.

**Prevention:** Wrap errors with connection mode context:
```
ConnectionError: [direct_anthropic] 401 Unauthorized - Check ANTHROPIC_API_KEY
```

**Which phase should address it:** Phase 2 (error handling)

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|----------------|------------|
| Connection Abstraction | Auth header mismatch (#1) | Test each mode against real endpoint |
| Connection Abstraction | Model name translation (#3) | Build validation that catches mismatches early |
| Thinking Support | Proxy doesn't support thinking (#2) | Detect and warn, don't fail silently |
| Config Migration | Breaking existing users (#4) | Backward-compatible defaults |
| Gemini Integration | Different auth pattern (#6) | Separate config section |
| Documentation | Missing capability matrix (#7) | Feature table per mode |
| Release Prep | Secrets in repo (#5) | Pre-release audit script |

---

## Sources

- [Anthropic API Authentication](https://docs.anthropic.com/en/api/overview) - Official auth header documentation
- [Anthropic SDK Python](https://github.com/anthropics/anthropic-sdk-python) - Custom HTTP client support
- [Extended Thinking Docs](https://platform.claude.com/docs/en/docs/build-with-claude/extended-thinking) - Model support, API parameters
- [Google Gemini API Key Setup](https://ai.google.dev/gemini-api/docs/api-key) - Gemini auth patterns
- [LiteLLM Anthropic Provider](https://docs.litellm.ai/docs/providers/anthropic) - Proxy translation behavior
- [API Backward Compatibility Best Practices](https://zuplo.com/learning-center/api-versioning-backward-compatibility-best-practices) - Migration patterns
- [Environment Variables and Secrets](https://blog.miguelgrinberg.com/post/how-to-securely-store-secrets-in-environment-variables) - Security best practices
