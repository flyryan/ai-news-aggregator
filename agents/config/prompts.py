"""Prompt configuration loader and accessor.

This module provides loading and typed access to LLM prompts from YAML
configuration, with support for runtime variable substitution.

Example usage:
    from agents.config.prompts import load_prompts, PromptAccessor

    # Load prompts at startup
    config = load_prompts("./config")

    # Create accessor for runtime resolution
    prompts = PromptAccessor(config)

    # Get a prompt with context variables resolved
    prompt = prompts.get_analyzer_prompt(
        "news", "batch_analysis",
        {"batch_index": "1", "total_batches": "5", "items_context": "..."}
    )
"""
import sys
import yaml
import logging
from pathlib import Path
from typing import Any, Dict
from pydantic import ValidationError

from .loader import resolve_variables
from .schema import PromptConfig

logger = logging.getLogger(__name__)


def load_prompts(config_dir: str) -> PromptConfig:
    """Load and validate prompts configuration.

    Loads prompts.yaml from the specified directory and validates the structure
    with Pydantic. Runtime variables (${var}) are preserved as-is for later
    resolution by PromptAccessor. Only ${env:VAR} patterns would be resolved
    at load time, but prompts typically don't use environment variables.

    Args:
        config_dir: Directory containing prompts.yaml

    Returns:
        Validated PromptConfig

    Raises:
        SystemExit: If prompts.yaml is missing or invalid (with helpful error message)
    """
    prompts_path = Path(config_dir) / "prompts.yaml"

    if not prompts_path.exists():
        logger.error(f"Prompts file not found: {prompts_path}")
        logger.error("  This file contains all LLM prompts for the pipeline.")
        logger.error("  Restore from git or recreate from documentation.")
        sys.exit(1)

    try:
        # Load YAML without env var resolution - prompts contain ${var} placeholders
        # that should be resolved at runtime, not at load time
        with open(prompts_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        if raw_config is None:
            raw_config = {}
    except yaml.YAMLError as e:
        logger.error(f"YAML parse error in {prompts_path}: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load {prompts_path}: {e}")
        sys.exit(1)

    try:
        config = PromptConfig.model_validate(raw_config)
        logger.info(f"Loaded prompts config from {prompts_path}")
        return config
    except ValidationError as e:
        logger.error("Prompts validation failed:")
        for err in e.errors():
            loc = '.'.join(str(l) for l in err['loc'])
            msg = err['msg']
            logger.error(f"  {loc}: {msg}")
        sys.exit(1)


class PromptAccessor:
    """Provides typed access to prompts with runtime variable resolution.

    This class wraps a PromptConfig and provides methods to retrieve prompts
    for each pipeline phase, automatically resolving runtime variables using
    the provided context dict.

    Example:
        config = load_prompts("./config")
        prompts = PromptAccessor(config)

        # Get analysis prompt with variables
        prompt = prompts.get_analyzer_prompt(
            "news", "batch_analysis",
            {"batch_index": "1", "total_batches": "3"}
        )

        # Get orchestration prompt
        prompt = prompts.get_orchestration_prompt(
            "topic_detection",
            {"context": "...reports..."}
        )
    """

    def __init__(self, config: PromptConfig):
        """Initialize the accessor with a validated config.

        Args:
            config: Validated PromptConfig instance
        """
        self.config = config

    def get_analyzer_prompt(
        self,
        category: str,
        prompt_type: str,
        context: Dict[str, Any]
    ) -> str:
        """Get an analysis prompt with variables resolved.

        Args:
            category: Category name ('news', 'research', 'social', 'reddit')
            prompt_type: Prompt type ('batch_analysis', 'ranking', 'filter', 'combined_analysis')
            context: Runtime variables to substitute

        Returns:
            Resolved prompt string

        Raises:
            ValueError: If category or prompt_type is unknown
        """
        if category not in self.config.analysis:
            raise ValueError(
                f"Unknown category: '{category}'. "
                f"Valid categories: {', '.join(self.config.analysis.keys())}"
            )

        analyzer_prompts = self.config.analysis[category]
        prompt_template = getattr(analyzer_prompts, prompt_type, None)

        if prompt_template is None:
            valid_types = ['batch_analysis', 'ranking', 'filter', 'combined_analysis', 'analysis']
            raise ValueError(
                f"Unknown prompt type: '{prompt_type}' for category '{category}'. "
                f"Valid types: {', '.join(valid_types)}"
            )

        return resolve_variables(
            prompt_template,
            {k: str(v) for k, v in context.items()},
            path=f"analysis.{category}.{prompt_type}"
        )

    def get_gathering_prompt(
        self,
        prompt_type: str,
        context: Dict[str, Any]
    ) -> str:
        """Get a gathering phase prompt with variables resolved.

        Args:
            prompt_type: Prompt type ('link_relevance')
            context: Runtime variables to substitute

        Returns:
            Resolved prompt string

        Raises:
            ValueError: If prompt_type is unknown
        """
        prompt_template = getattr(self.config.gathering, prompt_type, None)

        if prompt_template is None:
            raise ValueError(
                f"Unknown gathering prompt type: '{prompt_type}'. "
                f"Valid types: link_relevance"
            )

        return resolve_variables(
            prompt_template,
            {k: str(v) for k, v in context.items()},
            path=f"gathering.{prompt_type}"
        )

    def get_orchestration_prompt(
        self,
        prompt_type: str,
        context: Dict[str, Any]
    ) -> str:
        """Get an orchestration phase prompt with variables resolved.

        Args:
            prompt_type: Prompt type ('topic_detection', 'executive_summary')
            context: Runtime variables to substitute

        Returns:
            Resolved prompt string

        Raises:
            ValueError: If prompt_type is unknown
        """
        prompt_template = getattr(self.config.orchestration, prompt_type, None)

        if prompt_template is None:
            raise ValueError(
                f"Unknown orchestration prompt type: '{prompt_type}'. "
                f"Valid types: topic_detection, executive_summary"
            )

        return resolve_variables(
            prompt_template,
            {k: str(v) for k, v in context.items()},
            path=f"orchestration.{prompt_type}"
        )

    def get_post_processing_prompt(
        self,
        prompt_type: str,
        context: Dict[str, Any]
    ) -> str:
        """Get a post-processing phase prompt with variables resolved.

        Args:
            prompt_type: Prompt type ('link_enrichment', 'ecosystem_enrichment')
            context: Runtime variables to substitute

        Returns:
            Resolved prompt string

        Raises:
            ValueError: If prompt_type is unknown
        """
        prompt_template = getattr(self.config.post_processing, prompt_type, None)

        if prompt_template is None:
            raise ValueError(
                f"Unknown post_processing prompt type: '{prompt_type}'. "
                f"Valid types: link_enrichment, ecosystem_enrichment"
            )

        return resolve_variables(
            prompt_template,
            {k: str(v) for k, v in context.items()},
            path=f"post_processing.{prompt_type}"
        )
