# Phase 2: LLM Provider Support - Context

**Gathered:** 2026-01-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Enable users to connect to LLM providers directly (Anthropic API) or through OpenAI-compatible proxies (LiteLLM, etc.) with proper authentication. Extended thinking must work in both modes. This phase handles LLM connectivity only — image provider support is Phase 3.

</domain>

<decisions>
## Implementation Decisions

### Authentication Flow
- Anthropic mode: Always use `x-api-key` header (Anthropic SDK standard, not configurable)
- OpenAI-compatible mode: Always use Bearer token (`Authorization: Bearer <key>`)
- Warn at startup if API key format doesn't match expected pattern for the mode (but proceed anyway)
- Fail at startup if API key is missing entirely — clear error before any work begins

### Extended Thinking Handling
- LiteLLM proxies support extended thinking when endpoint is correct (no `/v1` suffix)
- If a proxy doesn't support extended thinking, fail with clear error — ET is essential for quality, don't degrade
- Thinking budget levels (QUICK/STANDARD/DEEP/ULTRATHINK) should be configurable in YAML
- Research phase should investigate other proxy behaviors beyond LiteLLM

### Model Name Mapping
- Single `model` field in config, used as-is for the configured mode
- No default model — require explicit specification (fail if missing)
- No validation of model name format — accept any string, let API reject invalid models
- Example config shows `claude-opus-4-5-20250514` as the standard example (not a list of options)

### Mode Detection & Switching
- Explicit `mode` field in config (not inferred from endpoint)
- Mode values: `anthropic` or `openai-compatible`
- No warning if mode doesn't match endpoint pattern — trust the user
- Endpoint defaults to `https://api.anthropic.com` for anthropic mode
- Endpoint required for openai-compatible mode

### Claude's Discretion
- Exact error message wording
- How to detect extended thinking support failure
- Config schema structure details

</decisions>

<specifics>
## Specific Ideas

- Current production uses LiteLLM proxy — extended thinking works fine there
- The key difference between modes is really just auth header format and endpoint handling
- Users running direct Anthropic shouldn't need to specify endpoint at all

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-llm-provider-support*
*Context gathered: 2026-01-24*
