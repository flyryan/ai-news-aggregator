# AI News Aggregator

## What This Is

A multi-agent pipeline that collects AI/ML news from multiple sources (RSS feeds, arXiv API, Twitter, Reddit, Bluesky, Mastodon), analyzes them using Claude with extended thinking, and serves a modern Svelte SPA frontend. Configurable for anyone to run with their own API keys via YAML configuration with environment variable interpolation.

## Core Value

Make the pipeline work with standard API endpoints (Anthropic direct, OpenAI-compatible, Google Gemini) so anyone can run their own instance without internal infrastructure dependencies.

## Requirements

### Validated

- Multi-agent pipeline (4 gatherers, 4 analyzers) — existing
- Extended thinking support (QUICK/STANDARD/DEEP/ULTRATHINK) — existing
- RSS, arXiv, Twitter, Reddit, Bluesky, Mastodon collection — existing
- Cross-category topic detection — existing
- Executive summary generation — existing
- Link enrichment for internal navigation — existing
- Ecosystem context for model release grounding — existing
- Hero image generation via Gemini — existing
- Svelte SPA frontend with search, calendar, dark mode — existing
- JSON/RSS feed generation — existing
- Docker deployment — existing
- YAML configuration with ${VAR} interpolation — v1.0
- Direct Anthropic API support (x-api-key auth) — v1.0
- OpenAI-compatible endpoint support (Bearer auth) — v1.0
- Extended thinking validation (fail-fast) — v1.0
- Native Google Gemini SDK support — v1.0
- OpenAI-compatible image endpoint support — v1.0
- Graceful hero image skip when not configured — v1.0
- Externalized prompts in config/prompts.yaml — v1.0
- Apache 2.0 LICENSE — v1.0
- About page with AI disclaimer — v1.0
- Comprehensive README for external users — v1.0

### Active

(No active requirements — milestone complete)

### Out of Scope

- Custom branding configuration — TrendAI branding stays (mascot, colors)
- Alternative image providers (DALL-E, etc.) — Gemini only for now
- Database backend — keep file-based JSON output
- User authentication — public static site
- Multi-tenant support — single instance per deployment
- AWS Bedrock provider — adds complexity, can add in v2 if requested
- Provider abstraction library (LiteLLM dep) — direct SDK calls preferred

## Context

**Current State (v1.0):**
- ~13,600 lines Python, ~3,800 lines Svelte/TypeScript
- Tech stack: Python 3.11+, Anthropic SDK, google-genai SDK, Svelte 5, SvelteKit, Tailwind CSS
- Configuration: YAML-based with env var interpolation, backwards-compatible with legacy env vars
- LLM: Dual mode support (anthropic direct, openai-compatible proxies)
- Images: Dual mode support (native Gemini, openai-compatible proxies), optional with graceful skip
- Prompts: 18 prompts externalized to config/prompts.yaml
- Documentation: 368-line README, Apache 2.0 license, About page

**Live deployment:** https://news.aatf.ai

## Constraints

- **Model Requirement**: Pipeline designed for Claude Opus 4.5 extended thinking — untested on other models
- **Image Model**: Hero generation requires Gemini 3 Pro image capabilities
- **License**: Apache 2.0 (permissive with patent protection)
- **Backwards Compatibility**: Existing deployments using env vars continue working

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| YAML config with env var fallbacks | Cleaner than pure env vars, supports templates | Good |
| Keep TrendAI branding | Project identity, draws attention to TrendAI | Good |
| Apache 2.0 license | Permissive with patent protection | Good |
| Skip hero gracefully if no Gemini | Don't block pipeline for missing optional feature | Good |
| Factory method pattern (from_config) | Consistent initialization across components | Good |
| Fail fast on missing thinking blocks | Extended thinking is essential for quality | Good |
| Backwards-compatible fallbacks | Legacy code paths preserved | Good |
| Mode-based auth switching | Clean separation of anthropic vs openai-compatible | Good |
| PromptAccessor pattern | Type-safe prompt access with variable resolution | Good |

---
*Last updated: 2026-01-26 after v1.0 milestone*
