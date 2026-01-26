# Phase 3: Image Provider Support - Research

**Researched:** 2026-01-25
**Domain:** Google Gemini image generation via google-genai SDK and OpenAI-compatible endpoints
**Confidence:** HIGH

## Summary

This phase replaces the current RDSec MCP tool dependency for hero image generation with direct Google Gemini API access (native mode) or OpenAI-compatible proxy endpoints. The google-genai SDK is the official Python SDK for Google's generative AI models and provides a clean interface for image generation using `gemini-3-pro-image-preview`.

The research confirms that image generation with Gemini follows a simple pattern: create a client with API key, call `generate_content()` with image config, and extract base64 image data from the response. The SDK handles authentication, retries (configurable), and provides structured error handling via `errors.APIError`.

The existing HeroGenerator already uses OpenAI-compatible chat completions format (the RDSec endpoint is an OpenAI-compatible proxy), so the openai-compatible mode requires minimal changes to the current implementation. Native mode requires a new code path using the google-genai SDK.

**Primary recommendation:** Implement a unified ImageClient abstraction with NativeGeminiClient and OpenAICompatibleClient implementations. Use factory pattern (`ImageClient.from_config()`) to instantiate the appropriate client based on mode. Follow the same pattern established in LLM client (Phase 2).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-genai | >=1.0.0 | Native Google Gemini SDK | Official Google SDK, replaces deprecated google-generativeai |
| httpx | >=0.27.0 | OpenAI-compatible HTTP requests | Already used by LLM client, async support, configurable timeouts |
| Pillow | >=10.0.0 | Image handling | Already installed, used by image_optimizer |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | >=8.0.0 | Retry logic | Optional - google-genai has built-in HttpRetryOptions |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| google-genai | google-generativeai | Deprecated, google-genai is the new official SDK |
| httpx for openai-compatible | requests | httpx has better async support and is consistent with LLM client |
| Built-in retries | tenacity | google-genai has HttpRetryOptions; httpx needs manual retry handling |

**Installation:**
```bash
pip install google-genai>=1.0.0
```

Note: httpx and Pillow already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
generators/
├── hero_generator.py        # High-level hero generation orchestration (unchanged interface)
├── image_client.py          # NEW: Unified image client abstraction
├── image_optimizer.py       # Existing WebP optimization
└── __init__.py
```

### Pattern 1: Factory Pattern for Mode Selection
**What:** Use factory method to instantiate mode-specific client implementation
**When to use:** When config determines which implementation to use
**Example:**
```python
# Source: Follows LLM client pattern from Phase 2
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class ImageResponse:
    """Response from image generation."""
    image_data: bytes  # Raw image bytes (PNG)
    mime_type: str = "image/png"

class BaseImageClient(ABC):
    """Abstract base class for image generation clients."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        """Generate an image from a prompt."""
        pass

class ImageClient:
    """Factory for creating image clients based on config."""

    @classmethod
    def from_config(cls, config: 'ImageProviderConfig') -> BaseImageClient:
        if config.mode == "native":
            return NativeGeminiClient(
                api_key=config.api_key,
                model=config.model
            )
        elif config.mode == "openai-compatible":
            return OpenAICompatibleClient(
                api_key=config.api_key,
                endpoint=config.endpoint,
                model=config.model
            )
        else:
            raise ValueError(f"Unknown image mode: {config.mode}")
```

### Pattern 2: Native Gemini Client with google-genai SDK
**What:** Use official SDK for direct Google API access
**When to use:** Users with Google AI API keys (native mode)
**Example:**
```python
# Source: https://github.com/googleapis/python-genai (Context7)
from google import genai
from google.genai import types, errors
from PIL import Image
import io

class NativeGeminiClient(BaseImageClient):
    """Image client using google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-pro-image-preview",
        timeout: float = 180.0
    ):
        self.model = model
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),  # SDK uses milliseconds
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=1.0,
                    max_delay=10.0
                )
            )
        )

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        # Build contents list
        contents = []
        if reference_image:
            # Add reference image first
            pil_image = Image.open(io.BytesIO(reference_image))
            contents.append(pil_image)
        contents.append(prompt)

        # Generate with image config
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
            )
        )

        # Extract image from response
        for part in response.parts:
            if part.inline_data:
                return ImageResponse(
                    image_data=part.inline_data.data,
                    mime_type=part.inline_data.mime_type or "image/png"
                )

        raise RuntimeError("No image returned from API")
```

### Pattern 3: OpenAI-Compatible Client (existing RDSec pattern)
**What:** Use httpx for chat/completions format endpoints
**When to use:** Users with LiteLLM or other proxies (openai-compatible mode)
**Example:**
```python
# Source: Existing hero_generator.py implementation
import httpx
import base64

