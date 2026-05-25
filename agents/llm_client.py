"""
Anthropic Client with Adaptive/Manual Thinking Support

This module provides a wrapper around the Anthropic SDK that:
1. Uses Bearer token authentication (custom httpx transport)
2. Supports Opus 4.7 adaptive thinking and legacy manual thinking budgets
3. Returns structured responses including thinking blocks
4. Automatically tracks token usage and costs
"""

import os
import re
import json
import logging
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import IntEnum

import httpx
import anthropic

from .cost_tracker import get_tracker

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .config import LLMProviderConfig

logger = logging.getLogger(__name__)


class ThinkingLevel(IntEnum):
    """Internal thinking profiles.

    On Opus 4.7+, these legacy budget values are mapped to adaptive effort
    levels. On older Claude models, they remain manual thinking budgets.
    """
    QUICK = 4096       # Simple tasks (summarization)
    STANDARD = 8192    # Normal analysis
    DEEP = 16000       # Complex ranking
    ULTRATHINK = 32000 # Cross-category synthesis


# Map legacy token budgets to Opus 4.7+ effort levels. Effort is a behavioral
# signal, not a token budget, so we key on the caller's original intent rather
# than on any max_tokens-adjusted value.
BUDGET_TO_EFFORT = {
    ThinkingLevel.QUICK: "high",
    ThinkingLevel.STANDARD: "xhigh",
    ThinkingLevel.DEEP: "max",
    ThinkingLevel.ULTRATHINK: "max",
}


def _uses_adaptive_thinking(model: str) -> bool:
    """True if model requires adaptive thinking (Opus 4.7 and later).

    Opus 4.7 removed manual thinking (`type: enabled` + budget_tokens)
    and sampling parameters. Opus 4.6 and earlier still accept manual thinking,
    so we keep the legacy path for them. Regex is permissive to handle the
    alias space: claude-opus-4-7, claude-4.7-opus, claude-opus-4-7-20260416,
    claude-4.6-opus-aws, etc.
    """
    match = re.search(r'(\d+)[-.](\d+)', model.lower())
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major > 4 or (major == 4 and minor >= 7)


# Default model max token limit (can be overridden via config or constructor)
DEFAULT_MODEL_MAX_TOKENS = 128000


@dataclass
class LLMResponse:
    """Structured response from LLM including thinking."""
    content: str
    thinking: Optional[str]
    usage: Dict[str, int]
    model: str
    stop_reason: Optional[str] = None  # Detect truncation via "max_tokens"


class BearerAuth(httpx.Auth):
    """Custom httpx auth handler for Bearer token authentication."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class ApiKeyAuth(httpx.Auth):
    """Custom httpx auth handler for Anthropic x-api-key authentication."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def auth_flow(self, request: httpx.Request):
        request.headers["x-api-key"] = self.api_key
        yield request


