---
phase: "03"
plan: "02"
subsystem: hero-generation
tags: ["hero-generator", "image-client", "graceful-skip", "config-based"]

dependency-graph:
  requires:
    - "01: Configuration infrastructure"
    - "03-01: ImageClient abstraction"
  provides:
    - "HeroGenerator using unified ImageClient"
    - "initialize_hero_generator() for pipeline integration"
    - "Config-based regenerate_hero.py script"
  affects:
    - "03-03: Pipeline integration"
    - "03-04: Example config updates"

tech-stack:
  added: []
  patterns:
    - "Factory pattern: HeroGenerator.from_config(config)"
    - "Graceful skip with WARNING logs for unconfigured providers"
    - "Backwards-compatible initialization with deprecation warnings"
    - "Config validation with helpful error messages"

key-files:
  modified:
    - "generators/hero_generator.py"
    - "scripts/regenerate_hero.py"

decisions:
  - id: "03-02-01"
    choice: "Keep backwards compatibility in HeroGenerator"
    reason: "Legacy api_key/endpoint/model params still work with deprecation warnings"
  - id: "03-02-02"
    choice: "initialize_hero_generator as module function"
    reason: "Clean entry point for pipeline code that handles None config gracefully"
  - id: "03-02-03"
    choice: "Script fails fast without config"
    reason: "regenerate_hero.py exits with helpful error if providers.yaml missing"
  - id: "03-02-04"
    choice: "Mode-specific troubleshooting in errors"
    reason: "Error messages include native vs openai-compatible specific guidance"

metrics:
  duration: "3 min"
  completed: "2026-01-25"
---

# Phase 3 Plan 2: Hero Generator Integration Summary

**One-liner:** HeroGenerator refactored to use ImageClient abstraction with graceful skip when unconfigured and config-based regenerate script

## What Was Built

### 1. HeroGenerator Refactoring (`generators/hero_generator.py`)

**Updated initialization:**
- New `client` parameter accepts any `BaseImageClient` implementation
- `from_config()` creates client via `ImageClient.from_config(config)`
- Legacy `api_key/endpoint/model` params still work with deprecation warnings
- Env var fallback (`ANTHROPIC_API_KEY`) preserved for backwards compat

**New features:**
- `initialize_hero_generator(config)` - module-level function returning `None` with WARNING log when no config
- Mode-specific troubleshooting in error messages
- Cleaned up by removing class-level ENDPOINT and MODEL constants

**Internal changes:**
- `generate()` uses `self.client.generate()` instead of direct requests.post
- `edit()` similarly refactored
- Response handling updated for `ImageResponse` dataclass

### 2. Graceful Skip Logic

When `initialize_hero_generator(None)` is called:
```
WARNING - Hero image generation disabled: no 'image' section in providers.yaml.
To enable, add image provider config. You can run scripts/regenerate_hero.py later
to generate images.
```

Pipeline continues with:
- `hero_image_url: null` in summary.json
- `hero_image_prompt: null` in summary.json

### 3. regenerate_hero.py Updates

**Config-based initialization:**
- `load_image_config()` - loads from `config/providers.yaml`
- `initialize_generator()` - wrapper with helpful error messages
- No more env var fallback - script requires proper config

**Helpful error messages:**
- Missing providers.yaml: suggests copying from example
- Missing image section: shows required YAML structure
- Initialization failures: includes mode-specific troubleshooting

## Key Code Patterns

### Factory Method Usage
```python
from generators.image_client import ImageClient

client = ImageClient.from_config(config)  # Returns appropriate implementation
generator = HeroGenerator(client=client)
```

### Graceful Skip Pattern
```python
generator = initialize_hero_generator(config.image)  # May be None
if generator:
    result = await generator.generate(topics, date, output_dir)
else:
    # hero_image_url = None, hero_image_prompt = None
```

### Backwards Compatibility
```python
# Old way (deprecated, still works)
generator = HeroGenerator(api_key="...", endpoint="...", model="...")
# DeprecationWarning emitted

# New way
generator = HeroGenerator.from_config(image_config)
```

## Commits

| Hash | Description |
|------|-------------|
| 792f4d4 | HeroGenerator ImageClient refactoring |
| f0e9719 | regenerate_hero.py config updates |

## Files Changed

| File | Changes |
|------|---------|
| `generators/hero_generator.py` | +208/-176 lines: ImageClient integration, graceful skip |
| `scripts/regenerate_hero.py` | +93/-7 lines: Config-based initialization |

## Deviations from Plan

None - plan executed exactly as written.

## Next Phase Readiness

Ready for:
- **03-03**: Pipeline integration - orchestrator can use `initialize_hero_generator()`
- **03-04**: Example config updates - can document image provider configuration

No blockers or concerns.
