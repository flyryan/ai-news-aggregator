"""YAML configuration loader with environment variable interpolation."""
import os
import re
import sys
import yaml
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from pydantic import ValidationError

from .schema import ProviderConfig

logger = logging.getLogger(__name__)

# Pattern to match ${VAR} environment variable references (legacy, for env-only resolution)
ENV_VAR_PATTERN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')

# Pattern to match both ${VAR} and ${env:VAR} patterns
# - ${VAR} = runtime variable from context dict
# - ${env:VAR} = environment variable (explicit)
VAR_PATTERN = re.compile(r'\$\{(env:)?([A-Za-z_][A-Za-z0-9_]*)\}')


class EnvVarError(Exception):
    """Raised when a referenced environment variable is not set."""
    pass


def _resolve_env_vars(value: Any, path: str = "") -> Any:
    """Recursively resolve ${VAR} patterns in YAML values.

    Args:
        value: The value to process (string, dict, list, or other)
        path: Current path in config for error messages

    Returns:
        Value with environment variables resolved

    Raises:
        EnvVarError: If a referenced environment variable is not set
    """
    if isinstance(value, str):
        matches = ENV_VAR_PATTERN.findall(value)
        if not matches:
            return value

        result = value
        for var_name in matches:
            env_value = os.environ.get(var_name)
            if env_value is None:
                raise EnvVarError(
                    f"Environment variable '{var_name}' not set "
                    f"(referenced in {path or 'config'})"
                )
            result = result.replace(f'${{{var_name}}}', env_value)
        return result

    elif isinstance(value, dict):
        return {
            k: _resolve_env_vars(v, f"{path}.{k}" if path else k)
            for k, v in value.items()
        }

    elif isinstance(value, list):
        return [
            _resolve_env_vars(item, f"{path}[{i}]")
            for i, item in enumerate(value)
        ]

    return value


def resolve_variables(
    value: Any,
    context: Dict[str, str],
    allow_missing: bool = False,
    path: str = ""
) -> Any:
    """Resolve ${var} patterns in values using runtime context.

    Supports two variable syntaxes:
    - ${VAR}: Looks up 'VAR' in the context dict
    - ${env:VAR}: Looks up 'VAR' in os.environ

    Args:
        value: The value to process (string, dict, list, or other)
        context: Runtime context dict (e.g., {"date": "2026-01-25", "category": "news"})
        allow_missing: If True, leave unresolved variables as-is instead of raising
        path: Current path in config for error messages

    Returns:
        Value with variables resolved

    Raises:
        ValueError: If a variable is not found and allow_missing is False

    Example:
        >>> resolve_variables("Hello ${name}!", {"name": "World"})
        'Hello World!'
        >>> import os; os.environ['TEST_VAR'] = 'from_env'
        >>> resolve_variables("Value: ${env:TEST_VAR}", {})
        'Value: from_env'
    """
    if isinstance(value, str):
        def replacer(match):
            is_env = match.group(1) is not None  # True if ${env:VAR}
            var_name = match.group(2)

            if is_env:
                # Environment variable lookup
                env_val = os.environ.get(var_name)
                if env_val is None:
                    if allow_missing:
                        return match.group(0)  # Leave as-is
                    raise ValueError(
                        f"Environment variable '{var_name}' not set "
                        f"(at {path or 'root'})"
                    )
                return env_val
            else:
                # Runtime context variable lookup
                if var_name in context:
                    return str(context[var_name])
                elif allow_missing:
                    return match.group(0)  # Leave as-is
                else:
                    raise ValueError(
                        f"Variable '{var_name}' not in context "
                        f"(at {path or 'root'})"
                    )

        return VAR_PATTERN.sub(replacer, value)

    elif isinstance(value, dict):
        return {
            k: resolve_variables(v, context, allow_missing, f"{path}.{k}" if path else k)
            for k, v in value.items()
        }

    elif isinstance(value, list):
        return [
            resolve_variables(item, context, allow_missing, f"{path}[{i}]")
            for i, item in enumerate(value)
        ]

    return value


def load_yaml_with_env(path: Path) -> Dict[str, Any]:
    """Load YAML file with ${VAR} environment variable interpolation.

    Args:
        path: Path to YAML file

    Returns:
        Parsed YAML dict with env vars resolved

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is malformed
        EnvVarError: If referenced env var is not set
    """
    with open(path, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        return {}

    return _resolve_env_vars(raw_config)


def _exit_with_config_instructions(config_path: Path, example_path: Path) -> None:
    """Exit with helpful instructions for creating config."""
    logger.error(f"Configuration file not found: {config_path}")
    if example_path.exists():
        logger.error(f"  Copy the example: cp {example_path} {config_path}")
    else:
        logger.error(f"  Create {config_path} with LLM provider settings")
    logger.error("  See documentation for configuration options")
    sys.exit(1)


def load_config(
    config_dir: str,
    migrate_fn: Optional[Callable[[str], bool]] = None
) -> ProviderConfig:
    """Load and validate provider configuration.

    Args:
        config_dir: Directory containing providers.yaml
        migrate_fn: Optional migration function to call if no config exists.
                    Signature: migrate_fn(config_dir: str) -> bool

    Returns:
        Validated ProviderConfig

    Raises:
        SystemExit: If config is missing or invalid (with helpful error message)
    """
    config_path = Path(config_dir) / "providers.yaml"
    example_path = Path(config_dir) / "providers.yaml.example"

    # Check if config exists
    if not config_path.exists():
        # Try migration if function provided
        if migrate_fn is not None:
            try:
                if migrate_fn(config_dir):
                    logger.info(f"Migrated environment variables to {config_path}")
                else:
                    _exit_with_config_instructions(config_path, example_path)
            except Exception as e:
                logger.error(f"Migration failed: {e}")
                _exit_with_config_instructions(config_path, example_path)
        else:
            _exit_with_config_instructions(config_path, example_path)

    # Load YAML with env var interpolation
    try:
        raw_config = load_yaml_with_env(config_path)
    except EnvVarError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load {config_path}: {e}")
        sys.exit(1)

    # Check for unrecognized top-level keys
    known_keys = {'llm', 'image', 'pipeline'}
    unknown_keys = set(raw_config.keys()) - known_keys
    if unknown_keys:
        logger.warning(f"Unrecognized config keys (ignored): {', '.join(unknown_keys)}")

    # Validate with Pydantic
    try:
        config = ProviderConfig.model_validate(raw_config)
        logger.info(f"Loaded provider config: llm.model={config.llm.model}, "
                   f"image={'enabled' if config.image else 'disabled'}")
        return config
    except ValidationError as e:
        logger.error("Configuration validation failed:")
        for err in e.errors():
            loc = '.'.join(str(l) for l in err['loc'])
            msg = err['msg']
            logger.error(f"  {loc}: {msg}")
        sys.exit(1)


# Backwards-compatible function name
_interpolate_env_vars = _resolve_env_vars
