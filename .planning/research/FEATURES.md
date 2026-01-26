# Feature Landscape: Multi-Provider LLM Configuration

**Domain:** LLM API configuration for open-source Python pipeline
**Researched:** 2026-01-24
**Confidence:** HIGH (verified against official SDK docs and real-world patterns)

## Context

The AI News Aggregator currently uses a hardcoded RDSec proxy (internal TrendAI infrastructure). For open-source release, users need to configure their own LLM providers. The goal is NOT to add abstraction layers like LiteLLM as a dependency, but to make the existing code work with multiple connection modes through configuration.

**Critical Constraint:** The pipeline requires Anthropic's extended thinking feature, which is NOT available through OpenAI-compatible APIs. This means the Anthropic SDK must remain the primary integration for text analysis.

## Table Stakes

Features users expect. Missing = product feels incomplete or unusable.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Native Anthropic API support** | Standard way to use Claude; RDSec proxy is internal | Low | Change from Bearer token to x-api-key header auth |
| **Environment variable configuration** | Industry standard, 12-factor app compatible | Low | Already partially implemented; formalize with validation |
| **Model name configuration** | Different deployments use different model identifiers | Low | Already have ANTHROPIC_MODEL env var |
| **Base URL configuration** | Users may have enterprise endpoints or proxies | Low | Already have ANTHROPIC_API_BASE env var |
| **Graceful skip for missing providers** | Hero images require Gemini; shouldn't block pipeline | Low | Skip hero generation if Gemini not configured |
| **Clear error messages** | Users need to know what's misconfigured | Low | Validate config at startup, not at first API call |
| **Example configuration file** | Users need starting point for setup | Low | config.yaml.example with templates |
| **Documentation of model requirements** | Users need to know Opus 4.5 is required for extended thinking | Low | README section on model compatibility |

## Differentiators

Features that add value but are not expected. Nice-to-have for better UX.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **YAML config with env var fallbacks** | Cleaner than pure env vars for complex configs; supports provider templates | Medium | Use `os.environ/VAR_NAME` syntax like LiteLLM |
| **Provider presets/templates** | Easier onboarding: "anthropic-direct", "openai-compatible", "bedrock" | Medium | Pre-fill auth header type, endpoint patterns |
| **Separate Gemini API configuration** | Hero image provider independent of text analysis provider | Medium | Some users may have Gemini but not Anthropic |
| **Connection mode selector** | Explicit choice: `auth_type: x-api-key` vs `auth_type: bearer` | Low | Clearer than inferring from endpoint URL |
| **Timeout configuration** | Long-running analysis may need longer timeouts | Low | Already have 300s default; make configurable |
| **Retry configuration** | Network flakiness handling | Medium | Exponential backoff with configurable attempts |
| **Dry-run validation** | Test config without running full pipeline | Medium | `--validate-config` flag that tests API connectivity |
| **Cost tracking output** | Show estimated API costs per run | Low | Already implemented; ensure it works across providers |
| **Per-phase model override** | Use cheaper model for low-stakes phases | High | ULTRATHINK vs QUICK phases have different needs |

## Anti-Features

Features to explicitly NOT build. Common mistakes or complexity traps.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **LiteLLM as dependency** | Adds complexity; we already have clean Anthropic SDK integration | Support OpenAI-compatible endpoints directly via optional httpx wrapper |
| **Provider abstraction layer** | Extended thinking is Anthropic-specific; can't abstract it away | Keep Anthropic SDK as primary; Gemini as separate optional integration |
| **Multiple LLM provider fallback** | Different providers behave differently; can't silently substitute | Fail clearly; let user configure ONE provider |
| **Auto-detection of provider from URL** | Magic behavior confuses users | Explicit `provider` or `auth_type` field |
| **Load balancing multiple API keys** | Over-engineering for a batch pipeline that runs once daily | Single API key per provider; rotate manually if needed |
| **Database-backed configuration** | File-based is simpler and fits the static-site architecture | YAML file + env vars only |
| **Runtime config reloading** | Pipeline is batch, not long-running service | Restart pipeline to reload config |
| **Custom authentication plugins** | Endless edge cases; hard to maintain | Support 3 auth types: x-api-key, Bearer, none |
| **Model capability detection** | Extended thinking support varies by model version; hard to detect reliably | Document supported models; fail early if wrong model |
| **Streaming configuration** | Batch pipeline doesn't benefit from streaming | Keep non-streaming for simplicity |

## Feature Dependencies

