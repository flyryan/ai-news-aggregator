"""Migration from environment variables to YAML configuration."""
import os
import logging
from pathlib import Path
from datetime import datetime

import yaml

logger = logging.getLogger(__name__)

# Environment variables to detect and migrate
ENV_VAR_MAPPING = {
    # LLM provider
    'ANTHROPIC_API_KEY': ('llm', 'api_key'),
    'ANTHROPIC_API_BASE': ('llm', 'base_url'),
    'ANTHROPIC_MODEL': ('llm', 'model'),
    # Image provider
    'GEMINI_API_KEY': ('image', 'api_key'),
}


def detect_env_vars() -> dict:
    """
    Detect existing environment variables for provider config.

    Returns:
        Dict with detected config values, or empty dict if none found
    """
    config = {'llm': {}, 'image': {}}
    found_any = False

    for env_var, (section, key) in ENV_VAR_MAPPING.items():
        value = os.environ.get(env_var)
        if value:
            config[section][key] = value
            found_any = True
            logger.debug(f"Detected {env_var}")

    # Remove empty sections
    if not config['llm']:
        del config['llm']
    if not config['image']:
        del config['image']

    return config if found_any else {}


def migrate_from_env(config_dir: str) -> bool:
    """
    Migrate from environment variables to YAML configuration.

    Creates providers.yaml with ${VAR} references that resolve from env vars.

    Args:
        config_dir: Directory to write providers.yaml

    Returns:
        True if migration was performed, False if no env vars detected
    """
    config_path = Path(config_dir) / "providers.yaml"
    env_path = Path(config_dir).parent / ".env"  # .env is typically in project root

    # Don't overwrite existing config
    if config_path.exists():
        if env_path.exists():
            logger.warning(
                f"Both {config_path} and .env exist. "
                f"Using {config_path}, .env settings are ignored."
            )
        return True  # Config exists, no migration needed

    # Detect env vars
    detected = detect_env_vars()
    if not detected:
        logger.debug("No provider environment variables detected")
        return False

    # Validate we have minimum required config
    if 'llm' not in detected or 'api_key' not in detected.get('llm', {}):
        logger.warning("ANTHROPIC_API_KEY not found, cannot migrate")
        return False

    # Determine which optional fields are set
    has_custom_base_url = 'base_url' in detected.get('llm', {})
    has_custom_model = 'model' in detected.get('llm', {})

    # If ANTHROPIC_API_BASE is set, user is using a proxy (openai-compatible mode)
    # This is the common case for internal Trend users
    using_proxy = has_custom_base_url

    # Determine values based on mode
    llm_mode = 'openai-compatible' if using_proxy else 'anthropic'
    model_value = '${ANTHROPIC_MODEL}' if has_custom_model else 'claude-opus-4-5-20251101'

    # For proxy users: image uses same endpoint/key as LLM
    # For direct API users: image needs separate Gemini config
    image_mode = 'openai-compatible' if using_proxy else 'native'
    image_api_key = '${ANTHROPIC_API_KEY}' if using_proxy else 'your-google-api-key-here'
    image_model = 'gemini-3-pro-image' if using_proxy else 'gemini-3-pro-image-preview'

    # Write YAML config
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Build YAML content
    # For proxy users: openai-compatible mode with shared endpoint/key
    # For direct API users: anthropic mode for LLM, native mode for image
    yaml_content = f'''# Provider Configuration for AI News Aggregator
# Auto-generated from environment variables on {datetime.now().isoformat()}
#
# Tip: You can use environment variable interpolation with ${{VAR}} syntax:
#   api_key: "${{ANTHROPIC_API_KEY}}"
# This lets you keep secrets in .env while using this config structure.

# =============================================================================
# LLM Provider (required)
# =============================================================================
llm:
  # Mode: "anthropic" or "openai-compatible"
  #
  # anthropic (default):
  #   - Direct Anthropic API access
  #   - Uses x-api-key header authentication
  #   - Full extended thinking support (QUICK/STANDARD/DEEP/ULTRATHINK)
  #
  # openai-compatible:
  #   - For LiteLLM, AWS Bedrock proxies, or other OpenAI-compatible endpoints
  #   - Uses Bearer token authentication
  #   - Extended thinking will log warnings (proxy may not support it)
  #
  mode: "{llm_mode}"

  # API key for authentication
  api_key: "${{ANTHROPIC_API_KEY}}"

  # API base URL (no /v1 suffix)
  # Uncomment to override the default (https://api.anthropic.com)
{('  base_url: "${ANTHROPIC_API_BASE}"' if using_proxy else '  # base_url: "https://your-proxy.example.com"')}

  # Model identifier
  # - Direct Anthropic: claude-opus-4-5-20251101, claude-sonnet-4-20250514, etc.
  # - Proxy: whatever model name your proxy expects
  model: "{model_value}"

  # Request timeout in seconds (1-600)
  timeout: 300

# =============================================================================
# Image Provider (hero image generation)
# =============================================================================
# Generates a daily hero image featuring the AATF skunk mascot.
# Comment out this entire section to skip hero image generation.
#
image:
  # Mode: "native" or "openai-compatible"
  #
  # native (default):
  #   - Uses google-genai SDK directly
  #   - Requires Google AI API key
  #   - Model: gemini-3-pro-image-preview
  #   - No endpoint needed (SDK handles it)
  #
  # openai-compatible:
  #   - Uses REST chat/completions format
  #   - For LiteLLM or other proxies that wrap Gemini
  #   - Requires endpoint URL
  #   - Model name depends on your proxy (e.g., gemini-3-pro-image)
  #
  mode: "{image_mode}"

  # API key
  api_key: "{image_api_key}"

  # Endpoint (required for openai-compatible mode only)
{('  endpoint: "${ANTHROPIC_API_BASE}/v1"' if using_proxy else '  # endpoint: "https://your-proxy.example.com/v1"')}

  # Model name
  # - native: gemini-3-pro-image-preview
  # - openai-compatible: depends on your proxy (e.g., gemini-3-pro-image)
  model: "{image_model}"
'''

    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)

    logger.info(f"Created {config_path} from environment variables")
    # Note: .env file is kept in place since YAML uses ${VAR} references

    return True
