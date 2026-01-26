# Phase 5: Documentation & Cleanup - Research

**Researched:** 2026-01-25
**Domain:** Documentation, code cleanup, open-source preparation
**Confidence:** HIGH

## Summary

This phase prepares the codebase for public open-source release by removing internal references, updating documentation, and adding frontend enhancements (About page, AI disclaimer). The research revealed a comprehensive list of files requiring cleanup, including hardcoded RDSec endpoints, Trend Micro references, and an internal domain URL in the feed generator.

**Key findings:**
- RDSec references exist in 4 production files plus documentation
- Trend Micro email/references exist in README and LICENSE
- Feed generator has hardcoded `SITE_URL = "https://news.aatf.ai"` that needs to be configurable
- Logo assets already exist in WebP format (no conversion needed per DOC-05)
- LICENSE currently shows MIT but needs Apache 2.0 per requirements
- The banner image has a smudged AATF logo that needs fixing

**Primary recommendation:** Execute file-by-file cleanup with grep verification, add base_url config option for feeds, create About page with AI disclaimer, and update LICENSE to Apache 2.0.

## Standard Stack

### Core Tools
| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| sed/grep | system | Text search and replace | Standard Unix tools for file manipulation |
| SvelteKit | existing | Frontend routing | Already in use, add /about route |
| YAML | existing | Configuration | Already used for providers.yaml |

### No New Dependencies Required
This phase is purely cleanup and documentation - no new libraries needed. All changes use existing tooling.

## Architecture Patterns

### File Cleanup Pattern
Files are categorized by cleanup type:

**Type A: Remove RDSec references (literal strings, comments, URLs)**
- `agents/llm_client.py` (line 39) - Comment: "# RDSec proxy model limit"
- `generators/hero_generator.py` (lines 94, 117) - Hardcoded RDSec endpoints
- `CLAUDE.md` (lines 87, 250) - Documentation references

**Type B: Remove Trend Micro references**
- `README.md` (lines 38, 96-97, 188, 389) - Contact emails, portal links
- `LICENSE` (line 3) - Copyright attribution
- `generators/hero_generator.py` (line 257) - Comment in prompt

**Type C: Make configurable**
- `generators/feed_generator.py` (line 95) - `SITE_URL = "https://news.aatf.ai"`
  - Add `base_url` parameter to `__init__` with default from config
  - Add `pipeline.base_url` option to providers.yaml schema

### Recommended Config Extension
```yaml
# In providers.yaml
pipeline:
  base_url: "https://your-domain.com"  # For RSS feed URLs
  lookback_hours: 24  # Data collection window
```

### Frontend Addition Pattern
```
frontend/src/routes/
├── +page.svelte          # Existing home
├── archive/+page.svelte  # Existing archive
├── feeds/+page.svelte    # Existing feeds
└── about/+page.svelte    # NEW: About/FAQ page
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| License text | Write from scratch | Apache 2.0 template | Legal compliance, standard format |
| About page markup | Custom HTML | Svelte markdown component | Consistency with existing pages |

## Common Pitfalls

### Pitfall 1: Incomplete Reference Removal
**What goes wrong:** grep finds references but some in comments or strings are missed
**Why it happens:** Multiple file types (.py, .md, .yaml, .svelte) with different comment syntaxes
**How to avoid:** Use comprehensive grep patterns, verify with `grep -r "rdsec\|RDSec" --include="*.py" --include="*.md" --include="*.yaml"`
**Warning signs:** Post-cleanup grep still returns results

### Pitfall 2: Breaking Feed URLs
**What goes wrong:** Changing SITE_URL breaks existing RSS subscribers
**Why it happens:** RSS readers cache feed URLs; changing them creates duplicates
**How to avoid:** Make base_url configurable with sensible default, document in README
**Warning signs:** RSS validation errors, duplicate entries in readers

### Pitfall 3: Forgetting Internal Context in Comments
**What goes wrong:** Comments mentioning "internal", "Trend users", "proxy" left behind
**Why it happens:** Focus on literal strings, ignore contextual references
**How to avoid:** Search for contextual terms: `grep -r "internal\|proxy\|Trend user"`
**Warning signs:** Code archaeology reveals original context

### Pitfall 4: Missing LICENSE Update
**What goes wrong:** LICENSE says MIT but README says Apache 2.0
**Why it happens:** LICENSE file forgotten during documentation updates
**How to avoid:** Update LICENSE first, then reference in README
**Warning signs:** License mismatch between files

## Files Requiring Changes

### RDSec Reference Removal (Priority 1)
| File | Line(s) | Content | Action |
|------|---------|---------|--------|
| `agents/llm_client.py` | 39 | `# RDSec proxy model limit` | Change to `# Model max token limit` |
| `generators/hero_generator.py` | 94 | RDSec endpoint in legacy code | Remove entirely (deprecated path) |
| `generators/hero_generator.py` | 117 | RDSec endpoint in env fallback | Remove entirely (deprecated path) |
| `generators/hero_generator.py` | 257 | "No Trend Micro logos" comment | Change to "No company logos" |
| `CLAUDE.md` | 87 | "via RDSec" | Change to "via Gemini API" |
| `CLAUDE.md` | 250 | "via RDSec endpoint" | Change to "via configured provider" |

