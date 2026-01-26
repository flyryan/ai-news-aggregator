# Phase 4: Prompt Abstraction - Context

**Gathered:** 2026-01-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Extract all LLM prompts to external YAML files for customization without code changes. Users can view and modify prompts in `config/prompts.yaml` and see changes reflected in the next pipeline run.

</domain>

<decisions>
## Implementation Decisions

### File Organization
- Single `config/prompts.yaml` file (not split by agent type)
- Organized by pipeline phase: gathering, analysis, orchestration, post-processing
- Nested structure within phases (e.g., `analysis.news.system`, `analysis.news.ranking`)
- Orchestrator prompts (topic_detection, executive_summary) get their own top-level `orchestration:` section

### Variable Substitution
- Use `${var}` syntax for runtime variables (matches providers.yaml pattern)
- Support both runtime context (`${date}`, `${category}`) and environment variables (`${env:VAR}`)
- Fail fast with clear error on missing variables by default
- Per-prompt `allow_missing: true` flag to bypass variable validation (commented out in file with explanation of when to use)
- No prompt composition/includes — each prompt is self-contained

### Customization Workflow
- `config/prompts.yaml` ships in the repo with all working prompts (not an example file)
- Comments inline document available variables for each prompt section
- Full validation at startup: schema, prompt existence, AND variable presence (unless bypassed per-prompt)
- If prompts.yaml missing: fail with instructions to recreate (safety net for accidental deletion)
- Don't expose prompt keys in output JSON — keep output clean

### Claude's Discretion
- Exact prompt text and structure
- How to organize variables documentation within comments
- Validation error message formatting
- Default prompts content (extract from current code)

</decisions>

<specifics>
## Specific Ideas

- "This is an information distillation pattern" — missing variables are bad for the workflow, but don't want restrictions buried in code
- The `allow_missing` flag should be commented out with a note explaining why someone might want to use it (experimentation, edge cases)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-prompt-abstraction*
*Context gathered: 2026-01-25*
