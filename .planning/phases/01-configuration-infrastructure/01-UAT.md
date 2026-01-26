---
status: diagnosed
phase: 01-configuration-infrastructure
source: 01-01-SUMMARY.md, 01-02-SUMMARY.md, 01-03-SUMMARY.md, 01-04-SUMMARY.md
started: 2026-01-24T21:15:00Z
updated: 2026-01-24T21:30:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Create and Load providers.yaml
expected: Create config/providers.yaml with LLM provider settings. Pipeline reads it at startup without errors.
result: issue
reported: "it renamed me .env to .env.env.backup. also, we need to gitignore the backup (and the providers.yaml if its not)"
severity: major

### 2. Environment Variable Interpolation
expected: Use ${ANTHROPIC_API_KEY} syntax in providers.yaml. Pipeline resolves it from environment variable.
result: skipped
reason: .env was renamed by migration bug, can't test env var resolution

### 3. Auto-Migration on First Run
expected: User with env vars but no providers.yaml runs pipeline. Config is auto-generated from env vars.
result: issue
reported: "Already tested in Test 1 - migration has multiple bugs (wrong backup name, copies literal values, wrong image model/endpoint)"
severity: major

### 4. Config Validation Error Messages
expected: Create providers.yaml with invalid value (e.g., api_key: "your-key-here"). Pipeline shows clear error at startup listing the validation issue.
result: pass
verified: Schema validates "your-api-key-here" placeholder and raises clear ValueError

### 5. Example Config File
expected: config/providers.yaml.example exists with documented options for LLM and image providers, including mode field documentation.
result: pass
verified: File exists (3340 bytes) with full documentation

### 6. Auto-Migration from Env Vars
expected: Delete providers.yaml. Run pipeline with ANTHROPIC_* env vars. A new providers.yaml is auto-generated with those values.
result: issue
reported: "Migration has bugs - see Test 1 (wrong backup name, copies literals, wrong image model)"
severity: major

### 7. Hero Generator Disabled Without Image Config
expected: Run pipeline with LLM config but no image provider config. Pipeline completes successfully, logs "Hero image generation disabled".
result: pass
verified: orchestrator.py:202 logs "Hero image generation disabled (no image provider configured)"

### 8. providers.yaml Gitignored
expected: Check .gitignore contains config/providers.yaml entry. User secrets won't be committed.
result: issue
reported: "config/providers.yaml is gitignored, but .env.backup* is NOT gitignored"
severity: minor

### 9. Dual API Mode Fields Present
expected: providers.yaml.example shows mode field options: anthropic/openai-compatible for LLM, native/openai-compatible for image.
result: pass
verified: Example shows mode: "anthropic", mode: "native", mode: "openai-compatible" options

## Summary

total: 9
passed: 4
issues: 4
pending: 0
skipped: 1

## Gaps

- truth: "Migration creates providers.yaml with env var references and backs up .env correctly"
  status: failed
  reason: "User reported: (1) renamed .env to .env.env.backup instead of .env.backup, (2) copies literal API key values instead of using ${VAR} references, (3) didn't migrate image provider config, (4) backup files not gitignored"
  severity: major
  test: 1
  root_cause: "Path.with_suffix() replaces suffix but .env's entire name IS the suffix; template hardcodes literal values and wrong gemini-2.0 model; no GEMINI_API_KEY in ENV_VAR_MAPPING"
  artifacts:
    - path: "agents/config/migration.py"
      issue: "Line 99 copies literal values; Line 119 suffix logic wrong; Lines 107-109 have wrong gemini-2.0 endpoint/model instead of gemini-3-pro-image-preview with native mode (no endpoint)"
    - path: ".gitignore"
      issue: "Missing .env.backup entries"
  missing:
    - "Use ${ANTHROPIC_API_KEY} syntax instead of literal values"
    - "Fix backup filename logic"
    - "Migrate image provider env vars if present (GEMINI_API_KEY or similar)"
    - "Add .env.backup* to .gitignore"
    - "Update image example to match providers.yaml.example: mode=native, model=gemini-3-pro-image-preview, no endpoint"
  fix_plan: "01-05-PLAN.md"

- truth: ".env.backup files are gitignored"
  status: failed
  reason: "config/providers.yaml is gitignored, but .env.backup* is NOT gitignored"
  severity: minor
  test: 8
  root_cause: "Simply missing from .gitignore"
  artifacts:
    - path: ".gitignore"
      issue: "Missing .env.backup* pattern"
  missing:
    - "Add .env.backup* to .gitignore"
  fix_plan: "01-05-PLAN.md (Task 5)"
