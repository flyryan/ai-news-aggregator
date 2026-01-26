"""
Unified Image Client Abstraction

Provides mode-based image generation:
- native: Uses google-genai SDK directly for Google Gemini API
- openai-compatible: Uses REST chat/completions format for LiteLLM proxies

Follows the same factory pattern as agents/llm_client.py for consistency.
"""

import io
import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import httpx
from PIL import Image

from google import genai
from google.genai import types, errors

if TYPE_CHECKING:
    from agents.config import ImageProviderConfig

logger = logging.getLogger(__name__)


@dataclass
class ImageResponse:
    """Response from image generation."""
    image_data: bytes  # Raw image bytes
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
        """
        Generate an image from a prompt.

        Args:
            prompt: Text prompt describing the image to generate
            reference_image: Optional reference image bytes for style/content guidance
            aspect_ratio: Image aspect ratio (default "21:9" for hero banners)
            image_size: Image resolution (default "2K")

        Returns:
            ImageResponse with raw image bytes and mime type
        """
        pass


class NativeGeminiClient(BaseImageClient):
    """
    Image client using google-genai SDK (native mode).

    Uses the official Google SDK for direct Gemini API access.
    Recommended for users with Google AI API keys.
    """

    DEFAULT_MODEL = "gemini-3-pro-image-preview"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: float = 180.0
    ):
        """
        Initialize native Gemini client.

        Args:
            api_key: Google AI API key (explicit, not from env vars)
            model: Model name (default: gemini-3-pro-image-preview)
            timeout: Request timeout in seconds (converted to ms for SDK)
        """
        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout

        # Create client with explicit API key and retry options
        # SDK uses milliseconds for timeout
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
            )
        )

        logger.info(f"NativeGeminiClient initialized with model={self.model}, timeout={timeout}s")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        """Generate image using google-genai SDK."""
        contents = []

        # Add reference image if provided (must be PIL Image for SDK)
        if reference_image:
            pil_image = Image.open(io.BytesIO(reference_image))
            contents.append(pil_image)

        contents.append(prompt)

        try:
            # Use async client (client.aio)
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
            error_msg = (
                f"Gemini API error (code={e.code}): {e.message}\n\n"
                f"Troubleshooting (native mode):\n"
                f"- Verify your Google API key has access to {self.model}\n"
                f"- Check quotas at https://console.cloud.google.com/apis/dashboard\n"
                f"- Ensure the model name is correct for your API access level"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        # Extract image from response parts
        for part in response.parts:
            if part.inline_data:
                return ImageResponse(
                    image_data=part.inline_data.data,
                    mime_type=part.inline_data.mime_type or "image/png"
                )

        raise RuntimeError(
            "No image returned from Gemini API. "
            "Check that the model supports image generation and the prompt is valid."
        )


class OpenAICompatibleClient(BaseImageClient):
    """
    Image client using OpenAI chat/completions format (openai-compatible mode).

    Uses REST API for LiteLLM or other OpenAI-compatible proxies.
    Refactored from existing HeroGenerator implementation.
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        model: str,
        timeout: float = 180.0
    ):
        """
        Initialize OpenAI-compatible client.

        Args:
            api_key: API key for Bearer authentication
            endpoint: API endpoint URL (auto-appends /chat/completions if ends with /v1)
            model: Model name for the proxy
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

        # Auto-append /chat/completions if endpoint ends with /v1
        if endpoint.rstrip('/').endswith('/v1'):
            self.endpoint = endpoint.rstrip('/') + '/chat/completions'
        else:
            self.endpoint = endpoint

        logger.info(f"OpenAICompatibleClient initialized with endpoint={self.endpoint}, model={self.model}")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        aspect_ratio: str = "21:9",
        image_size: str = "2K"
    ) -> ImageResponse:
        """Generate image using OpenAI chat/completions format."""
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
            "temperature": 1.0,  # Required by Gemini image models
            "modalities": ["image", "text"],
            "image_config": {
                "aspect_ratio": aspect_ratio,
                "image_size": image_size
            }
        }

        try:
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

        except httpx.TimeoutException:
            error_msg = f"Image generation timed out after {self.timeout}s"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except httpx.HTTPStatusError as e:
            error_msg = (
                f"Image generation API error (status={e.response.status_code}): "
                f"{e.response.text[:500]}\n\n"
                f"Troubleshooting (openai-compatible mode):\n"
                f"- Verify your proxy endpoint supports image generation\n"
                f"- Check that the model name '{self.model}' is correct for your proxy\n"
                f"- Ensure the API key has proper permissions"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except httpx.RequestError as e:
            error_msg = (
                f"Image generation request failed: {e}\n\n"
                f"Troubleshooting (openai-compatible mode):\n"
                f"- Verify the endpoint URL is correct: {self.endpoint}\n"
                f"- Check network connectivity to your proxy"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        # Extract image from response
        message = data.get("choices", [{}])[0].get("message", {})
        images = message.get("images", [])

        if not images:
            error_content = message.get("content", "Unknown error - no images returned")
            raise RuntimeError(f"No image returned from API: {error_content}")

        image_url = images[0].get("image_url", {}).get("url", "")
        if not image_url or "," not in image_url:
            raise RuntimeError("Invalid image URL format in response - expected base64 data URL")

        # Parse base64 data URL (format: data:image/png;base64,<data>)
        image_base64 = image_url.split(",", 1)[1]
        return ImageResponse(
            image_data=base64.b64decode(image_base64),
            mime_type="image/png"
        )


class ImageClient:
    """
    Factory class for creating image clients based on configuration.

    Usage:
        client = ImageClient.from_config(config)
        response = await client.generate(prompt, reference_image)
    """

    @classmethod
    def from_config(cls, config: 'ImageProviderConfig') -> BaseImageClient:
        """
        Create appropriate image client based on config mode.

        Args:
            config: ImageProviderConfig with mode, api_key, endpoint, model

        Returns:
            NativeGeminiClient for native mode
            OpenAICompatibleClient for openai-compatible mode

        Raises:
            ValueError: If mode is unknown
        """
        if config.mode == "native":
            return NativeGeminiClient(
                api_key=config.api_key,
                model=config.model
            )
        elif config.mode == "openai-compatible":
            return OpenAICompatibleClient(
                api_key=config.api_key,
                endpoint=config.endpoint,  # Already validated by schema
                model=config.model
            )
        else:
            raise ValueError(
                f"Unknown image mode: {config.mode}. "
                f"Expected 'native' or 'openai-compatible'."
            )
