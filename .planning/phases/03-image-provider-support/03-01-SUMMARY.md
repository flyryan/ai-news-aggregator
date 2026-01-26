---
phase: "03"
plan: "01"
subsystem: image-generation
tags: ["google-genai", "image-api", "factory-pattern", "async"]

dependency-graph:
  requires:
    - "01: Configuration infrastructure"
    - "02: LLM provider patterns"
  provides:
    - "BaseImageClient ABC for image generation"
    - "NativeGeminiClient using google-genai SDK"
    - "OpenAICompatibleClient using REST format"
    - "ImageClient factory method"
  affects:
    - "03-02: HeroGenerator refactoring"

tech-stack:
  added:
    - "google-genai>=1.0.0 (official Google SDK)"
  patterns:
    - "Factory pattern with from_config() method"
    - "TYPE_CHECKING import for forward references"
    - "Mode-specific error handling with troubleshooting"
    - "Async-first API design"

key-files:
  created:
    - "generators/image_client.py"
  modified:
    - "requirements.txt"

decisions:
  - id: "03-01-01"
    choice: "Use google-genai (not google-generativeai)"
    reason: "google-genai is the new official SDK, google-generativeai is deprecated"
  - id: "03-01-02"
    choice: "SDK timeout in milliseconds"
    reason: "google-genai HttpOptions uses milliseconds, convert from seconds in constructor"
  - id: "03-01-03"
    choice: "Explicit api_key parameter"
    reason: "Don't rely on env vars in SDK - pass key explicitly to genai.Client()"
  - id: "03-01-04"
    choice: "Mode-specific error messages"
    reason: "Include troubleshooting guidance specific to native vs openai-compatible mode"

metrics:
  duration: "1 min"
  completed: "2026-01-25"
---

# Phase 3 Plan 1: Image Client Abstraction Summary

**One-liner:** Unified image client abstraction with NativeGeminiClient (google-genai SDK) and OpenAICompatibleClient (REST) implementations following LLM client factory pattern

## What Was Built

Created `generators/image_client.py` with a unified abstraction layer for image generation:

1. **ImageResponse dataclass** - Standardized response with `image_data` bytes and `mime_type`

2. **BaseImageClient ABC** - Abstract interface with `generate(prompt, reference_image, aspect_ratio, image_size)` method

3. **NativeGeminiClient** - Direct Google Gemini API using google-genai SDK:
   - Creates `genai.Client` with explicit API key
   - Converts reference_image bytes to PIL Image for SDK
   - Uses `client.aio.models.generate_content()` for async
   - Extracts image from `response.parts[].inline_data.data`
   - Mode-specific error handling with troubleshooting links

4. **OpenAICompatibleClient** - REST chat/completions format for proxies:
   - Refactored from existing HeroGenerator code
   - Auto-appends `/chat/completions` if endpoint ends with `/v1`
   - Uses httpx AsyncClient for requests
   - Extracts image from `choices[].message.images[].image_url.url`
   - Mode-specific error handling with proxy troubleshooting

5. **ImageClient factory** - `from_config(config)` returns appropriate implementation based on mode

## Key Implementation Details

```python
# Factory usage
from agents.config.schema import ImageProviderConfig
from generators.image_client import ImageClient

# Native mode (google-genai SDK)
native_config = ImageProviderConfig(mode='native', api_key='GOOGLE_API_KEY')
client = ImageClient.from_config(native_config)

# OpenAI-compatible mode (REST)
compat_config = ImageProviderConfig(
    mode='openai-compatible',
    api_key='PROXY_KEY',
    endpoint='https://proxy.example.com/v1'
)
client = ImageClient.from_config(compat_config)

# Generate image
response = await client.generate(
    prompt="Generate a hero image",
    reference_image=skunk_bytes,
    aspect_ratio="21:9",
    image_size="2K"
)
```

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

| Check | Result |
|-------|--------|
| google-genai in requirements.txt | PASS |
| image_client.py imports | PASS |
| Factory returns correct client type | PASS |
| No circular import errors | PASS |

## Files Changed

| File | Change |
|------|--------|
| `requirements.txt` | Added google-genai>=1.0.0 |
| `generators/image_client.py` | Created (318 lines) |

## Commit Log

| Hash | Message |
|------|---------|
| 19e61ff | chore(03-01): add google-genai SDK dependency |
| bf98532 | feat(03-01): create unified image client abstraction |

## Next Phase Readiness

**Plan 03-02 can proceed.** The image client abstraction is complete and ready for HeroGenerator integration.

Required for 03-02:
- BaseImageClient interface defined
- Both client implementations working
- Factory method tested

No blockers identified.
