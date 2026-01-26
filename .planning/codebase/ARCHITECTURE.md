# Architecture

**Analysis Date:** 2026-01-24

## Pattern Overview

**Overall:** Multi-Agent Pipeline with Decoupled Frontend

**Key Characteristics:**
- Agent-based parallel processing for data collection and analysis
- Map-reduce pattern for LLM analysis at scale
- Async Python backend generating static JSON consumed by Svelte SPA
- Clear separation between data generation (Python) and presentation (Svelte)

## Layers

**Data Collection Layer:**
- Purpose: Gather AI news from multiple external sources in parallel
- Location: `agents/gatherers/`
- Contains: Source-specific gatherers (RSS, arXiv, Twitter, Reddit, Bluesky, Mastodon)
- Depends on: External APIs, BaseGatherer abstraction
- Used by: MainOrchestrator for parallel gathering phase
- Pattern: Each gatherer normalizes to `CollectedItem` dataclass

**Analysis Layer:**
- Purpose: LLM-based analysis of collected items using extended thinking
- Location: `agents/analyzers/`
- Contains: Category-specific analyzers (news, research, social, reddit)
- Depends on: AnthropicClient, AsyncAnthropicClient, BaseAnalyzer abstraction
- Used by: MainOrchestrator for parallel analysis phase
- Pattern: Map-reduce with batched LLM calls returning `CategoryReport`

**Orchestration Layer:**
- Purpose: Coordinate all agents and synthesize cross-category insights
- Location: `agents/orchestrator.py`
- Contains: MainOrchestrator, pipeline coordination logic
- Depends on: All gatherers, all analyzers, LLM clients
- Used by: Entry point script `run_pipeline.py`
- Pattern: Multi-phase async pipeline with sequential dependencies

**Generation Layer:**
- Purpose: Transform analyzed data into frontend-consumable formats
- Location: `generators/`
- Contains: JSONGenerator, FeedGenerator, SearchIndexer, HeroGenerator
- Depends on: OrchestratorResult dataclass, image APIs
- Used by: Pipeline after orchestration completes
- Pattern: File-based output to `web/data/` directory structure

**Presentation Layer:**
- Purpose: Svelte 5 SPA for browsing news with dark mode, search, calendar
- Location: `frontend/src/`
- Contains: Components, stores, services, routes
- Depends on: Generated JSON data in `web/data/`
- Used by: End users via web browser
- Pattern: SvelteKit with file-based routing, client-side data loading

**Support Layer:**
- Purpose: Cross-cutting concerns and utilities
- Location: `agents/llm_client.py`, `agents/cost_tracker.py`, `agents/ecosystem_context.py`
- Contains: LLM client abstraction, cost tracking, grounding context
- Depends on: Anthropic SDK, httpx for Bearer auth
- Used by: All analyzers and orchestrator

## Data Flow

**Collection Flow (Phase 1):**

1. MainOrchestrator initializes 4 gatherers (news, research, social, reddit)
2. Research, reddit, social run in parallel
3. Social gatherer results passed to news gatherer for link following
4. News gatherer extracts linked articles using LLM relevance checks
5. All gatherers normalize results to `CollectedItem` format

**Analysis Flow (Phase 2):**

1. Each analyzer receives list of `CollectedItem` for its category
2. MAP phase: Items split into batches of 75, processed in parallel (max 4 concurrent)
3. Each batch analyzed with STANDARD thinking (8192 token budget)
4. REDUCE phase: Top 50 candidates re-ranked with DEEP thinking (16000 tokens)
5. Output: `CategoryReport` with top 10 items, themes, category summary

**Synthesis Flow (Phases 3-4):**

1. Cross-category topic detection with ULTRATHINK (32000 token budget)
2. Executive summary generation with DEEP thinking
3. Link enrichment adds internal navigation links to summaries
4. Ecosystem enrichment detects new model releases from news items
5. Hero image generation via Gemini 3 Pro with skunk mascot

**Output Flow (Phases 5-7):**

1. JSONGenerator creates `web/data/{date}/` directory structure
2. Generates `summary.json`, `{category}.json` for each category
3. Updates `index.json` with date manifest
4. SearchIndexer builds Lunr.js index from all items
5. FeedGenerator creates Atom RSS feeds with Media RSS thumbnails

