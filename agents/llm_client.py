"""
Anthropic Client with Adaptive/Manual Thinking Support

This module provides a wrapper around the Anthropic SDK that:
1. Uses Bearer token authentication (custom httpx transport)
2. Supports Opus 4.8 adaptive thinking and legacy manual thinking budgets
3. Returns structured responses including thinking blocks
4. Automatically tracks token usage and costs
"""

import os
import re
import json
import logging
import time
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from enum import IntEnum

import httpx
import anthropic

from .cost_tracker import get_tracker

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .config import LLMProviderConfig
    from .config.schema import ResolvedLLMRouteConfig

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an integer environment setting with validation."""
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
        return default
    if value < minimum:
        logger.warning(f"Ignoring {name}={value}; minimum is {minimum}, using {default}")
        return default
    return value


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Read a float environment setting with validation."""
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
        return default
    if value < minimum:
        logger.warning(f"Ignoring {name}={value}; minimum is {minimum}, using {default}")
        return default
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment setting."""
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
    return default


class ThinkingLevel(IntEnum):
    """Internal analysis profiles.

    On Opus 4.8+, these legacy budget values are mapped to adaptive effort
    levels. On older Claude models, they remain manual thinking budgets.
    """
    QUICK = 4096       # Simple tasks (summarization)
    STANDARD = 8192    # Normal analysis
    DEEP = 16000       # Complex ranking
    ULTRATHINK = 32000 # Cross-category synthesis


# Map internal pipeline analysis profiles to Opus 4.8+ effort levels. Effort is
# the provider-facing reasoning knob; the enum values only remain for older
# manual-thinking models and backwards-compatible call sites.
BUDGET_TO_EFFORT = {
    ThinkingLevel.QUICK: "high",
    ThinkingLevel.STANDARD: "xhigh",
    ThinkingLevel.DEEP: "max",
    ThinkingLevel.ULTRATHINK: "max",
}

# Opus 4.8 adaptive thinking does not use fixed thinking budgets. Keep the
# response ceiling separate from internal profiles so logs do not imply that
# QUICK/STANDARD/DEEP/ULTRATHINK are Anthropic token-budget settings.
DEFAULT_ADAPTIVE_MAX_TOKENS = 65536

THINKING_LEVEL_NAMES = {
    ThinkingLevel.QUICK: "QUICK",
    ThinkingLevel.STANDARD: "STANDARD",
    ThinkingLevel.DEEP: "DEEP",
    ThinkingLevel.ULTRATHINK: "ULTRATHINK",
}


def _content_char_count(content: Any) -> int:
    """Estimate request content size without logging the raw content."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False, default=str))


def _messages_char_count(messages: List[Dict[str, Any]]) -> int:
    return sum(_content_char_count(message.get("content")) for message in messages)


