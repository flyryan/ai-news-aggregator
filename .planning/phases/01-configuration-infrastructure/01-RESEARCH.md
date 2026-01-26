# Phase 1: Configuration Infrastructure - Research

**Researched:** 2026-01-24
**Domain:** Python YAML configuration with validation and environment variable interpolation
**Confidence:** HIGH

## Summary

This phase establishes a YAML-based configuration system for LLM and image provider settings, replacing the current scattered env var approach. The research confirms that **Pydantic + PyYAML** is the standard stack for this pattern in Python, with pydantic-settings providing built-in YAML support.

The project already has PyYAML installed (`PyYAML>=6.0`). Adding `pydantic` and `pydantic-settings` provides type-safe validation with excellent error messages. Environment variable interpolation (`${VAR}` syntax) requires a custom YAML loader since it's not native to YAML or pydantic-settings.

**Primary recommendation:** Use Pydantic BaseSettings with a custom YAML loader that supports `${VAR}` syntax for optional env var interpolation. Keep the config schema simple and flat where possible.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | >=2.12 | Schema definition, validation | Industry standard for Python data validation; type-safe, excellent error messages |
| pydantic-settings | >=2.12 | Settings management | Official Pydantic companion; supports YAML via YamlConfigSettingsSource |
| PyYAML | >=6.0 | YAML parsing | Already installed; the standard Python YAML library |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | >=1.0.0 | .env file loading | Already installed; used during migration detection |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pydantic-settings | omegaconf | OmegaConf has built-in interpolation but less validation; pydantic is more Pythonic |
| Custom YAML loader | piny/envyaml | External libs add dependencies; custom is ~30 lines and fits our exact needs |
| pydantic | attrs + cattrs | attrs is lighter but pydantic has better ecosystem and error messages |

**Installation:**
```bash
pip install pydantic>=2.12 pydantic-settings>=2.12
```

Note: PyYAML and python-dotenv already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
config/
├── providers.yaml           # User config (gitignored)
├── providers.yaml.example   # Template (committed)
├── rss_feeds.txt            # Feed lists stay as .txt
├── twitter_accounts.txt
└── ...

agents/
├── config/
│   ├── __init__.py          # Exports load_config(), ProviderConfig
│   ├── schema.py            # Pydantic models for config schema
│   ├── loader.py            # YAML loading with env var interpolation
│   └── migration.py         # Auto-migrate env vars to YAML
└── ...
```

### Pattern 1: Pydantic Settings with YAML
**What:** Use Pydantic BaseModel for config schema, load from YAML with custom loader
**When to use:** Config needs validation, type safety, and clear error messages
**Example:**
```python
# Source: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal

class LLMProviderConfig(BaseModel):
    """Configuration for an LLM provider."""
    api_key: str = Field(..., description="API key for authentication")
    base_url: str = Field(..., description="API base URL")
    model: str = Field(default="claude-opus-4-5-20251101", description="Model identifier")
    timeout: float = Field(default=300.0, ge=1.0, le=600.0)

class ImageProviderConfig(BaseModel):
    """Configuration for image generation provider."""
    api_key: str
    endpoint: str = "https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions"
    model: str = "gemini-3-pro-image"

class ProviderConfig(BaseModel):
    """Root configuration schema."""
    llm: LLMProviderConfig
    image: Optional[ImageProviderConfig] = None  # Optional, hero gen disabled if missing
```

### Pattern 2: Custom YAML Loader with Env Var Interpolation
**What:** Extend PyYAML SafeLoader to resolve `${VAR}` patterns
**When to use:** When YAML values need to reference environment variables
**Example:**
```python
# Source: https://gist.github.com/mkaranasou/ba83e25c835a8f7629e34dd7ede01931
import os
import re
import yaml

def load_yaml_with_env(path: str) -> dict:
    """Load YAML with ${VAR} environment variable interpolation."""
    pattern = re.compile(r'\$\{(\w+)\}')

    loader = yaml.SafeLoader
    loader.add_implicit_resolver('!env', pattern, None)

    def env_constructor(loader, node):
        value = loader.construct_scalar(node)
        matches = pattern.findall(value)
        for var_name in matches:
            env_value = os.environ.get(var_name)
            if env_value is None:
                raise ValueError(f"Environment variable '{var_name}' not set")
            value = value.replace(f'${{{var_name}}}', env_value)
        return value

    loader.add_constructor('!env', env_constructor)

    with open(path) as f:
        return yaml.load(f, Loader=loader)
```

### Pattern 3: Collect All Validation Errors
**What:** Pydantic collects errors across fields, display all at once
**When to use:** Startup validation to show user all problems, not just the first
**Example:**
```python
# Source: https://docs.pydantic.dev/latest/errors/errors/
from pydantic import ValidationError

