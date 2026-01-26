---
phase: 01-configuration-infrastructure
plan: 02
subsystem: config
tags: [migration, env-vars, yaml, llm-client, factory-method]

# Dependency graph
requires:
  - phase: 01-01
    provides: Pydantic schema (ProviderConfig, LLMProviderConfig), YAML loader with ${VAR} interpolation
provides:
  - migrate_from_env() function for auto-converting env vars to providers.yaml
  - detect_env_vars() function for detecting ANTHROPIC_* environment variables
  - AnthropicClient.from_config() class method for config-based initialization
  - AsyncAnthropicClient.from_config() class method for config-based initialization
  - Automatic .env backup during migration
affects:
  - 01-03 (pipeline/orchestrator integration uses from_config())
  - 01-04 (dual API mode builds on config foundation)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Factory method pattern for LLM client creation (from_config)
    - Environment variable detection and auto-migration
    - TYPE_CHECKING import for forward references

key-files:
  created:
    - agents/config/migration.py
  modified:
    - agents/config/__init__.py
    - agents/llm_client.py

key-decisions:
  - "Return True from migrate_from_env if config already exists (no-op, not error)"
  - "Use TYPE_CHECKING import to avoid circular dependency with config module"
  - "Backup .env with timestamp suffix if previous backup exists"

patterns-established:
  - "Factory method pattern: ClientClass.from_config(config) for config-based instantiation"
  - "Migration creates human-readable YAML with comments, not just raw dump"
  - "Auto-migration integrated into load_config via auto_migrate parameter"

# Metrics
duration: 2min
completed: 2026-01-24
---

# Phase 1 Plan 02: Env Var Migration and LLM Client Config Summary

**Migration module for auto-converting ANTHROPIC_* env vars to providers.yaml, plus from_config() factory method for LLM clients**

## Performance

- **Duration:** 2 min
- **Started:** 2026-01-24T19:48:48Z
- **Completed:** 2026-01-24T19:50:29Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- Migration module detects ANTHROPIC_API_KEY, ANTHROPIC_API_BASE, ANTHROPIC_MODEL env vars
- Auto-generates providers.yaml with sensible defaults (model, timeout) when env vars present
- Backs up .env to .env.backup (with timestamp if backup exists)
- LLM clients (sync and async) have from_config() class method for clean initialization
- load_config() integrates auto-migration when no providers.yaml exists
- Warning logged when both providers.yaml and .env exist

## Task Commits

Each task was committed atomically:

1. **Task 1: Create migration module** - `c0171f9` (feat) - prior session
2. **Task 2: Integrate auto-migration into load_config** - `0fd2b06` (feat) - prior session
3. **Task 3: Add from_config to LLM clients** - `fa32c76` (feat)

## Files Created/Modified

- `agents/config/migration.py` - detect_env_vars() and migrate_from_env() functions
- `agents/config/__init__.py` - Updated to integrate migration, export detect_env_vars
- `agents/llm_client.py` - Added from_config() class method to AnthropicClient and AsyncAnthropicClient

## Decisions Made

1. **Return True for existing config:** migrate_from_env() returns True if providers.yaml already exists (treats as "migration successful" for backwards compat)
2. **Typed forward reference:** Used TYPE_CHECKING import to add LLMProviderConfig type hint without runtime import cycle
3. **Human-readable output:** Generated YAML includes comments and example for image provider (not raw dict dump)

## Deviations from Plan

None - plan executed exactly as written. Tasks 1 and 2 were completed in a prior session; Task 3 was completed in this session.

## Issues Encountered

None - all verification tests passed on first run.

## User Setup Required

None - no external service configuration required. The migration module helps users transition from env var setup to YAML config automatically.

## Next Phase Readiness

- Migration and from_config() ready for Plan 03 (pipeline/orchestrator integration)
- Orchestrator can now use `AsyncAnthropicClient.from_config(config.llm)` instead of relying on env vars
- All must_haves verified:
  - User with ANTHROPIC_* env vars sees auto-generated providers.yaml
  - .env renamed to .env.backup after migration
  - Warning when both providers.yaml and .env exist
  - LLM client can be initialized from ProviderConfig
  - Backwards compat maintained (env vars still work when no config)

---
*Phase: 01-configuration-infrastructure*
*Completed: 2026-01-24*