def _uses_adaptive_thinking(model: str) -> bool:
    """True if model requires adaptive thinking (Opus 4.8 and later).

    Opus 4.8 removed manual thinking (`type: enabled` + budget_tokens)
    and sampling parameters. Opus 4.6 and earlier still accept manual thinking,
    so we keep the legacy path for them. Regex is permissive to handle the
    alias space: claude-opus-4-8, claude-4.8-opus, claude-opus-4-8-20260416,
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
    thinking_type: Optional[str] = None
    adaptive_effort: Optional[str] = None
    analysis_profile: Optional[str] = None
    thinking_block_count: int = 0


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
        self.model = model or os.environ.get('ANTHROPIC_MODEL', 'claude-4.8-opus-aws')
        self.timeout = _env_float("LLM_TIMEOUT_SECONDS", timeout, minimum=1.0)
        self.mode = mode
        self.max_output_tokens = max_output_tokens or DEFAULT_MODEL_MAX_TOKENS
        self.adaptive_max_tokens = min(
            _env_int("LLM_ADAPTIVE_MAX_TOKENS", DEFAULT_ADAPTIVE_MAX_TOKENS, minimum=1024),
            self.max_output_tokens,
        )
        self.trust_env_proxy = _env_bool("LLM_TRUST_ENV_PROXY", False)
        self.max_retries = _env_int("LLM_MAX_RETRIES", 2, minimum=0)

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
            timeout=httpx.Timeout(self.timeout),
            trust_env=self.trust_env_proxy
        )

        # Create Anthropic client with custom http client
        self._client = anthropic.Anthropic(
            base_url=self.base_url,
            api_key=self.api_key,  # SDK sends this as x-api-key header
            http_client=self._http_client,
            max_retries=self.max_retries,
        )

        logger.info(
            f"AnthropicClient initialized with mode={self.mode}, model={self.model}, "
            f"base_url={self.base_url}, timeout={self.timeout}s, sdk_max_retries={self.max_retries}, "
            f"trust_env_proxy={self.trust_env_proxy}"
        )

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
        Make a plain API call.

        Opus 4.8+ still receives adaptive thinking request metadata here; this
        method only means the caller does not need returned thinking text.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system: Optional system prompt.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature (ignored on Opus 4.8+).

        Returns:
            LLMResponse with content and no returned thinking text.
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        use_adaptive = _uses_adaptive_thinking(self.model)
        if use_adaptive:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["extra_body"] = {"output_config": {"effort": "high"}}
        else:
            kwargs["temperature"] = temperature

        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)

        # Extract text content
        content = ""
        thinking_block_count = 0
        for block in response.content:
            if getattr(block, "type", None) == "thinking":
                thinking_block_count += 1
            if hasattr(block, 'text'):
                content += block.text

        return LLMResponse(
            content=content,
            thinking=None,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            },
            model=response.model,
            thinking_type="adaptive" if use_adaptive else None,
            adaptive_effort="high" if use_adaptive else None,
            analysis_profile="plain" if use_adaptive else None,
            thinking_block_count=thinking_block_count
        )

    def call_with_thinking(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: int = ThinkingLevel.STANDARD,
        profile: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        full_output_budget: bool = False
    ) -> LLMResponse:
        """
        Make an API call with adaptive or manual thinking enabled.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system: Optional system prompt.
            budget_tokens: Backward-compatible manual thinking budget/profile.
            profile: Analysis profile (use ThinkingLevel enum). Opus 4.8+
                     maps this to adaptive effort; older models use it as a
                     manual thinking budget.
            max_tokens: Maximum response output tokens. On adaptive models this
                       defaults to LLM_ADAPTIVE_MAX_TOKENS; older manual
                       thinking models default to profile plus an output buffer.
            temperature: Must be 1.0 for thinking mode.
            full_output_budget: Raise the default ceiling to the full model
                       output limit so max-effort thinking cannot clip the
                       output. Effort is never reduced.

        Returns:
            LLMResponse with content and thinking blocks.
        """
        requested_profile = profile if profile is not None else budget_tokens
        profile_name = THINKING_LEVEL_NAMES.get(requested_profile, str(requested_profile))
        use_adaptive = _uses_adaptive_thinking(self.model)

        if use_adaptive:
            effort = BUDGET_TO_EFFORT.get(requested_profile, "high")
            if max_tokens is None:
                max_tokens = self.max_output_tokens if full_output_budget else self.adaptive_max_tokens
            manual_budget_tokens = None
        else:
            effort = None
            manual_budget_tokens = int(requested_profile)
            # Use larger buffer (49152) to avoid JSON truncation in dense batches
            # on older manual-thinking models.
            if max_tokens is None:
                max_tokens = manual_budget_tokens + 49152
            elif max_tokens <= manual_budget_tokens:
                max_tokens = manual_budget_tokens + 16384

        # Cap at model/proxy limit
        if max_tokens > self.max_output_tokens:
            logger.debug(f"Capping max_tokens from {max_tokens} to {self.max_output_tokens} (model limit)")
            max_tokens = self.max_output_tokens

        # If capping pushed max_tokens below the manual budget, reduce it too
        # (only meaningful on the manual-thinking path)
        if not use_adaptive and max_tokens <= manual_budget_tokens:
            manual_budget_tokens = max(max_tokens - 8192, max_tokens // 2)
            logger.info(
                f"Reduced manual thinking budget to {manual_budget_tokens} "
                f"to fit within {self.max_output_tokens} token limit"
            )

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        if use_adaptive:
            # Opus 4.8+ path: adaptive thinking with effort. Manual thinking
            # and non-default sampling parameters return 400 on these models.
            # `thinking` is a typed SDK param in anthropic>=0.75.0; keep it
            # top-level so Opus 4.8 cannot silently run with thinking disabled.
            # `output_config` is not typed in this SDK yet, so effort goes
            # through extra_body as a passthrough.
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["extra_body"] = {"output_config": {"effort": effort}}
            logger.debug(
                f"Calling with adaptive thinking: analysis_profile={profile_name}, "
                f"effort={effort}, max_tokens={max_tokens}"
            )
        else:
            # Opus 4.6 and earlier: manual thinking with an explicit token budget.
            if temperature != 1.0:
                logger.warning("Temperature must be 1.0 for thinking mode, overriding")
                temperature = 1.0
            kwargs["temperature"] = temperature
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": manual_budget_tokens}
            logger.debug(
                f"Calling with manual thinking: profile={profile_name}, "
                f"budget={manual_budget_tokens}, max_tokens={max_tokens}"
            )

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
        if not use_adaptive and manual_budget_tokens > 0 and not thinking_blocks:
            error_msg = (
                f"Extended thinking requested (budget_tokens={manual_budget_tokens}) but no thinking "
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
            stop_reason=response.stop_reason,
            thinking_type="adaptive" if use_adaptive else "enabled",
            adaptive_effort=effort,
            analysis_profile=profile_name,
            thinking_block_count=len(thinking_blocks)
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
        max_output_tokens: Optional[int] = None,
        provider_id: Optional[str] = None,
        max_concurrent_requests: Optional[int] = None,
        max_retries: Optional[int] = None
    ):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.base_url = base_url or os.environ.get('ANTHROPIC_API_BASE')
        self.model = model or os.environ.get('ANTHROPIC_MODEL', 'claude-4.8-opus-aws')
        self.provider_id = provider_id or self.model
        self.timeout = _env_float("LLM_TIMEOUT_SECONDS", timeout, minimum=1.0)
        self.mode = mode
        self.max_output_tokens = max_output_tokens or DEFAULT_MODEL_MAX_TOKENS
        self.adaptive_max_tokens = min(
            _env_int("LLM_ADAPTIVE_MAX_TOKENS", DEFAULT_ADAPTIVE_MAX_TOKENS, minimum=1024),
            self.max_output_tokens,
        )
        self.trust_env_proxy = _env_bool("LLM_TRUST_ENV_PROXY", False)
        self.max_concurrent_requests = (
            max_concurrent_requests
            if max_concurrent_requests is not None
            else _env_int("LLM_MAX_CONCURRENT_REQUESTS", 8)
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else _env_int("LLM_MAX_RETRIES", 2, minimum=0)
        )
        self.log_requests = _env_bool("LLM_LOG_REQUESTS", True)
        self.heartbeat_seconds = _env_float("LLM_HEARTBEAT_SECONDS", 60.0, minimum=0.0)
        self.metrics_path = os.environ.get("LLM_METRICS_PATH", "").strip()
        self._request_semaphore = (
            asyncio.Semaphore(self.max_concurrent_requests)
            if self.max_concurrent_requests > 0
            else None
        )
        self._request_lock = asyncio.Lock()
        self._metrics_lock = asyncio.Lock()
        self._request_sequence = 0
        self._active_requests = 0
        self._queued_requests = 0

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
            timeout=httpx.Timeout(self.timeout),
            trust_env=self.trust_env_proxy
        )

        # Create async Anthropic client
        self._client = anthropic.AsyncAnthropic(
            base_url=self.base_url,
            api_key=self.api_key,  # SDK sends this as x-api-key header
            http_client=self._http_client,
            max_retries=self.max_retries,
        )

        logger.info(
            f"AsyncAnthropicClient initialized with provider_id={self.provider_id}, "
            f"mode={self.mode}, model={self.model}, "
            f"timeout={self.timeout}s, sdk_max_retries={self.max_retries}, "
            f"heartbeat_seconds={self.heartbeat_seconds}, "
            f"max_concurrent_requests={self.max_concurrent_requests or 'unlimited'}, "
            f"trust_env_proxy={self.trust_env_proxy}, request_logging={self.log_requests}, "
            f"metrics_path={self.metrics_path or 'disabled'}"
        )

    def _format_request_context(self, request_context: Optional[Dict[str, Any]]) -> str:
        context = request_context or {}
        parts = [
            f"caller={context.get('caller', 'unknown')}",
            f"kind={context.get('kind', 'message')}",
            f"provider_id={context.get('provider_id', self.provider_id)}",
            f"provider_model={context.get('provider_model', self.model)}",
        ]
        for key in (
            "thinking_type",
            "analysis_profile",
            "adaptive_effort",
            "manual_budget_tokens",
            "response_max_tokens",
            "thinking_blocks",
            "attempt",
            "fallback_from",
            "retry_reason",
            "message_count",
            "message_chars",
            "system_chars",
        ):
            value = context.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        return " ".join(parts)

    async def _register_queued_request(self, request_context: Optional[Dict[str, Any]]) -> Tuple[int, int, int]:
        async with self._request_lock:
            self._request_sequence += 1
            request_id = self._request_sequence
            self._queued_requests += 1
            active = self._active_requests
            queued = self._queued_requests
        logger.debug(
            f"LLM queued #{request_id} {self._format_request_context(request_context)} "
            f"active={active} queued={queued} cap={self.max_concurrent_requests or 'unlimited'}"
        )
        return request_id, active, queued

    async def _mark_request_started(
        self,
        request_id: int,
        request_context: Optional[Dict[str, Any]],
        queued_at: float,
    ) -> float:
        async with self._request_lock:
            self._queued_requests = max(0, self._queued_requests - 1)
            self._active_requests += 1
            active = self._active_requests
            queued = self._queued_requests
        wait_seconds = time.time() - queued_at
        logger.debug(
            f"LLM start #{request_id} {self._format_request_context(request_context)} "
            f"active={active} queued={queued} waited={wait_seconds:.1f}s"
        )
        return wait_seconds

    async def _mark_request_finished(
        self,
        request_id: int,
        request_context: Optional[Dict[str, Any]],
        started_at: float,
        wait_seconds: float,
        response: Optional[Any] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        async with self._request_lock:
            self._active_requests = max(0, self._active_requests - 1)
            active = self._active_requests
            queued = self._queued_requests

        duration = time.time() - started_at
        if error is not None:
            logger.info(
                f"LLM failed #{request_id} {self._format_request_context(request_context)} "
                f"active={active} queued={queued} duration={duration:.1f}s "
                f"error={type(error).__name__}: {error}"
            )
            await self._write_metric({
                "event": "failed",
                "request_id": request_id,
                "context": request_context or {},
                "wait_seconds": round(wait_seconds, 3),
                "duration_seconds": round(duration, 3),
                "active_after": active,
                "queued_after": queued,
                "error_type": type(error).__name__,
                "error": str(error),
            })
            return

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        stop_reason = getattr(response, "stop_reason", None)
        content_blocks = getattr(response, "content", []) or []
        thinking_block_count = sum(
            1 for block in content_blocks if getattr(block, "type", None) == "thinking"
        )
        text_block_count = sum(
            1 for block in content_blocks if getattr(block, "type", None) == "text"
        )
        logger.info(
            f"LLM done #{request_id} {self._format_request_context(request_context)} "
            f"active={active} queued={queued} duration={duration:.1f}s "
            f"stop_reason={stop_reason} input_tokens={input_tokens} output_tokens={output_tokens} "
            f"thinking_blocks={thinking_block_count} text_blocks={text_block_count}"
        )
        await self._write_metric({
            "event": "done",
            "request_id": request_id,
            "context": request_context or {},
            "wait_seconds": round(wait_seconds, 3),
            "duration_seconds": round(duration, 3),
            "active_after": active,
            "queued_after": queued,
            "stop_reason": stop_reason,
            "thinking_blocks": thinking_block_count,
            "text_blocks": text_block_count,
            "response_model": getattr(response, "model", None),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            },
        })

    async def _cancel_queued_request(
        self,
        request_id: int,
        request_context: Optional[Dict[str, Any]],
        queued_at: float,
        error: BaseException,
    ) -> None:
        async with self._request_lock:
            self._queued_requests = max(0, self._queued_requests - 1)
            active = self._active_requests
            queued = self._queued_requests
        logger.info(
            f"LLM cancelled before start #{request_id} {self._format_request_context(request_context)} "
            f"active={active} queued={queued} waited={time.time() - queued_at:.1f}s "
            f"error={type(error).__name__}: {error}"
        )
        await self._write_metric({
            "event": "cancelled_before_start",
            "request_id": request_id,
            "context": request_context or {},
            "wait_seconds": round(time.time() - queued_at, 3),
            "active_after": active,
            "queued_after": queued,
            "error_type": type(error).__name__,
            "error": str(error),
        })

    def _start_heartbeat(
        self,
        request_id: int,
        request_context: Optional[Dict[str, Any]],
        started_at: float,
    ) -> Optional[asyncio.Task]:
        if self.heartbeat_seconds <= 0:
            return None
        return asyncio.create_task(
            self._log_request_heartbeat(request_id, request_context, started_at)
        )

    async def _log_request_heartbeat(
        self,
        request_id: int,
        request_context: Optional[Dict[str, Any]],
        started_at: float,
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            async with self._request_lock:
                active = self._active_requests
                queued = self._queued_requests
            duration = time.time() - started_at
            logger.info(
                f"LLM running #{request_id} {self._format_request_context(request_context)} "
                f"active={active} queued={queued} duration={duration:.1f}s"
            )
            await self._write_metric({
                "event": "heartbeat",
                "request_id": request_id,
                "context": request_context or {},
                "duration_seconds": round(duration, 3),
                "active": active,
                "queued": queued,
            })

    async def _write_metric(self, record: Dict[str, Any]) -> None:
        if not self.metrics_path:
            return

        base_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider_id": self.provider_id,
            "provider_model": self.model,
            "configured_model": self.model,
            "mode": self.mode,
            "timeout_seconds": self.timeout,
            "sdk_max_retries": self.max_retries,
            "max_concurrent_requests": self.max_concurrent_requests or None,
            "trust_env_proxy": self.trust_env_proxy,
        }
        base_record.update(record)

        async with self._metrics_lock:
            await asyncio.to_thread(self._append_metric_record, base_record)

    def _append_metric_record(self, record: Dict[str, Any]) -> None:
        metrics_file = Path(self.metrics_path)
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with metrics_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    async def _create_message(self, request_context: Optional[Dict[str, Any]] = None, **kwargs):
        """Create a message under the optional global async LLM concurrency cap."""
        if not self.log_requests:
            if self._request_semaphore is None:
                return await self._client.messages.create(**kwargs)
            async with self._request_semaphore:
                return await self._client.messages.create(**kwargs)

        queued_at = time.time()
        request_id, _, _ = await self._register_queued_request(request_context)
        acquired = False
        started_at = queued_at
        heartbeat_task = None

        try:
            if self._request_semaphore is not None:
                await self._request_semaphore.acquire()
            acquired = True
            started_at = time.time()
            wait_seconds = await self._mark_request_started(request_id, request_context, queued_at)
            heartbeat_task = self._start_heartbeat(request_id, request_context, started_at)
            response = await self._client.messages.create(**kwargs)
            await self._mark_request_finished(
                request_id,
                request_context,
                started_at,
                wait_seconds,
                response=response,
            )
            return response
        except BaseException as error:
            if acquired:
                await self._mark_request_finished(
                    request_id,
                    request_context,
                    started_at,
                    wait_seconds if 'wait_seconds' in locals() else 0.0,
                    error=error,
                )
            else:
                await self._cancel_queued_request(request_id, request_context, queued_at, error)
            raise
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if acquired and self._request_semaphore is not None:
                self._request_semaphore.release()

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

    @classmethod
    def from_route_config(
        cls,
        config: 'ResolvedLLMRouteConfig',
        max_retries: Optional[int] = None
    ) -> 'AsyncAnthropicClient':
        """Create a concrete async client from a resolved route config."""
        return cls(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout,
            mode=config.mode,
            max_output_tokens=config.max_output_tokens,
            provider_id=config.id,
            max_concurrent_requests=config.max_concurrent_requests,
            max_retries=max_retries,
        )

    async def call_with_thinking(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: int = ThinkingLevel.STANDARD,
        profile: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        caller: Optional[str] = None,
        routing_context: Optional[Dict[str, Any]] = None,
        full_output_budget: bool = False
    ) -> LLMResponse:
        """Async version of call_with_thinking.

        When ``full_output_budget`` is True (used by single-shot
        ranking/summary/topic calls), the default response ceiling is raised to
        the full model output limit so that max-effort thinking cannot starve
        the visible output. Effort is never reduced. The map phase deliberately
        leaves this False because it has its own split-and-retry recovery.
        """
        requested_profile = profile if profile is not None else budget_tokens
        profile_name = THINKING_LEVEL_NAMES.get(requested_profile, str(requested_profile))
        use_adaptive = _uses_adaptive_thinking(self.model)

        if use_adaptive:
            effort = BUDGET_TO_EFFORT.get(requested_profile, "high")
            if max_tokens is None:
                # These callers get the full combined thinking+output budget up
                # front so max-effort thinking cannot clip the output.
                max_tokens = self.max_output_tokens if full_output_budget else self.adaptive_max_tokens
            manual_budget_tokens = None
        else:
            effort = None
            manual_budget_tokens = int(requested_profile)
            # Use larger buffer (49152) to avoid JSON truncation in dense batches
            # on older manual-thinking models.
            if max_tokens is None:
                max_tokens = manual_budget_tokens + 49152
            elif max_tokens <= manual_budget_tokens:
                max_tokens = manual_budget_tokens + 16384

        # Cap at model/proxy limit
        if max_tokens > self.max_output_tokens:
            logger.debug(f"Capping max_tokens from {max_tokens} to {self.max_output_tokens} (model limit)")
            max_tokens = self.max_output_tokens

        # If capping pushed max_tokens below the manual budget, reduce it too
        # (only meaningful on the manual-thinking path)
        if not use_adaptive and max_tokens <= manual_budget_tokens:
            manual_budget_tokens = max(max_tokens - 8192, max_tokens // 2)
            logger.info(
                f"Reduced manual thinking budget to {manual_budget_tokens} "
                f"to fit within {self.max_output_tokens} token limit"
            )

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        if use_adaptive:
            # Opus 4.8+ path: adaptive thinking with effort. See the sync
            # method for the thinking/output_config rationale.
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["extra_body"] = {"output_config": {"effort": effort}}
        else:
            # Opus 4.6 and earlier: manual thinking with an explicit budget.
            if temperature != 1.0:
                temperature = 1.0
            kwargs["temperature"] = temperature
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": manual_budget_tokens}

        if system:
            kwargs["system"] = system

        start_time = time.time()
        request_context = {
            "caller": caller or "async_call_with_thinking",
            "kind": "adaptive_thinking" if use_adaptive else "manual_thinking",
            "provider_id": self.provider_id,
            "provider_model": self.model,
            "thinking_type": "adaptive" if use_adaptive else "enabled",
            "analysis_profile": profile_name,
            "adaptive_effort": effort if use_adaptive else None,
            "manual_budget_tokens": manual_budget_tokens if not use_adaptive else None,
            "response_max_tokens": max_tokens,
            "message_count": len(messages),
            "message_chars": _messages_char_count(messages),
            "system_chars": _content_char_count(system),
        }
        if routing_context:
            request_context.update(routing_context)

        response = await self._create_message(request_context=request_context, **kwargs)
        duration = time.time() - start_time

        # Log stop_reason for diagnostics (helps debug proxy behavior)
        logger.debug(f"Response stop_reason: {response.stop_reason}, output_tokens: {response.usage.output_tokens}")

        # Check for truncation. Callers that pass full_output_budget already run
        # at the model's max output ceiling, so there is no larger budget to
        # grant -- we surface it loudly rather than degrading thinking effort.
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
        if not use_adaptive and manual_budget_tokens > 0 and not thinking_blocks:
            error_msg = (
                f"Extended thinking requested (budget_tokens={manual_budget_tokens}) but no thinking "
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
        get_tracker().record_call(
            caller=caller or "async_call_with_thinking",
            usage=usage,
            thinking_level=None if use_adaptive else profile_name,
            duration_seconds=duration,
            model=response.model,
            provider_id=self.provider_id,
            analysis_profile=profile_name if use_adaptive else None,
            adaptive_effort=effort if use_adaptive else None
        )

        return LLMResponse(
            content="\n".join(text_blocks),
            thinking="\n\n".join(thinking_blocks) if thinking_blocks else None,
            usage=usage,
            model=response.model,
            stop_reason=response.stop_reason,
            thinking_type="adaptive" if use_adaptive else "enabled",
            adaptive_effort=effort,
            analysis_profile=profile_name,
            thinking_block_count=len(thinking_blocks)
        )

    async def call(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        caller: Optional[str] = None,
        routing_context: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Async plain call; Opus 4.8+ still uses adaptive thinking metadata."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        use_adaptive = _uses_adaptive_thinking(self.model)
        if use_adaptive:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["extra_body"] = {"output_config": {"effort": "high"}}
        else:
            kwargs["temperature"] = temperature

        if system:
            kwargs["system"] = system

        start_time = time.time()
        request_context = {
            "caller": caller or "async_call",
            "kind": "adaptive_message" if use_adaptive else "message",
            "provider_id": self.provider_id,
            "provider_model": self.model,
            "thinking_type": "adaptive" if use_adaptive else None,
            "analysis_profile": "plain" if use_adaptive else None,
            "adaptive_effort": "high" if use_adaptive else None,
            "response_max_tokens": max_tokens,
            "message_count": len(messages),
            "message_chars": _messages_char_count(messages),
            "system_chars": _content_char_count(system),
        }
        if routing_context:
            request_context.update(routing_context)

        response = await self._create_message(request_context=request_context, **kwargs)
        duration = time.time() - start_time

        content = ""
        thinking_block_count = 0
        for block in response.content:
            if getattr(block, "type", None) == "thinking":
                thinking_block_count += 1
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
            model=response.model,
            provider_id=self.provider_id,
            analysis_profile="plain" if use_adaptive else None,
            adaptive_effort="high" if use_adaptive else None
        )

        return LLMResponse(
            content=content,
            thinking=None,
            usage=usage,
            model=response.model,
            thinking_type="adaptive" if use_adaptive else None,
            adaptive_effort="high" if use_adaptive else None,
            analysis_profile="plain" if use_adaptive else None,
            thinking_block_count=thinking_block_count
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


class AsyncLLMRouter:
    """Round-robin async LLM router with per-provider caps and failover."""

    def __init__(self, clients: List[AsyncAnthropicClient]):
        if not clients:
            raise ValueError("AsyncLLMRouter requires at least one route client")
        self.clients = clients
        self._route_lock = asyncio.Lock()
        self._next_route_index = 0
        finite_caps = [client.max_concurrent_requests for client in clients]
        self.max_total_concurrent_requests = (
            sum(finite_caps)
            if all(cap > 0 for cap in finite_caps)
            else None
        )
        logger.info(
            "AsyncLLMRouter initialized with routes=%s per_provider_caps=%s "
            "max_total_concurrent_requests=%s",
            ", ".join(f"{client.provider_id}:{client.model}" for client in clients),
            ", ".join(
                f"{client.provider_id}:{client.max_concurrent_requests or 'unlimited'}"
                for client in clients
            ),
            self.max_total_concurrent_requests or "unlimited",
        )

    @classmethod
    def from_config(cls, config: 'LLMProviderConfig') -> 'AsyncLLMRouter | AsyncAnthropicClient':
        """Create a routed client when llm.routes is configured."""
        routes = config.get_route_configs()
        if len(routes) == 1:
            return AsyncAnthropicClient.from_route_config(routes[0])

        # In routed mode, provider failover is the retry strategy. Disable SDK
        # retries so a retryable provider failure moves to another route.
        clients = [
            AsyncAnthropicClient.from_route_config(route, max_retries=0)
            for route in routes
        ]
        return cls(clients)

    async def _next_start_index(self) -> int:
        async with self._route_lock:
            start_index = self._next_route_index
            self._next_route_index = (self._next_route_index + 1) % len(self.clients)
        return start_index

    def _ordered_clients(self, start_index: int) -> List[AsyncAnthropicClient]:
        return [
            self.clients[(start_index + offset) % len(self.clients)]
            for offset in range(len(self.clients))
        ]

    @staticmethod
    def _retry_reason(error: Exception) -> Optional[str]:
        """Return retry reason for transient provider failures."""
        retryable_types = (
            httpx.TimeoutException,
            httpx.TransportError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        )
        if isinstance(error, retryable_types):
            return type(error).__name__

        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code == 429:
            return "http_429"
        if isinstance(status_code, int) and status_code >= 500:
            return f"http_{status_code}"

        return None

    async def _call_with_failover(
        self,
        method_name: str,
        call_kwargs: Dict[str, Any],
    ) -> LLMResponse:
        start_index = await self._next_start_index()
        fallback_from = None
        retry_reason = None
        last_error = None

        for attempt, client in enumerate(self._ordered_clients(start_index), start=1):
            routing_context = {
                "attempt": attempt,
                "fallback_from": fallback_from,
                "retry_reason": retry_reason,
            }
            try:
                return await getattr(client, method_name)(
                    **call_kwargs,
                    routing_context=routing_context,
                )
            except Exception as error:
                last_error = error
                reason = self._retry_reason(error)
                if reason is None or attempt >= len(self.clients):
                    raise

                logger.warning(
                    "Retrying LLM call on another provider after %s failed: %s: %s",
                    client.provider_id,
                    type(error).__name__,
                    error,
                )
                fallback_from = client.provider_id
                retry_reason = reason

        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM router had no route to call")

    async def call_with_thinking(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: int = ThinkingLevel.STANDARD,
        profile: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        caller: Optional[str] = None,
        full_output_budget: bool = False
    ) -> LLMResponse:
        """Route an adaptive/manual thinking call across configured providers."""
        return await self._call_with_failover(
            "call_with_thinking",
            {
                "messages": messages,
                "system": system,
                "budget_tokens": budget_tokens,
                "profile": profile,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "caller": caller,
                "full_output_budget": full_output_budget,
            },
        )

    async def call(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        caller: Optional[str] = None
    ) -> LLMResponse:
        """Route a plain message call across configured providers."""
        return await self._call_with_failover(
            "call",
            {
                "messages": messages,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "caller": caller,
            },
        )

    async def call_json(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        budget_tokens: Optional[int] = None,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """Route a JSON helper call across configured providers."""
        if budget_tokens:
            response = await self.call_with_thinking(
                messages=messages,
                system=system,
                budget_tokens=budget_tokens,
                max_tokens=max_tokens,
            )
        else:
            response = await self.call(
                messages=messages,
                system=system,
                max_tokens=max_tokens,
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
        """Close all routed async clients."""
        for client in self.clients:
            await client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