try:
    config = ProviderConfig.model_validate(yaml_data)
except ValidationError as e:
    print("Configuration errors found:")
    for error in e.errors():
        loc = '.'.join(str(l) for l in error['loc'])
        msg = error['msg']
        print(f"  - {loc}: {msg}")
    sys.exit(1)
```

### Pattern 4: Auto-Migration from Env Vars
**What:** Detect env vars and generate YAML config automatically
**When to use:** First run when no providers.yaml exists but env vars are set
**Example:**
```python
def detect_and_migrate() -> Optional[dict]:
    """Detect existing env vars and return equivalent config dict."""
    env_mapping = {
        'ANTHROPIC_API_KEY': ('llm', 'api_key'),
        'ANTHROPIC_API_BASE': ('llm', 'base_url'),
        'ANTHROPIC_MODEL': ('llm', 'model'),
    }

    config = {'llm': {}}
    found_any = False

    for env_var, (section, key) in env_mapping.items():
        value = os.environ.get(env_var)
        if value:
            config[section][key] = value
            found_any = True

    return config if found_any else None
```

### Anti-Patterns to Avoid
- **Mixing env var fallbacks with YAML:** User decision is YAML-only; don't create two sources of truth
- **Validating lazily:** Validate at startup, not when config is first accessed
- **Hiding errors:** Show all validation errors at once, not just the first
- **Complex nesting:** Keep schema flat where possible; deep nesting complicates error messages

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Config validation | Custom validation functions | Pydantic models | Type coercion, nested validation, excellent error messages |
| Error collection | Try/catch chains | Pydantic ValidationError | Automatically collects all field errors |
| YAML parsing | String manipulation | PyYAML safe_load | Security (no code execution), handles all YAML edge cases |
| Type coercion | Manual int(), str() | Pydantic Field types | Handles edge cases, clear error messages |

**Key insight:** Pydantic's value is not just validation but clear, actionable error messages. A custom solution will produce worse errors.

## Common Pitfalls

### Pitfall 1: Unsafe YAML Loading
**What goes wrong:** Using `yaml.load()` without Loader allows arbitrary code execution
**Why it happens:** Default behavior changed between PyYAML versions
**How to avoid:** Always use `yaml.safe_load()` or `yaml.load(f, Loader=yaml.SafeLoader)`
**Warning signs:** DeprecationWarning about Loader argument

### Pitfall 2: Env Var Interpolation Missing Variable
**What goes wrong:** `${VAR}` in YAML but VAR not set leads to literal string or silent failure
**Why it happens:** No default error handling in custom loaders
**How to avoid:** Raise clear error: "Environment variable 'VAR' not set (referenced in providers.yaml)"
**Warning signs:** Config value looks like `${ANTHROPIC_API_KEY}` at runtime

### Pitfall 3: Pydantic Validates Defaults
**What goes wrong:** `Field(default="invalid")` raises error even if user provides valid value
**Why it happens:** Pydantic BaseSettings validates defaults by default (unlike BaseModel)
**How to avoid:** Use `model_config = SettingsConfigDict(validate_default=False)` or ensure defaults are valid
**Warning signs:** Validation errors for fields user didn't configure

### Pitfall 4: YAML Gotchas
**What goes wrong:** `yes`, `no`, `on`, `off` parsed as booleans; `3.14` parsed as float
**Why it happens:** YAML spec interprets bare values
**How to avoid:** Quote strings that could be misinterpreted: `model: "gpt-4"` not `model: gpt-4`
**Warning signs:** Boolean where string expected, or number parsing errors

### Pitfall 5: Docker Config Not Updating
**What goes wrong:** Config changes require container rebuild
**Why it happens:** Config baked into image instead of mounted as volume
**How to avoid:** docker-compose.yml already mounts `./config:/app/config`; no change needed
**Warning signs:** Config changes not taking effect without `docker-compose build`

### Pitfall 6: Migration Clobbers User Config
**What goes wrong:** Auto-migration overwrites user's manually created providers.yaml
**Why it happens:** Migration runs without checking if file exists
**How to avoid:** Only migrate if providers.yaml doesn't exist; warn if both exist
**Warning signs:** User's custom config disappears

## Code Examples

Verified patterns from official sources:

### Loading and Validating Config
```python
# Full config loading workflow
from pathlib import Path
from pydantic import ValidationError
import yaml
import sys

