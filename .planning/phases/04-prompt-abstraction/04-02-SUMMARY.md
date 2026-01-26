---
phase: 04-prompt-abstraction
plan: 02
subsystem: config
tags: [yaml, prompts, llm, configuration]

# Dependency graph
requires:
  - phase: 04-01
    provides: Prompt infrastructure (schema, loader, accessor)
provides:
  - Centralized prompt configuration file (config/prompts.yaml)
  - All 18 LLM prompts externalized from 8 source files
  - Variable documentation for each prompt section
affects: [04-03, pipeline-migration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Runtime variable syntax: ${var} for context, ${env:VAR} for environment

key-files:
  created:
    - config/prompts.yaml
  modified:
    - agents/config/prompts.py

key-decisions:
  - "Load prompts YAML without env var resolution to preserve ${var} placeholders"
  - "Use literal block scalars (|) for multiline prompt preservation"
  - "File serves as both working config AND reference example (per PRM-05)"

patterns-established:
  - "Prompt organization: gathering > analysis > orchestration > post_processing"
  - "Variable documentation in comments above each prompt section"

# Metrics
duration: 4min
completed: 2026-01-25
---

# Phase 4 Plan 2: Prompt Extraction Summary

**All 18 LLM prompts externalized to config/prompts.yaml with ${var} syntax and documented variables per section**

## Performance

- **Duration:** 4 min (224 seconds)
- **Started:** 2026-01-25T20:15:41Z
- **Completed:** 2026-01-25T20:19:25Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- Extracted 18 prompts from 8 source files to centralized YAML configuration
- Organized prompts by pipeline phase (gathering, analysis, orchestration, post_processing)
- Documented available variables in comments for each prompt section
- Fixed prompts.py loader to preserve ${var} placeholders for runtime resolution

## Task Commits

1. **Task 1: Create config/prompts.yaml with all prompts** - `ee8f4dc` (feat)

## Files Created/Modified

- `config/prompts.yaml` - Complete prompt configuration (805 lines)
  - gathering: link_relevance prompt
  - analysis: news, research, social, reddit (5 prompts each with batch_analysis, filter, combined_analysis, analysis, ranking)
  - orchestration: topic_detection, executive_summary
  - post_processing: link_enrichment, ecosystem_enrichment
- `agents/config/prompts.py` - Fixed loader to not resolve env vars at load time

## Decisions Made

- **Load prompts without env var resolution**: The original prompts.py called `load_yaml_with_env` which tried to resolve all `${var}` patterns as environment variables. Changed to use plain `yaml.safe_load` since prompts contain runtime placeholders that should be preserved.
- **Literal block scalars**: All prompts use YAML `|` syntax to preserve multiline formatting, especially important for JSON examples in prompts.
- **Single file for config AND example**: Per PRM-05 user decision in 04-CONTEXT.md, the prompts.yaml file serves as both the working configuration and the reference example with documented variables.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed prompts.py to not resolve env vars at load time**
- **Found during:** Task 1 verification
- **Issue:** `load_yaml_with_env` was trying to resolve `${url}` as an environment variable
- **Fix:** Changed `load_prompts` to use `yaml.safe_load` directly, preserving ${var} placeholders
- **Files modified:** agents/config/prompts.py
- **Verification:** `load_prompts('./config')` succeeds and schema validation passes
- **Committed in:** ee8f4dc (part of Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Required for prompts to load correctly. No scope creep.

## Issues Encountered

None beyond the blocking issue documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- config/prompts.yaml is complete with all 18 prompts
- Schema validation confirms structure matches PromptConfig
- Ready for Plan 04-03 to wire prompts into pipeline components
- All analyzer classes still have hardcoded prompts that need migration

---
*Phase: 04-prompt-abstraction*
*Completed: 2026-01-25*