**Frontend Flow:**

1. User navigates to `/?date=2026-01-23` or `/archive`
2. Date store loads `index.json` to get available dates
3. DataLoader fetches `summary.json` for selected date
4. Markdown content converted to sanitized HTML (nh3 library)
5. Navigation updates URL query params, triggers reactive data load

**State Management:**
- Backend: Stateless pipeline, each run independent
- Frontend: Svelte stores for date navigation, theme toggle
- Data: File-based JSON, no database

## Key Abstractions

**CollectedItem:**
- Purpose: Standardized format for items from any source
- Examples: `agents/base.py:25-49`
- Pattern: Dataclass with normalized fields (title, content, url, source_type)

**AnalyzedItem:**
- Purpose: CollectedItem enriched with LLM analysis
- Examples: `agents/base.py:88-110`
- Pattern: Wraps CollectedItem with summary, importance_score, themes

**CategoryReport:**
- Purpose: Complete analysis output for one category
- Examples: `agents/base.py:123-152`
- Pattern: Contains top_items, all_items, category_summary, themes

**BaseGatherer:**
- Purpose: Abstract base for all source gatherers
- Examples: `agents/gatherers/news_gatherer.py`, `agents/gatherers/research_gatherer.py`
- Pattern: Implements gather() returning List[CollectedItem]

**BaseAnalyzer:**
- Purpose: Abstract base for all category analyzers
- Examples: `agents/analyzers/news_analyzer.py`, `agents/analyzers/research_analyzer.py`
- Pattern: Implements analyze() with map-reduce LLM processing

**ThinkingLevel:**
- Purpose: Budget tokens for extended thinking
- Examples: `agents/llm_client.py:27-32`
- Pattern: IntEnum with QUICK/STANDARD/DEEP/ULTRATHINK levels

## Entry Points

**CLI Entry Point:**
- Location: `run_pipeline.py:253-310`
- Triggers: Manual execution via `python3 run_pipeline.py`
- Responsibilities: Parses CLI args, initializes MainOrchestrator, runs async pipeline

**Docker Entry Point:**
- Location: `entrypoint.sh`
- Triggers: Container startup or scheduled cron job
- Responsibilities: Serves existing content via nginx, optionally runs pipeline on schedule

**Frontend Entry Point:**
- Location: `frontend/src/routes/+layout.svelte`
- Triggers: Initial page load
- Responsibilities: Initializes date store, loads available dates, sets up theme

**Main Orchestrator:**
- Location: `agents/orchestrator.py:201-358`
- Triggers: Called by run_pipeline.py
- Responsibilities: Runs 7-phase pipeline, coordinates all agents, saves results

## Error Handling

**Strategy:** Graceful degradation with partial results

**Patterns:**
- Gatherers: Log errors, return empty list on failure, track collection status
- Analyzers: Retry failed batches once with 5s backoff, return low-scored items if analysis fails
- Orchestrator: Continues pipeline even if individual categories fail, logs collection status
- Frontend: Shows error states for missing data, date validation redirects

## Cross-Cutting Concerns

**Logging:** Python logging module at INFO level, structured log messages with phase markers

**Validation:**
- Date format validation (YYYY-MM-DD) with user-friendly error messages
- Category param validation against allowed list (news/research/social/reddit)
- URL normalization for deduplication
- HTML sanitization via nh3 library to prevent XSS

**Authentication:**
- Bearer token auth for Anthropic API via custom httpx transport
- TwitterAPI.io key-based authentication
- No authentication for Reddit, Bluesky, Mastodon (public APIs)

**Cost Tracking:**
- Singleton cost tracker records all LLM calls with token counts
- Calculates costs based on model pricing
- Generates JSON cost reports per pipeline run
- Located in `agents/cost_tracker.py`

**Grounding Context:**
- EcosystemContextManager maintains AI model release dates
- Injected as system prompt to prevent hallucinations
- Auto-updates from OpenRouter API and detected news
- Located in `agents/ecosystem_context.py`

---

*Architecture analysis: 2026-01-24*