def load_config(config_dir: str) -> ProviderConfig:
    """Load and validate provider configuration."""
    config_path = Path(config_dir) / "providers.yaml"

    if not config_path.exists():
        # Try auto-migration
        migrated = detect_and_migrate()
        if migrated:
            # Write migrated config
            with open(config_path, 'w') as f:
                yaml.safe_dump(migrated, f, default_flow_style=False)
            print(f"Migrated env vars to {config_path}")
            # Backup .env if exists
            env_path = Path(config_dir).parent / ".env"
            if env_path.exists():
                env_path.rename(env_path.with_suffix('.env.backup'))
        else:
            print(f"Error: {config_path} not found and no env vars detected")
            print(f"Copy providers.yaml.example to providers.yaml and configure")
            sys.exit(1)

    # Load with env var interpolation
    raw_config = load_yaml_with_env(str(config_path))

    # Validate
    try:
        return ProviderConfig.model_validate(raw_config)
    except ValidationError as e:
        print("Configuration errors:")
        for err in e.errors():
            loc = '.'.join(str(l) for l in err['loc'])
            print(f"  {loc}: {err['msg']}")
        sys.exit(1)
```

### Example providers.yaml.example
```yaml
# Provider Configuration for AI News Aggregator
# Copy this file to providers.yaml and fill in your values

# LLM Provider (required)
llm:
  # API key for authentication
  api_key: "your-api-key-here"

  # API base URL (no /v1 suffix)
  base_url: "https://api.anthropic.com"

  # Model identifier
  model: "claude-opus-4-5-20251101"

  # Request timeout in seconds (1-600)
  timeout: 300

# Image Provider (optional - hero image generation)
# Comment out this section to disable hero images
image:
  api_key: "your-image-api-key"
  endpoint: "https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions"
  model: "gemini-3-pro-image"

# Environment variable interpolation (advanced)
# You can reference environment variables with ${VAR} syntax:
#   api_key: ${ANTHROPIC_API_KEY}
# This is optional - direct values are preferred for clarity
```

### Helpful Error Messages
```python
# Custom validator for better error messages
from pydantic import field_validator

class LLMProviderConfig(BaseModel):
    api_key: str
    base_url: str

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v):
        if not v or v == "your-api-key-here":
            raise ValueError(
                "API key not configured. "
                "Set a valid key in config/providers.yaml"
            )
        if v.startswith('${') and v.endswith('}'):
            raise ValueError(
                f"Environment variable {v} was not resolved. "
                "Check that the variable is set."
            )
        return v

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v):
        if v.endswith('/v1'):
            raise ValueError(
                "base_url should not include '/v1' suffix. "
                f"Use '{v[:-3]}' instead."
            )
        return v
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| configparser (INI) | YAML + Pydantic | ~2020 | Better for nested config, type validation |
| yaml.load() | yaml.safe_load() | PyYAML 5.1 (2019) | Security; safe_load is now required |
| Pydantic v1 | Pydantic v2 | July 2023 | New API: model_validate() not parse_obj() |
| BaseSettings auto-env | Explicit sources | pydantic-settings 2.0 | More control via settings_customise_sources |

**Deprecated/outdated:**
- `pydantic.parse_obj()` - Use `model_validate()` in v2
- `yaml.load()` without Loader - Use `yaml.safe_load()` or explicit SafeLoader
- Pydantic v1 syntax - Use v2 Field(), model_config, etc.

## Open Questions

Things that couldn't be fully resolved:

1. **Unrecognized keys: error or warning?**
   - What we know: Pydantic ignores extra fields by default; can set `extra='forbid'` to error
   - What's unclear: User preference not specified in CONTEXT.md (left to Claude's discretion)
   - Recommendation: Use `extra='ignore'` (default) with a logged warning for unrecognized keys. This is forgiving but informative.

2. **Image provider API key reuse**
   - What we know: HeroGenerator currently uses `ANTHROPIC_API_KEY` for RDSec endpoint
   - What's unclear: Should image have its own key, or share with LLM?
   - Recommendation: Separate key in schema (`image.api_key`) but can be same value. Migration detects ANTHROPIC_API_KEY and uses for both.

## Sources

### Primary (HIGH confidence)
- [Pydantic Settings Documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) - Settings management, YAML support, validation
- [Pydantic Error Handling](https://docs.pydantic.dev/latest/errors/errors/) - ValidationError collection
- [pydantic-settings PyPI](https://pypi.org/project/pydantic-settings/) - Version 2.12.0

### Secondary (MEDIUM confidence)
- [YAML env var interpolation gist](https://gist.github.com/mkaranasou/ba83e25c835a8f7629e34dd7ede01931) - Custom loader pattern
- [Docker Compose volumes](https://docs.docker.com/reference/compose-file/volumes/) - Bind mount for config files

### Tertiary (LOW confidence)
- Web search results for ecosystem patterns - verified against official docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Pydantic + PyYAML is well-documented standard
- Architecture: HIGH - Patterns verified from official Pydantic docs
- Pitfalls: HIGH - Common issues documented in official docs and verified

**Research date:** 2026-01-24
**Valid until:** 60 days (stable libraries, slow-changing domain)