```
[Env var config] ─────┐
                      ├──> [YAML config loader] ──> [Provider client init]
[YAML config file] ───┘

[Auth type config] ──> [Anthropic client] ──> [Extended thinking calls]

[Gemini config] ──> [Hero generator] ──> [Optional skip if unconfigured]

[Validation] ──> [Early fail] ──> [Clear error message]
```

**Key dependency:** Extended thinking REQUIRES the native Anthropic SDK. OpenAI-compatible mode cannot support extended thinking, so it's only useful if the user has an OpenAI-compatible proxy that routes to Anthropic AND preserves extended thinking.

## MVP Recommendation

For MVP (open-source release), prioritize:

1. **Native Anthropic API support** (table stakes) - Switch from Bearer to x-api-key auth
2. **Graceful skip for Gemini** (table stakes) - Don't block on hero image if not configured
3. **Example configuration** (table stakes) - config.yaml.example with working templates
4. **Clear error messages** (table stakes) - Validate at startup
5. **Connection mode selector** (differentiator) - Explicit auth_type field

### Defer to Post-MVP

- **Per-phase model override** - Adds significant complexity; most users use one model
- **Retry configuration** - Current defaults work; can add later based on feedback
- **Dry-run validation** - Nice to have but not critical for first release
- **Provider presets** - Can document common patterns in config.yaml.example instead

## Configuration Schema Recommendation

Based on research, recommend this structure:

```yaml
# config.yaml
providers:
  anthropic:
    enabled: true
    auth_type: x-api-key          # or "bearer" for proxy endpoints
    base_url: https://api.anthropic.com  # or proxy URL
    api_key: ${ANTHROPIC_API_KEY}  # env var reference
    model: claude-opus-4-5-20251101
    timeout: 300

  gemini:
    enabled: false                 # Set true to enable hero images
    api_key: ${GEMINI_API_KEY}
    model: gemini-3-pro-image      # or "gemini-2.5-flash-image"

pipeline:
  extended_thinking:
    quick: 4096
    standard: 8192
    deep: 16000
    ultrathink: 32000
```

**Rationale:**
- Explicit `enabled` field for optional providers
- `auth_type` removes magic/guessing
- Environment variable syntax (`${VAR}` or `os.environ/VAR`) keeps secrets out of config
- Separate sections for different providers (not trying to unify them)
- Extended thinking budgets exposed for advanced users

## Auth Type Matrix

| Provider | Auth Type | Header | Use Case |
|----------|-----------|--------|----------|
| Anthropic Direct | x-api-key | `x-api-key: <key>` | Standard Anthropic API |
| Anthropic via Proxy | bearer | `Authorization: Bearer <key>` | LiteLLM, enterprise proxies |
| Gemini Direct | api-key | Via SDK (not header) | Standard Gemini API |
| OpenAI-compatible | bearer | `Authorization: Bearer <key>` | User's own LiteLLM proxy |

## Sources

**HIGH Confidence (Official Docs):**
- [Anthropic Python SDK README](https://github.com/anthropics/anthropic-sdk-python/blob/main/README.md) - Custom HTTP client, base_url config
- [Google GenAI Python SDK](https://github.com/googleapis/python-genai) - Client initialization, image generation
- [LiteLLM Anthropic Provider](https://docs.litellm.ai/docs/providers/anthropic) - Extended thinking support in proxy mode
- [LiteLLM Proxy Config](https://docs.litellm.ai/docs/proxy/configs) - YAML config patterns

**MEDIUM Confidence (Community Best Practices):**
- [Env vars vs config files](https://medium.com/israeli-tech-radar/favor-config-files-over-env-vars-d9189d53c4b8) - Hybrid approach recommendation
- [LLM project configuration patterns](https://cismography.medium.com/structuring-projects-and-configuration-management-for-llm-powered-apps-3c8fc6e0cc93) - YAML + env var hybrid
- [LiteLLM load balancing](https://docs.litellm.ai/docs/proxy/load_balancing) - Fallback patterns (for understanding, NOT implementing)

## Implications for Roadmap

Based on this research, the configuration feature should be structured as:

1. **Phase 1: Auth Refactor** - Modify `llm_client.py` to support both x-api-key and Bearer auth modes
2. **Phase 2: Config Schema** - Create YAML config loader with env var substitution
3. **Phase 3: Optional Gemini** - Make hero generator check for Gemini config, skip gracefully if missing
4. **Phase 4: Documentation** - config.yaml.example, README updates, model requirements

**Risk flag:** Extended thinking is Anthropic-only. Any user who configures an OpenAI-compatible endpoint expecting full functionality will be disappointed. Documentation must be crystal clear that extended thinking requires native Anthropic API access.

---
*Research conducted: 2026-01-24*
