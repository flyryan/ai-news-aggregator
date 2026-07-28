"""Pydantic models for provider and prompt configuration schema."""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, List, Optional, Literal


class LLMRouteConfig(BaseModel):
    """Optional per-route override for multi-provider LLM routing."""

    id: str = Field(..., min_length=1, description="Stable route/provider identifier")
    mode: Optional[Literal["anthropic", "openai-compatible"]] = Field(
        default=None,
        description="Override API mode for this route"
    )
    api_key: Optional[str] = Field(default=None, description="Override API key for this route")
    base_url: Optional[str] = Field(default=None, description="Override API base URL for this route")
    model: str = Field(..., min_length=1, description="Model identifier for this route")
    max_output_tokens: Optional[int] = Field(
        default=None,
        ge=1024,
        le=128000,
        description="Override maximum output tokens for this route"
    )
    timeout: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=600.0,
        description="Override request timeout for this route"
    )
    max_concurrent_requests: Optional[int] = Field(
        default=None,
        ge=0,
        description="Override per-provider async request cap; 0 disables the cap"
    )

    @field_validator('api_key')
    @classmethod
    def validate_optional_api_key(cls, v: Optional[str]) -> Optional[str]:
        """Validate route API key if explicitly configured."""
        if v is None:
            return v
        if not v or v == "your-api-key-here":
            raise ValueError("Route API key is not configured")
        if v.startswith('${') and v.endswith('}'):
            raise ValueError(
                f"Environment variable {v} was not resolved. "
                "Check that the variable is set."
            )
        return v

    @field_validator('base_url')
    @classmethod
    def validate_optional_base_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate route base_url if explicitly configured."""
        if v is None:
            return v
        if v.endswith('/v1'):
            raise ValueError(
                f"base_url should not include '/v1' suffix. "
                f"Use '{v[:-3]}' instead."
            )
        return v.rstrip('/')


class LLMProviderConfig(BaseModel):
    """Configuration for LLM provider.

    Supports two modes:
    - anthropic: Direct Anthropic API with x-api-key header authentication
    - openai-compatible: OpenAI-compatible proxy with Bearer token authentication

    Attributes:
        mode: API mode - 'anthropic' for direct API, 'openai-compatible' for proxies
        api_key: API key for authentication
        base_url: API base URL (should not include /v1 suffix)
        model: Model identifier
        timeout: Request timeout in seconds (1-600)
    """
    mode: Literal["anthropic", "openai-compatible"] = Field(
        default="anthropic",
        description="API mode: 'anthropic' for direct API, 'openai-compatible' for proxies"
    )
    api_key: str = Field(..., description="API key for authentication")
    base_url: str = Field(
        default="https://api.anthropic.com",
        description="API base URL (no /v1 suffix)"
    )
    model: str = Field(default="claude-5-opus-aws", description="Model identifier")
    max_output_tokens: int = Field(
        default=128000,
        ge=1024,
        le=128000,
        description="Maximum output tokens the model/proxy supports. "
                    "Set lower for proxies with restrictive limits (e.g., 64000)."
    )
    timeout: float = Field(default=600.0, ge=1.0, le=600.0, description="Request timeout in seconds")
    routes: Optional[List[LLMRouteConfig]] = Field(
        default=None,
        description="Optional route list for multi-provider LLM routing"
    )

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate API key is configured and resolved."""
        if not v or v == "your-api-key-here":
            raise ValueError(
                "API key not configured. Set a valid key in config/providers.yaml"
            )
        if v.startswith('${') and v.endswith('}'):
            raise ValueError(
                f"Environment variable {v} was not resolved. "
                "Check that the variable is set."
            )
        return v

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        """Validate base_url doesn't have /v1 suffix."""
        if v.endswith('/v1'):
            raise ValueError(
                f"base_url should not include '/v1' suffix. "
                f"Use '{v[:-3]}' instead."
            )
        return v.rstrip('/')

    @model_validator(mode='after')
    def validate_routes(self) -> 'LLMProviderConfig':
        """Validate optional multi-provider route configuration."""
        if self.routes is None:
            return self
        if not self.routes:
            raise ValueError("llm.routes must not be empty when configured")
        route_ids = [route.id for route in self.routes]
        duplicates = sorted({route_id for route_id in route_ids if route_ids.count(route_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate llm.routes id(s): {', '.join(duplicates)}")
        return self

    def get_route_configs(self) -> List['ResolvedLLMRouteConfig']:
        """Return concrete route configs, inheriting root LLM settings."""
        if not self.routes:
            return [
                ResolvedLLMRouteConfig(
                    id=self.model,
                    mode=self.mode,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    timeout=self.timeout,
                    max_concurrent_requests=None,
                )
            ]

        return [
            ResolvedLLMRouteConfig(
                id=route.id,
                mode=route.mode or self.mode,
                api_key=route.api_key or self.api_key,
                base_url=route.base_url or self.base_url,
                model=route.model,
                max_output_tokens=route.max_output_tokens or self.max_output_tokens,
                timeout=route.timeout or self.timeout,
                max_concurrent_requests=route.max_concurrent_requests,
            )
            for route in self.routes
        ]


class ResolvedLLMRouteConfig(BaseModel):
    """Concrete LLM provider route after inheritance from root llm config."""

    id: str
    mode: Literal["anthropic", "openai-compatible"]
    api_key: str
    base_url: str
    model: str
    max_output_tokens: int
    timeout: float
    max_concurrent_requests: Optional[int] = None


class ImageProviderConfig(BaseModel):
    """Configuration for image generation provider (optional).

    Supports two modes:
    - native: Uses google-genai SDK directly (recommended for Google API keys)
    - openai-compatible: Uses REST chat/completions format (for LiteLLM proxies)

    Attributes:
        mode: API mode - 'native' for google-genai SDK, 'openai-compatible' for REST
        api_key: API key for image generation service
        endpoint: API endpoint URL (required for openai-compatible mode only)
        model: Model name for image generation
    """
    mode: Literal["native", "openai-compatible"] = Field(
        default="native",
        description="API mode: 'native' for google-genai SDK, 'openai-compatible' for REST"
    )
    api_key: str = Field(..., description="API key for image generation")
    endpoint: Optional[str] = Field(
        default=None,
        description="API endpoint URL (required for openai-compatible mode)"
    )
    model: str = Field(
        default="gemini-3-pro-image-preview",
        description="Model name for image generation"
    )

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate image API key is configured and resolved."""
        if not v or v == "your-image-api-key":
            raise ValueError(
                "Image API key not configured. "
                "Set a valid key or remove image section from config."
            )
        if v.startswith('${') and v.endswith('}'):
            raise ValueError(
                f"Environment variable {v} was not resolved. "
                "Check that the variable is set."
            )
        return v

    @model_validator(mode='after')
    def validate_endpoint_for_mode(self) -> 'ImageProviderConfig':
        """Validate endpoint is provided for openai-compatible mode."""
        if self.mode == "openai-compatible" and not self.endpoint:
            raise ValueError(
                "endpoint is required when mode is 'openai-compatible'. "
                "Provide your proxy's image generation endpoint URL."
            )
        return self


class PipelineConfig(BaseModel):
    """Configuration for pipeline settings.

    Attributes:
        base_url: Base URL for RSS feed links (e.g., https://your-domain.com)
        lookback_hours: Data collection window in hours (default: 24)
    """
    base_url: str = Field(
        default="https://news.aatf.ai",
        description="Base URL for RSS feed links. Set to your deployment domain."
    )
    lookback_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Data collection window in hours (1-168)"
    )

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        """Validate and normalize base_url."""
        if not v:
            raise ValueError("base_url cannot be empty")
        # Remove trailing slash for consistency
        return v.rstrip('/')

    model_config = {"extra": "ignore"}


class ProviderConfig(BaseModel):
    """Root configuration schema for all providers.

    Attributes:
        llm: LLM provider configuration (required)
        image: Image generation provider configuration (optional)
        pipeline: Pipeline settings (optional, has sensible defaults)
    """
    llm: LLMProviderConfig
    image: Optional[ImageProviderConfig] = None  # Optional - hero gen disabled if missing
    pipeline: Optional[PipelineConfig] = None  # Optional - defaults if missing

    model_config = {"extra": "ignore"}  # Warn but don't error on unknown keys

    def get_pipeline_config(self) -> PipelineConfig:
        """Get pipeline config, returning defaults if not specified."""
        return self.pipeline or PipelineConfig()


# Backwards-compatible aliases
LLMConfig = LLMProviderConfig
ImageConfig = ImageProviderConfig


# =============================================================================
# Prompt Configuration Schema
# =============================================================================


class AnalyzerPrompts(BaseModel):
    """Prompts for a single category analyzer.

    All analyzers use batch_analysis and ranking prompts. The filter and
    combined_analysis prompts are optional and only used by some analyzers.

    Attributes:
        batch_analysis: Map phase prompt for analyzing batches of items
        ranking: Reduce phase prompt for ranking analyzed items
        filter: Optional LLM filter prompt (only news analyzer uses this)
        combined_analysis: Optional small batch optimization prompt
        analysis: Legacy prompt (kept for reference during migration)
    """
    batch_analysis: str = Field(
        ...,
        min_length=10,
        description="Map phase prompt for batch analysis"
    )
    ranking: str = Field(
        ...,
        min_length=10,
        description="Reduce phase prompt for ranking items"
    )
    filter: Optional[str] = Field(
        default=None,
        description="Optional LLM filter prompt (only news analyzer)"
    )
    combined_analysis: Optional[str] = Field(
        default=None,
        description="Optional small batch optimization prompt"
    )
    analysis: Optional[str] = Field(
        default=None,
        description="Legacy prompt (kept for reference)"
    )

    model_config = {"extra": "ignore"}


class GatheringPrompts(BaseModel):
    """Prompts for the gathering phase.

    Attributes:
        link_relevance: Prompt for link follower to decide which URLs to fetch
    """
    link_relevance: str = Field(
        ...,
        min_length=10,
        description="Link follower decision prompt"
    )

    model_config = {"extra": "ignore"}


class OrchestrationPrompts(BaseModel):
    """Prompts for the orchestration phase.

    Attributes:
        topic_detection: Cross-category topic detection prompt
        executive_summary: Executive summary generation prompt
    """
    topic_detection: str = Field(
        ...,
        min_length=10,
        description="Cross-category topic detection prompt"
    )
    executive_summary: str = Field(
        ...,
        min_length=10,
        description="Executive summary generation prompt"
    )

    model_config = {"extra": "ignore"}


class PostProcessingPrompts(BaseModel):
    """Prompts for post-processing phase.

    Attributes:
        link_enrichment: Prompt for adding internal links to summaries
        ecosystem_enrichment: Prompt for detecting new model releases
    """
    link_enrichment: str = Field(
        ...,
        min_length=10,
        description="Link enrichment prompt"
    )
    ecosystem_enrichment: str = Field(
        ...,
        min_length=10,
        description="Ecosystem enrichment prompt"
    )

    model_config = {"extra": "ignore"}


class PromptConfig(BaseModel):
    """Root configuration schema for all prompts.

    Organizes prompts by pipeline phase:
    - gathering: Prompts used during data collection
    - analysis: Category-specific analysis prompts (keyed by category name)
    - orchestration: Cross-category and summary prompts
    - post_processing: Enrichment and enhancement prompts

    Attributes:
        gathering: Gathering phase prompts
        analysis: Dict of category name to AnalyzerPrompts
        orchestration: Orchestration phase prompts
        post_processing: Post-processing phase prompts
    """
    gathering: GatheringPrompts
    analysis: Dict[str, AnalyzerPrompts]
    orchestration: OrchestrationPrompts
    post_processing: PostProcessingPrompts

    model_config = {"extra": "ignore"}

    @model_validator(mode='after')
    def validate_required_categories(self) -> 'PromptConfig':
        """Validate that all required analysis categories are present."""
        required_categories = {'news', 'research', 'social', 'reddit'}
        present_categories = set(self.analysis.keys())
        missing = required_categories - present_categories
        if missing:
            raise ValueError(
                f"Missing required analysis categories: {', '.join(sorted(missing))}. "
                f"Each category needs batch_analysis and ranking prompts."
            )
        return self