### Trend Micro Reference Removal (Priority 2)
| File | Line(s) | Content | Action |
|------|---------|---------|--------|
| `README.md` | 38 | Email contact | Remove or change to generic |
| `README.md` | 96-97 | RDSec portal links | Remove prerequisites section |
| `README.md` | 188 | RDSec API key table | Update to show standard Anthropic/Gemini |
| `README.md` | 389 | Email contact | Change to GitHub Issues link |
| `LICENSE` | 3 | "Trend Micro" in copyright | Change to "AI Acceleration Task Force" |

### Configurable Base URL (Priority 3)
| File | Line(s) | Content | Action |
|------|---------|---------|--------|
| `generators/feed_generator.py` | 95 | `SITE_URL = "https://news.aatf.ai"` | Make configurable via `__init__` param |
| `agents/config/schema.py` | - | N/A | Add `PipelineConfig` with `base_url` |

### New Files to Create (Priority 4)
| File | Purpose |
|------|---------|
| `frontend/src/routes/about/+page.svelte` | About/FAQ page with AI disclaimer |
| `config/providers.yaml.example` | Update with heavily commented sections |
| `LICENSE` | Replace with Apache 2.0 text |

### Files to Update (Priority 5)
| File | Changes Needed |
|------|----------------|
| `frontend/src/lib/components/layout/Footer.svelte` | Add AI disclaimer, About link |
| `frontend/src/lib/components/layout/Navigation.svelte` | Add About link (desktop only) |
| `README.md` | Complete rewrite for external audience |
| `assets/pipeline-banner.webp` | Fix AATF logo (replace smudge with circle logo) |

## Asset Status

### Logo Files (DOC-05)
Current state: **Already in WebP format - NO CONVERSION NEEDED**
- `frontend/static/assets/logo.webp` - Main logo (exists, good quality)
- `web/assets/logo.webp` - Built output (exists)
- `frontend/static/assets/logo.png` - Original PNG (can be deleted)

The context decision states "Current logo is already in webp format and good" - confirmed by file inspection.

### Banner Image Fix
The `assets/pipeline-banner.webp` shows the AATF logo in top-left as an AI-generated smudge rather than the crisp circle logo from `frontend/static/assets/logo.webp`.

**Fix approach:**
1. Use image editing to overlay the circle logo onto the banner
2. Keep the skunk mascot and other elements unchanged
3. Save as WebP with same quality settings

## LICENSE Content

### Apache 2.0 Template
```text
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
   [standard Apache 2.0 text...]

   Copyright 2026 AI Acceleration Task Force (AATF)

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0
```

## About Page Content Structure

Per context decisions, the About page should include:
1. Project explanation (why it was built, purpose)
2. Reference AATF Wiki content (information distillation pattern)
3. Note about internal team adoption
4. AI-generated content disclaimer (detailed explanation)

### AI Disclaimer Text
**Footer (short):** "Content generated by AI. May contain errors."

**About page (detailed):**
- Explains that summaries are AI-generated using Claude Opus 4.5
- Notes that extended thinking is used for analysis
- Clarifies that source links lead to original content
- Recommends verifying important information from primary sources

## README Rewrite Structure

Per context decisions (balanced approach):
1. **Quick Start** - Docker and local dev side-by-side
2. **Architecture Overview** - Pipeline phases, agent pairs
3. **Configuration** - Link to providers.yaml.example
4. **Deployment** - Self-hosting instructions
5. **Contributing** - GitHub-focused (no internal contacts)

Remove:
- All RDSec/Trend Micro references
- Internal portal links
- Employee-specific instructions
- Internal email contacts

## Verification Commands

After cleanup, these commands should return NO results:

```bash
# No RDSec references in code
grep -ri "rdsec" --include="*.py" --include="*.yaml" --include="*.yml" .

# No Trend Micro references in code (except AATF branding which is OK)
grep -ri "trend.micro\|trendmicro" --include="*.py" --include="*.md" .

# No internal email references
grep -r "@trendmicro.com" .

# No hardcoded internal URLs
grep -r "portal\.rdsec\|api\.rdsec" .
```

## Open Questions

1. **Base URL default value**
   - What we know: Current hardcoded value is `https://news.aatf.ai`
   - What's unclear: Should default be empty (required), a placeholder, or localhost?
   - Recommendation: Use `http://localhost:8080` as default for local dev, require explicit config for production

2. **Banner image regeneration**
   - What we know: Logo needs to be replaced with actual circle logo
   - What's unclear: Does user want to regenerate via AI or manual edit?
   - Recommendation: Manual edit to overlay logo (faster, more predictable)

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection via grep and file reads
- Context decisions from 05-CONTEXT.md

### Secondary (MEDIUM confidence)
- Apache 2.0 license text format (standard, well-documented)

## Metadata

**Confidence breakdown:**
- File locations: HIGH - Direct grep/file inspection
- Cleanup scope: HIGH - Comprehensive search performed
- Config patterns: HIGH - Matches existing providers.yaml structure
- Frontend additions: MEDIUM - Follows existing SvelteKit patterns

**Research date:** 2026-01-25
**Valid until:** 2026-02-25 (stable, documentation-focused)
