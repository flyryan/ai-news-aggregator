# Phase 3: Image Provider Support - Context

**Gathered:** 2026-01-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Enable hero image generation via direct Google Gemini API (native mode) or OpenAI-compatible proxies, with graceful skip when image config is missing. Creating posts and interactions are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Mode Behavior
- Native mode uses google-genai SDK (official Python SDK)
- OpenAI-compatible mode uses httpx client (consistent with LLM client pattern, Bearer auth)
- Unified interface: ImageClient.from_config() returns appropriate implementation
- Hero generator doesn't care which mode — unified interface hides the difference

### Model Naming
- Native mode: model is optional, defaults to `gemini-3-pro-image-preview` (our default, not SDK's)
- OpenAI-compatible mode: model is required (no sensible default for proxy endpoints)
- Migration from env vars assumes OpenAI-compatible mode (like LLM migration)

### Graceful Skip Logic
- Skip hero generation if: no image_provider section in YAML OR no API key available
- Log level: WARNING when skipping
- Log message: be specific about why (missing section vs missing key)
- Log message: mention regenerate script as option for later generation
- JSON output: hero_image_url and hero_image_prompt set to null (not omitted) so regenerate script can populate

### Error Handling
- API failures: continue pipeline without hero image (hero is enhancement, not critical)
- Retries: 2-3 retries on transient errors (5xx, timeouts), skip retry on 4xx
- Log level after retries exhausted: WARNING
- Error messages: include mode-specific troubleshooting guidance (like LLM errors)

### Claude's Discretion
- Exact retry backoff strategy
- Timeout values for API calls
- Image output format/quality settings (already decided in Phase 1 config schema)

</decisions>

<specifics>
## Specific Ideas

- Migration treats existing env vars (GEMINI_API_KEY, etc.) as OpenAI-compatible mode, matching LLM behavior
- Regenerate script should work whether hero was skipped or failed

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-image-provider-support*
*Context gathered: 2026-01-25*