class OpenAICompatibleClient(BaseImageClient):
    """Image client using OpenAI chat/completions format."""

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        model: str,
        timeout: float = 180.0
    ):
        self.api_key = api_key
        self.model = model
        # Auto-append /chat/completions if endpoint ends with /v1
        if endpoint.rstrip('/').endswith('/v1'):
            self.endpoint = endpoint.rstrip('/') + '/chat/completions'
        else:
            self.endpoint = endpoint
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        # Build message content
        content = []
        if reference_image:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.b64encode(reference_image).decode()}"
                }
            })
        content.append({"type": "text", "text": prompt})

        request_body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 1.0,
            "modalities": ["image", "text"],
            "image_config": {
                "aspect_ratio": aspect_ratio,
                "image_size": image_size
            }
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_body
            )
            response.raise_for_status()
            data = response.json()

        # Extract image from response
        message = data.get("choices", [{}])[0].get("message", {})
        images = message.get("images", [])

        if not images:
            raise RuntimeError("No image returned from API")

        image_url = images[0].get("image_url", {}).get("url", "")
        if not image_url or "," not in image_url:
            raise RuntimeError("Invalid image URL format in response")

        image_base64 = image_url.split(",", 1)[1]
        return ImageResponse(
            image_data=base64.b64decode(image_base64),
            mime_type="image/png"
        )
```

### Pattern 4: Graceful Skip with Clear Logging
**What:** Skip hero generation when no image provider configured
**When to use:** Always - handle missing config gracefully
**Example:**
```python
# Source: CONTEXT.md decisions
import logging

logger = logging.getLogger(__name__)

def initialize_image_client(config: Optional['ImageProviderConfig']) -> Optional[BaseImageClient]:
    """Initialize image client, returning None if not configured."""
    if config is None:
        logger.warning(
            "Hero image generation disabled: no 'image' section in providers.yaml. "
            "To enable, add image provider config. You can also run "
            "scripts/regenerate_hero.py later to generate images."
        )
        return None

    try:
        return ImageClient.from_config(config)
    except ValueError as e:
        logger.warning(
            f"Hero image generation disabled: {e}. "
            "Check your image provider configuration."
        )
        return None
```

### Anti-Patterns to Avoid
- **Mixing sync/async in image client:** Use async throughout; the orchestrator is async
- **Hardcoding model names:** Use config model field, defaults in schema
- **Silent failures:** Always log why generation was skipped or failed
- **Blocking retries:** Let SDK/httpx handle retries with proper backoff

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry logic (native) | While loops with sleep | HttpRetryOptions | SDK handles backoff, jitter, respects rate limits |
| Retry logic (openai-compatible) | Manual retry | httpx retry or tenacity | Proper exponential backoff, respects HTTP semantics |
| Image format detection | Check magic bytes | PIL Image.open | Handles all formats, validates data |
| Base64 encoding | Manual string ops | base64 module | Proper padding, encoding |
| Auth header | String concat | httpx auth or custom class | Follows LLM client pattern |

**Key insight:** The google-genai SDK handles most complexity (auth, retries, response parsing). For openai-compatible mode, the existing hero_generator.py code works - just refactor into the new abstraction.

## Common Pitfalls

### Pitfall 1: google-genai vs google-generativeai
**What goes wrong:** Installing deprecated package, API differences
**Why it happens:** Old tutorials reference google-generativeai
**How to avoid:** Always use `google-genai` (the new official SDK), not `google-generativeai`
**Warning signs:** ImportError for `genai.Client`, deprecation warnings

### Pitfall 2: SDK Timeout Units
**What goes wrong:** Timeout too short or too long
**Why it happens:** google-genai uses milliseconds, not seconds
**How to avoid:** Convert: `int(timeout_seconds * 1000)`
**Warning signs:** Immediate timeouts or very long waits

### Pitfall 3: Missing Reference Image Handling
**What goes wrong:** Error when passing reference image to native SDK
**Why it happens:** SDK expects PIL Image, not raw bytes
**How to avoid:** Use `Image.open(io.BytesIO(bytes_data))` to convert
**Warning signs:** TypeError about image format

### Pitfall 4: Response Format Differences
**What goes wrong:** Code expecting one format gets another
**Why it happens:** Native SDK returns `parts[].inline_data`, OpenAI format returns `choices[].message.images`
**How to avoid:** Abstract response parsing into mode-specific clients
**Warning signs:** KeyError or AttributeError when accessing response

### Pitfall 5: API Key Environment Variable
**What goes wrong:** SDK picks up wrong key from environment
**Why it happens:** google-genai auto-reads GEMINI_API_KEY and GOOGLE_API_KEY env vars
**How to avoid:** Always pass `api_key` explicitly to `genai.Client()`
**Warning signs:** Auth errors when you expected it to work, or wrong account billed

### Pitfall 6: Blocking Async Context
**What goes wrong:** Blocking sync call inside async function
**Why it happens:** google-genai Client is sync by default
**How to avoid:** Use `client.aio` for async operations, or run sync in thread pool
**Warning signs:** Event loop blocked, slow parallel operations

## Code Examples

### Native Gemini Client - Full Implementation
```python
# Source: https://github.com/googleapis/python-genai (Context7)
from google import genai
from google.genai import types, errors
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

