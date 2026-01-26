# Phase 5: Documentation & Cleanup - Context

**Gathered:** 2026-01-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Prepare the codebase for public open-source release with clean documentation, proper licensing, and removal of internal references. Includes README updates, example file creation, code cleanup, and frontend additions (About page, AI disclaimer).

</domain>

<decisions>
## Implementation Decisions

### README Structure
- Balanced approach: quick start + architecture overview in README, detailed config in separate sections
- Show Docker and local dev paths side-by-side (tabs/sections), not prioritizing one over the other
- Keep existing images but update them:
  - Fix AATF logo in top image (replace AI-generated smudge with actual circle logo from repo)
  - Update flow diagrams to add the grounding data step (Phase 0)

### Code Cleanup Scope
- Remove all RDSec references (literal strings, comments, URLs)
- Remove all Trend Micro references
- Keep TrendAI and AATF branding (project identity)
- Clean up comments mentioning internal context (e.g., "RDSec proxy", "internal Trend users")
- Make base URL configurable for RSS feeds (currently hardcoded as ainews.aatf.dev which isn't even real)
  - Add `base_url` config option so users can set their deployment domain
  - RSS feeds need absolute URLs for compatibility

### Example Files
- No .env.example needed - provider config is in providers.yaml
- Env vars are only for protecting secrets (users can use ${VAR} syntax in YAML)
- Move remaining non-secret config to YAML:
  - LOOKBACK_HOURS → YAML pipeline settings
  - TARGET_DATE → CLI argument with optional YAML default, unset by default
  - TZ, ENABLE_CRON, COLLECTION_SCHEDULE → Docker settings (already in docker-compose)
  - TWITTERAPI_IO_KEY → stays as env var (it's a secret)
- providers.yaml.example should be heavily commented (self-documenting, every option explained)

### Branding/Assets
- Current logo is already in webp format and good
- README header image needs logo fix (replace smudge with actual circle logo)
- Add AI-generated content disclaimer:
  - Footer: small disclaimer on every page
  - About page: detailed explanation
- Add About/FAQ page to frontend:
  - Desktop: menu link visible
  - Mobile: hidden from main nav (won't fit)
  - Footer: "About" link visible on all devices
- About page content:
  - Project explanation (why it was built, purpose)
  - Reference AATF Wiki content (built for AI news sense-making, pattern for information distillation)
  - Note: repurposed by several internal teams

### Claude's Discretion
- README tone (friendly but professional for open-source)
- Exact structure of About page content
- How to organize heavily-commented providers.yaml.example sections

</decisions>

<specifics>
## Specific Ideas

- "The logo needs to be put on properly. Right now it's like an AI generated smudge. It just needs to be replaced with the actual circle logo. The text is fine."
- About page should explain why it was built - reference AATF Wiki page for context on information distillation pattern
- AI disclaimer in footer, fuller explanation on About page

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-documentation-cleanup*
*Context gathered: 2026-01-25*
