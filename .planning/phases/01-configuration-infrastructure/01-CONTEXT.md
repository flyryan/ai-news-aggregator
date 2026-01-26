# Phase 1: Configuration Infrastructure - Context

**Gathered:** 2026-01-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Establish a YAML configuration system (`config/providers.yaml`) for LLM and image provider settings. Replace direct env var usage with YAML-based configuration. Auto-migrate existing env var setups to YAML. Prompts configuration is a separate phase.

</domain>

<decisions>
## Implementation Decisions

### Config Precedence
- YAML is the single source of truth — no env var fallbacks for provider settings
- Env var interpolation (`${VAR}`) is supported in YAML values but not the default pattern
- Example file shows direct values; interpolation documented in comments only
- If `providers.yaml` is missing, generate it from detected env vars automatically
- If both YAML and env vars exist, YAML wins with a warning about ignoring .env

### Error Messaging
- Validate entire config at startup — fail fast before any work begins
- Collect and display all validation errors at once (not stop at first)
- Helpful error messages with suggestions (e.g., "expected string, got null. Did you mean to use ${VAR}?")

### File Organization
- Single file: `config/providers.yaml` for LLM + image providers
- Prompts stay separate (Phase 4 scope)
- Feed lists (rss_feeds.txt, twitter_accounts.txt, etc.) remain as .txt files
- Config directory stays at `config/`
- Example file: `config/providers.yaml.example` (user copies to `providers.yaml`)

### Migration Path
- If env vars detected and no YAML exists: auto-generate `providers.yaml` (no prompt)
- After auto-migration: rename `.env` to `.env.backup`
- If YAML already exists + .env present: use YAML, log warning about ignoring .env
- No backwards compatibility period — clean switch to YAML-only

### Claude's Discretion
- Whether unrecognized keys in YAML are errors vs warnings
- Exact YAML schema structure
- Specific env var names to detect during migration

</decisions>

<specifics>
## Specific Ideas

- "We'll need to set the compose to mount the local config file so the container doesn't need to rebuild"
- Migration should be seamless — auto-generate providers.yaml without user interaction
- Keep feed config files (.txt) as simple text, don't YAML-ify everything

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-configuration-infrastructure*
*Context gathered: 2026-01-24*