class NativeGeminiClient(BaseImageClient):
    """Image client using google-genai SDK (native mode)."""

    DEFAULT_MODEL = "gemini-3-pro-image-preview"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: float = 180.0
    ):
        self.model = model or self.DEFAULT_MODEL
        self.client = genai.Client(
            api_key=api_key,  # Explicit key, don't rely on env vars
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=1.0,
                    max_delay=10.0
                )
            )
        )
        logger.info(f"NativeGeminiClient initialized with model={self.model}")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        """Generate image using google-genai SDK."""
        contents = []

        # Add reference image if provided (must be PIL Image)
        if reference_image:
            pil_image = Image.open(io.BytesIO(reference_image))
            contents.append(pil_image)

        contents.append(prompt)

        try:
            # Use async client
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size=image_size
                    )
                )
            )
        except errors.APIError as e:
            if e.code == 429:
                logger.warning("Rate limited by Gemini API")
            elif e.code == 400:
                logger.error(f"Invalid request: {e.message}")
            raise RuntimeError(f"Gemini API error {e.code}: {e.message}")

        # Extract image from response parts
        for part in response.parts:
            if part.inline_data:
                return ImageResponse(
                    image_data=part.inline_data.data,
                    mime_type=part.inline_data.mime_type or "image/png"
                )

        raise RuntimeError(
            "No image returned from Gemini API. "
            "Check that the model supports image generation."
        )
```

### Error Handling with Mode-Specific Guidance
```python
# Source: CONTEXT.md decisions
def handle_image_error(e: Exception, mode: str) -> None:
    """Log error with mode-specific troubleshooting guidance."""
    if mode == "native":
        logger.error(
            f"Image generation failed (native mode): {e}. "
            f"Troubleshooting: Verify your GOOGLE_API_KEY has access to "
            f"gemini-3-pro-image-preview. Check quotas at "
            f"https://console.cloud.google.com/apis/dashboard"
        )
    elif mode == "openai-compatible":
        logger.error(
            f"Image generation failed (openai-compatible mode): {e}. "
            f"Troubleshooting: Verify your proxy endpoint supports image "
            f"generation and the model name is correct for your proxy."
        )
```

### Async Client Usage
```python
# Source: https://github.com/googleapis/python-genai (Context7)
from google import genai

# For async operations, use client.aio
async with genai.Client(api_key='YOUR_API_KEY').aio as aclient:
    response = await aclient.models.generate_content(
        model='gemini-3-pro-image-preview',
        contents='Generate an image',
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(
                aspect_ratio="21:9",
                image_size="2K"
            )
        )
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| google-generativeai | google-genai | 2025 | New SDK, different API surface |
| genai.configure() | genai.Client() | google-genai 1.0 | Client-based, not global config |
| Sync-only | async with client.aio | google-genai 1.0 | Native async support |
| imagen models | gemini-*-image-preview | 2025 | Gemini models now do image gen |

**Deprecated/outdated:**
- `google-generativeai` package - Use `google-genai` instead
- `genai.configure(api_key=...)` - Use `genai.Client(api_key=...)` instead
- `imagen-3.0-generate-002` - Use `gemini-3-pro-image-preview` for better quality

## Open Questions

Things that couldn't be fully resolved:

1. **Async context manager behavior**
   - What we know: `async with client.aio as aclient` is supported
   - What's unclear: Whether we need to manage client lifecycle or can create per-request
   - Recommendation: Create client once in `__init__`, reuse for all requests. Match LLM client pattern.

2. **Image editing support**
   - What we know: Native SDK has `edit_image()` for Imagen models, but Gemini image models use chat mode
   - What's unclear: Whether `gemini-3-pro-image-preview` supports the same edit workflow
   - Recommendation: Keep existing edit functionality in HeroGenerator using openai-compatible path. Mark as v2 improvement for native mode.

## Sources

### Primary (HIGH confidence)
- [googleapis/python-genai](https://github.com/googleapis/python-genai) via Context7 - SDK initialization, generate_content, error handling, async usage
- [Google AI Gemini API Docs](https://ai.google.dev/gemini-api/docs/image-generation) via Context7 - API format, image_config options

### Secondary (MEDIUM confidence)
- [Google AI Developers Forum](https://discuss.ai.google.dev/t/how-to-implement-retry-logic-in-the-new-python-sdk/83052) - HttpRetryOptions configuration
- Existing codebase (`agents/llm_client.py`, `generators/hero_generator.py`) - Established patterns

### Tertiary (LOW confidence)
- WebSearch results for retry patterns - verified against SDK source

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - google-genai is official SDK with good docs
- Architecture: HIGH - Follows established LLM client pattern from Phase 2
- Pitfalls: HIGH - Documented in official SDK and verified in Context7

**Research date:** 2026-01-25
**Valid until:** 60 days (google-genai is actively developed but API is stable)