class AnthropicClient:
    """
    Native Anthropic client with mode-based auth and adaptive/manual thinking support.

    This client wraps the Anthropic SDK to work with either:
    - Direct Anthropic API (x-api-key header authentication)
    - OpenAI-compatible proxies (Bearer token authentication)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 600.0,
        mode: str = "anthropic",
        max_output_tokens: Optional[int] = None
    ):
        """
        Initialize the Anthropic client.

        Args:
            api_key: API key. Defaults to ANTHROPIC_API_KEY env var.
            base_url: API base URL. Defaults to ANTHROPIC_API_BASE env var.
            model: Model name. Defaults to ANTHROPIC_MODEL env var.
            timeout: Request timeout in seconds.
            mode: API mode - 'anthropic' for direct API (x-api-key),
                  'openai-compatible' for proxies (Bearer token).
            max_output_tokens: Maximum output tokens the model/proxy supports.
                             Defaults to DEFAULT_MODEL_MAX_TOKENS (128000).
        """
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.base_url = base_url or os.environ.get('ANTHROPIC_API_BASE')
        self.model = model or os.environ.get('ANTHROPIC_MODEL', 'claude-opus-4-7')
        self.timeout = timeout
        self.mode = mode
        self.max_output_tokens = max_output_tokens or DEFAULT_MODEL_MAX_TOKENS

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable or api_key parameter required")
        if not self.base_url:
            raise ValueError("ANTHROPIC_API_BASE environment variable or base_url parameter required")

        # Select auth based on mode
        if self.mode == "anthropic":
            auth = ApiKeyAuth(self.api_key)
        elif self.mode == "openai-compatible":
            auth = BearerAuth(self.api_key)
        else:
            raise ValueError(f"Unknown mode: {self.mode}. Expected 'anthropic' or 'openai-compatible'.")

        # Create httpx client with mode-appropriate auth
        self._http_client = httpx.Client(
            auth=auth,
            timeout=httpx.Timeout(timeout)
        )

        # Create Anthropic client with custom http client
        self._client = anthropic.Anthropic(
            base_url=self.base_url,
            api_key=self.api_key,  # SDK sends this as x-api-key header
            http_client=self._http_client
        )

        logger.info(f"AnthropicClient initialized with mode={self.mode}, model={self.model}, base_url={self.base_url}")

    @classmethod
    def from_config(cls, config: 'LLMProviderConfig') -> 'AnthropicClient':
        """
        Create client from LLMProviderConfig.

        Args:
            config: LLMProviderConfig with api_key, base_url, model, timeout, mode

        Returns:
            Configured AnthropicClient instance
        """
        return cls(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout,
            mode=config.mode,
            max_output_tokens=getattr(config, 'max_output_tokens', DEFAULT_MODEL_MAX_TOKENS)
        )

    def call(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0
    ) -> LLMResponse:
        """
        Make a standard API call without thinking enabled.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system: Optional system prompt.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature (ignored on Opus 4.7+).

        Returns:
            LLMResponse with content, no thinking.
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        # Opus 4.7+ rejects any non-default sampling parameter with 400.
        # Omit temperature entirely on that path.
        if not _uses_adaptive_thinking(self.model):
            kwargs["temperature"] = temperature

        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)

        # Extract text content
        content = ""
        for block in response.content:
            if hasattr(block, 'text'):
                content += block.text

        return LLMResponse(
            content=content,
            thinking=None,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            },
            model=response.model
        )

    def call_with_thinking(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: int = ThinkingLevel.STANDARD,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0
    ) -> LLMResponse:
        """
        Make an API call with adaptive or manual thinking enabled.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system: Optional system prompt.
            budget_tokens: Token budget for thinking (use ThinkingLevel enum).
            max_tokens: Maximum tokens in response. Must be > budget_tokens.
                       Defaults to budget_tokens + 8192.
            temperature: Must be 1.0 for thinking mode.

        Returns:
            LLMResponse with content and thinking blocks.
        """
        # Preserve the caller's original intent before any max_tokens capping
        # adjusts budget_tokens — effort is a behavioral signal that shouldn't
        # be reduced just because the response buffer is tight.
        original_budget = budget_tokens
        use_adaptive = _uses_adaptive_thinking(self.model)

        # max_tokens must be greater than budget_tokens
        # Use larger buffer (49152) to avoid JSON truncation in dense batches
        # (e.g. 75-item research batches can produce 30k-40k tokens of JSON).
        if max_tokens is None:
            max_tokens = budget_tokens + 49152
        elif max_tokens <= budget_tokens:
            max_tokens = budget_tokens + 16384

        # Cap at model/proxy limit
        if max_tokens > self.max_output_tokens:
            logger.debug(f"Capping max_tokens from {max_tokens} to {self.max_output_tokens} (model limit)")
            max_tokens = self.max_output_tokens

        # If capping pushed max_tokens below budget_tokens, reduce budget too
        # (only meaningful on the manual-thinking path)
        if max_tokens <= budget_tokens:
            budget_tokens = max(max_tokens - 8192, max_tokens // 2)
            logger.info(f"Reduced thinking budget to {budget_tokens} to fit within {self.max_output_tokens} token limit")

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        if use_adaptive:
            # Opus 4.7+ path: adaptive thinking with effort. Manual thinking
            # and non-default sampling parameters return 400 on these models.
            # `output_config` isn't a typed SDK param in anthropic<=0.75.x, so
            # both new fields go through extra_body as a passthrough.
            effort = BUDGET_TO_EFFORT.get(original_budget, "high")
            kwargs["extra_body"] = {
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": effort},
            }
            logger.debug(f"Calling with adaptive thinking: effort={effort}, max_tokens={max_tokens}")
        else:
            # Opus 4.6 and earlier: manual thinking with an explicit token budget.
            if temperature != 1.0:
                logger.warning("Temperature must be 1.0 for thinking mode, overriding")
                temperature = 1.0
            kwargs["temperature"] = temperature
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
            logger.debug(f"Calling with manual thinking: budget={budget_tokens}, max_tokens={max_tokens}")

        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)

        # Log stop_reason for diagnostics (helps debug proxy behavior)
        logger.debug(f"Response stop_reason: {response.stop_reason}, output_tokens: {response.usage.output_tokens}")

        # Check for truncation
        if response.stop_reason == "max_tokens":
            logger.warning(f"Response truncated at max_tokens ({max_tokens}). Output may be incomplete.")

        # Extract thinking and text content
        thinking_blocks = []
        text_blocks = []

        for block in response.content:
            if block.type == "thinking":
                thinking_blocks.append(block.thinking)
            elif block.type == "text":
                text_blocks.append(block.text)

        # On the manual path, absent thinking blocks historically signaled a
        # proxy misconfiguration (e.g. LiteLLM routing through the wrong
        # endpoint). On the adaptive path, thinking blocks are often absent
        # legitimately — the proxy may strip them, or the model may skip
        # thinking for simple prompts — so we skip this guard there.
        if not use_adaptive and budget_tokens > 0 and not thinking_blocks:
            error_msg = (
                f"Extended thinking requested (budget_tokens={budget_tokens}) but no thinking "
                f"blocks returned. This is required for quality analysis.\n\n"
            )
            if self.mode == "openai-compatible":
                error_msg += (
                    f"You are using openai-compatible mode with base_url={self.base_url}. "
                    f"If using LiteLLM, ensure you're using the Anthropic passthrough endpoint "
                    f"(e.g., http://proxy:4000/anthropic) not the OpenAI chat/completions endpoint. "
                    f"See: https://docs.litellm.ai/docs/pass_through/anthropic_completion"
                )
            else:
                error_msg += (
                    f"Check that the model '{self.model}' supports manual thinking "
                    f"and that the API endpoint is responding correctly."
                )
            raise RuntimeError(error_msg)

        return LLMResponse(
            content="\n".join(text_blocks),
            thinking="\n\n".join(thinking_blocks) if thinking_blocks else None,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            },
            model=response.model,
            stop_reason=response.stop_reason
        )

    def call_json(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: Optional[int] = None,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Make an API call expecting JSON response.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system: Optional system prompt.
            budget_tokens: If provided, enables thinking with this budget.
            max_tokens: Maximum tokens in response.

        Returns:
            Parsed JSON dict from response.
        """
        if budget_tokens:
            response = self.call_with_thinking(
                messages=messages,
                system=system,
                budget_tokens=budget_tokens,
                max_tokens=max_tokens
            )
        else:
            response = self.call(
                messages=messages,
                system=system,
                max_tokens=max_tokens
            )

        # Try to parse JSON from response
        content = response.content.strip()

        # Handle markdown code blocks
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        try:
            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response content: {response.content[:500]}")
            raise ValueError(f"Invalid JSON in response: {e}")

    def close(self):
        """Close the HTTP client."""
        self._http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Async version for parallel operations
class AsyncAnthropicClient:
    """
    Async version of AnthropicClient for parallel operations.

    Supports mode-based authentication:
    - anthropic: Direct Anthropic API with x-api-key header
    - openai-compatible: OpenAI-compatible proxies with Bearer token
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 600.0,
        mode: str = "anthropic",
        max_output_tokens: Optional[int] = None
    ):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.base_url = base_url or os.environ.get('ANTHROPIC_API_BASE')
        self.model = model or os.environ.get('ANTHROPIC_MODEL', 'claude-opus-4-7')
        self.timeout = timeout
        self.mode = mode
        self.max_output_tokens = max_output_tokens or DEFAULT_MODEL_MAX_TOKENS

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable or api_key parameter required")
        if not self.base_url:
            raise ValueError("ANTHROPIC_API_BASE environment variable or base_url parameter required")

        # Select auth based on mode
        if self.mode == "anthropic":
            auth = ApiKeyAuth(self.api_key)
        elif self.mode == "openai-compatible":
            auth = BearerAuth(self.api_key)
        else:
            raise ValueError(f"Unknown mode: {self.mode}. Expected 'anthropic' or 'openai-compatible'.")

        # Create async httpx client with mode-appropriate auth
        self._http_client = httpx.AsyncClient(
            auth=auth,
            timeout=httpx.Timeout(timeout)
        )

        # Create async Anthropic client
        self._client = anthropic.AsyncAnthropic(
            base_url=self.base_url,
            api_key=self.api_key,  # SDK sends this as x-api-key header
            http_client=self._http_client
        )

        logger.info(f"AsyncAnthropicClient initialized with mode={self.mode}, model={self.model}")

    @classmethod
    def from_config(cls, config: 'LLMProviderConfig') -> 'AsyncAnthropicClient':
        """
        Create client from LLMProviderConfig.

        Args:
            config: LLMProviderConfig with api_key, base_url, model, timeout, mode

        Returns:
            Configured AsyncAnthropicClient instance
        """
        return cls(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout,
            mode=config.mode,
            max_output_tokens=getattr(config, 'max_output_tokens', DEFAULT_MODEL_MAX_TOKENS)
        )

    async def call_with_thinking(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: int = ThinkingLevel.STANDARD,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        caller: Optional[str] = None
    ) -> LLMResponse:
        """Async version of call_with_thinking."""
        # Preserve the caller's original intent before any capping shrinks
        # budget_tokens — effort mapping keys on what the caller asked for.
        original_budget = budget_tokens
        use_adaptive = _uses_adaptive_thinking(self.model)

        # Use larger buffer (49152) to avoid JSON truncation in dense batches
        # (e.g. 75-item research batches can produce 30k-40k tokens of JSON).
        if max_tokens is None:
            max_tokens = budget_tokens + 49152
        elif max_tokens <= budget_tokens:
            max_tokens = budget_tokens + 16384

        # Cap at model/proxy limit
        if max_tokens > self.max_output_tokens:
            logger.debug(f"Capping max_tokens from {max_tokens} to {self.max_output_tokens} (model limit)")
            max_tokens = self.max_output_tokens

        # If capping pushed max_tokens below budget_tokens, reduce budget too
        # (only meaningful on the manual-thinking path)
        if max_tokens <= budget_tokens:
            budget_tokens = max(max_tokens - 8192, max_tokens // 2)
            logger.info(f"Reduced thinking budget to {budget_tokens} to fit within {self.max_output_tokens} token limit")

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        if use_adaptive:
            # Opus 4.7+ path: adaptive thinking with effort. See the sync
            # method for the extra_body rationale.
            effort = BUDGET_TO_EFFORT.get(original_budget, "high")
            kwargs["extra_body"] = {
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": effort},
            }
        else:
            # Opus 4.6 and earlier: manual thinking with an explicit budget.
            if temperature != 1.0:
                temperature = 1.0
            kwargs["temperature"] = temperature
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}

        if system:
            kwargs["system"] = system

        start_time = time.time()
        response = await self._client.messages.create(**kwargs)
        duration = time.time() - start_time

        # Log stop_reason for diagnostics (helps debug proxy behavior)
        logger.debug(f"Response stop_reason: {response.stop_reason}, output_tokens: {response.usage.output_tokens}")

        # Check for truncation
        if response.stop_reason == "max_tokens":
            logger.warning(f"Response truncated at max_tokens ({max_tokens}). Output may be incomplete.")

        thinking_blocks = []
        text_blocks = []

        for block in response.content:
            if block.type == "thinking":
                thinking_blocks.append(block.thinking)
            elif block.type == "text":
                text_blocks.append(block.text)

        # Only enforce thinking-block presence on the manual path; see
        # the sync method for rationale.
        if not use_adaptive and budget_tokens > 0 and not thinking_blocks:
            error_msg = (
                f"Extended thinking requested (budget_tokens={budget_tokens}) but no thinking "
                f"blocks returned. This is required for quality analysis.\n\n"
            )
            if self.mode == "openai-compatible":
                error_msg += (
                    f"You are using openai-compatible mode with base_url={self.base_url}. "
                    f"If using LiteLLM, ensure you're using the Anthropic passthrough endpoint "
                    f"(e.g., http://proxy:4000/anthropic) not the OpenAI chat/completions endpoint. "
                    f"See: https://docs.litellm.ai/docs/pass_through/anthropic_completion"
                )
            else:
                error_msg += (
                    f"Check that the model '{self.model}' supports manual thinking "
                    f"and that the API endpoint is responding correctly."
                )
            raise RuntimeError(error_msg)

        # Build usage dict with all available fields
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens
        }
        # Add cache tokens if present
        if hasattr(response.usage, 'cache_creation_input_tokens'):
            usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens or 0
        if hasattr(response.usage, 'cache_read_input_tokens'):
            usage["cache_read_input_tokens"] = response.usage.cache_read_input_tokens or 0

        # Track cost — label by the caller's original intent (not any
        # capped-down value) so the cost report stays legible.
        thinking_name = {
            ThinkingLevel.QUICK: "QUICK",
            ThinkingLevel.STANDARD: "STANDARD",
            ThinkingLevel.DEEP: "DEEP",
            ThinkingLevel.ULTRATHINK: "ULTRATHINK"
        }.get(original_budget, str(original_budget))

        get_tracker().record_call(
            caller=caller or "async_call_with_thinking",
            usage=usage,
            thinking_level=thinking_name,
            duration_seconds=duration,
            model=response.model
        )

        return LLMResponse(
            content="\n".join(text_blocks),
            thinking="\n\n".join(thinking_blocks) if thinking_blocks else None,
            usage=usage,
            model=response.model,
            stop_reason=response.stop_reason
        )

    async def call(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        caller: Optional[str] = None
    ) -> LLMResponse:
        """Async version of call without thinking."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        # Opus 4.7+ rejects any non-default sampling parameter with 400.
        if not _uses_adaptive_thinking(self.model):
            kwargs["temperature"] = temperature

        if system:
            kwargs["system"] = system

        start_time = time.time()
        response = await self._client.messages.create(**kwargs)
        duration = time.time() - start_time

        content = ""
        for block in response.content:
            if hasattr(block, 'text'):
                content += block.text

        # Build usage dict with all available fields
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens
        }
        if hasattr(response.usage, 'cache_creation_input_tokens'):
            usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens or 0
        if hasattr(response.usage, 'cache_read_input_tokens'):
            usage["cache_read_input_tokens"] = response.usage.cache_read_input_tokens or 0

        # Track cost
        get_tracker().record_call(
            caller=caller or "async_call",
            usage=usage,
            thinking_level=None,
            duration_seconds=duration,
            model=response.model
        )

        return LLMResponse(
            content=content,
            thinking=None,
            usage=usage,
            model=response.model
        )

    async def call_json(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: Optional[int] = None,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """Async version of call_json."""
        if budget_tokens:
            response = await self.call_with_thinking(
                messages=messages,
                system=system,
                budget_tokens=budget_tokens,
                max_tokens=max_tokens
            )
        else:
            response = await self.call(
                messages=messages,
                system=system,
                max_tokens=max_tokens
            )

        content = response.content.strip()

        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        try:
            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise ValueError(f"Invalid JSON in response: {e}")

    async def close(self):
        """Close the async HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
