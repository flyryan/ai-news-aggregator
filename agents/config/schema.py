"""Pydantic models for provider and prompt configuration schema."""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, Optional, Literal


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
    model: str = Field(default="claude-opus-4-6", description="Model identifier")
    max_output_tokens: int = Field(
        default=128000,
        ge=1024,
        le=128000,
        description="Maximum output tokens the model/proxy supports. "
                    "Set lower for proxies with restrictive limits (e.g., 64000)."
    )
    timeout: float = Field(default=300.0, ge=1.0, le=600.0, description="Request timeout in seconds")

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
        default="http://localhost:8080",
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
