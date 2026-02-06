"""Provider and prompt configuration module.

This module provides YAML-based configuration loading for LLM providers,
image providers, and LLM prompts, with environment variable interpolation
and Pydantic validation.

Example usage:
    from agents.config import load_config, load_prompts, PromptAccessor

    # Load provider config
    config = load_config("./config")
    print(config.llm.model)  # claude-opus-4-6

    # Load prompts with typed accessor
    prompt_config = load_prompts("./config")
    prompts = PromptAccessor(prompt_config)
    prompt = prompts.get_analyzer_prompt("news", "batch_analysis", {"batch_index": "1"})

For migration from env vars:
    from agents.config import migrate_from_env

    if migrate_from_env("./config"):
        print("Migrated env vars to providers.yaml")
"""
from .schema import (
    ProviderConfig,
    LLMProviderConfig,
    ImageProviderConfig,
    LLMConfig,  # Backwards-compatible alias
    ImageConfig,  # Backwards-compatible alias
    # Prompt configuration schema
    PromptConfig,
    AnalyzerPrompts,
    GatheringPrompts,
    OrchestrationPrompts,
    PostProcessingPrompts,
)
from .loader import load_yaml_with_env, EnvVarError, resolve_variables
from .loader import load_config as _load_config_base
from .migration import migrate_from_env, detect_env_vars
from .prompts import load_prompts, PromptAccessor
import logging
from pathlib import Path

__all__ = [
    # Provider config
    'ProviderConfig',
    'LLMProviderConfig',
    'ImageProviderConfig',
    'LLMConfig',  # Backwards-compatible alias
    'ImageConfig',  # Backwards-compatible alias
    'load_config',
    'load_yaml_with_env',
    'EnvVarError',
    'migrate_from_env',
    'detect_env_vars',
    # Prompt config
    'PromptConfig',
    'AnalyzerPrompts',
    'GatheringPrompts',
    'OrchestrationPrompts',
    'PostProcessingPrompts',
    'load_prompts',
    'PromptAccessor',
    'resolve_variables',
]

logger = logging.getLogger(__name__)


def load_config(config_dir: str, auto_migrate: bool = True) -> ProviderConfig:
    """Load and validate provider configuration.

    This is a convenience wrapper around loader.load_config that integrates
    automatic migration from environment variables.

    Args:
        config_dir: Directory containing providers.yaml
        auto_migrate: If True, attempt to migrate from env vars if no config exists

    Returns:
        Validated ProviderConfig

    Raises:
        SystemExit: If config is missing or invalid (with helpful error message)
    """
    config_path = Path(config_dir) / "providers.yaml"
    env_path = Path(config_dir).parent / ".env"

    # Warn if .env exists alongside providers.yaml
    if config_path.exists() and env_path.exists():
        logger.warning(
            f"Both providers.yaml and .env exist. "
            f"Using providers.yaml, environment variables in .env are ignored for provider settings."
        )

    # Use migration function if auto_migrate is enabled
    migrate_fn = migrate_from_env if auto_migrate else None
    return _load_config_base(config_dir, migrate_fn=migrate_fn)
