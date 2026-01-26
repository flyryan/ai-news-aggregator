# Codebase Structure

**Analysis Date:** 2026-01-24

## Directory Layout

```
ai-news-aggregator/
├── agents/                  # Multi-agent data collection & analysis
│   ├── analyzers/          # Category-specific LLM analyzers
│   ├── gatherers/          # Source-specific data collectors
│   └── continuity/         # Story continuation detection
├── generators/             # JSON, RSS, search index generators
├── frontend/               # Svelte 5 SPA
│   ├── src/
│   │   ├── lib/           # Components, services, stores
│   │   └── routes/        # File-based routing
│   └── static/            # Static assets
├── scripts/                # Maintenance utilities
├── config/                 # Feed lists, model releases
├── data/                   # Runtime data (raw/processed)
├── web/                    # Generated output for frontend
│   ├── data/              # JSON data & hero images
│   └── _app/              # Built frontend assets
├── assets/                 # Reference images for generators
├── logs/                   # Pipeline logs
├── run_pipeline.py         # CLI entry point
└── docker-compose.yml      # Production deployment
```

## Directory Purposes

**agents/:**
- Purpose: Multi-agent pipeline core
- Contains: Gatherers, analyzers, orchestrator, LLM client, cost tracker
- Key files:
  - `orchestrator.py`: MainOrchestrator coordinating all agents
  - `base.py`: BaseGatherer, BaseAnalyzer, dataclasses (CollectedItem, CategoryReport)
  - `llm_client.py`: Anthropic client with Bearer auth & extended thinking
  - `cost_tracker.py`: LLM usage tracking and cost calculation
  - `link_enricher.py`: Adds internal navigation links to summaries
  - `ecosystem_context.py`: AI model release tracking for grounding

**agents/gatherers/:**
- Purpose: Source-specific data collection agents
- Contains: news_gatherer.py, research_gatherer.py, social_gatherer.py, reddit_gatherer.py
- Key files:
  - `base_gatherer.py`: Abstract base with date range handling
  - `news_gatherer.py`: RSS feeds + Twitter-linked articles with LLM filtering
  - `research_gatherer.py`: arXiv + LessWrong research blogs
  - `social_gatherer.py`: Twitter, Bluesky, Mastodon with platform status tracking
  - `reddit_gatherer.py`: Reddit JSON API client
  - `link_follower.py`: LLM-based link extraction from social posts

**agents/analyzers/:**
- Purpose: Category-specific LLM analysis with map-reduce
- Contains: news_analyzer.py, research_analyzer.py, social_analyzer.py, reddit_analyzer.py
- Key files:
  - `base_analyzer.py`: Abstract base with map-reduce batch processing
  - Each analyzer: MAP phase (batches of 75, STANDARD thinking), REDUCE phase (DEEP thinking)

**agents/continuity/:**
- Purpose: Detect story continuations across days
- Contains: coordinator.py, matcher.py, curator.py
- Key files:
  - `coordinator.py`: Main continuity detection coordinator
  - `matcher.py`: LLM-based matching of today's items to historical items
  - `curator.py`: Adds continuation metadata and reference text

**generators/:**
- Purpose: Transform analyzed data into frontend formats
- Contains: json_generator.py, feed_generator.py, search_indexer.py, hero_generator.py
- Key files:
  - `json_generator.py`: Generates summary.json, category.json files with HTML sanitization
  - `feed_generator.py`: Atom RSS feeds with Media RSS namespace
  - `search_indexer.py`: Builds Lunr.js search index with rolling 30-day window
  - `hero_generator.py`: Daily hero images via Gemini 3 Pro with AATF skunk mascot

**frontend/src/lib/components/:**
- Purpose: Reusable Svelte 5 UI components
- Contains: layout/, calendar/, news/, search/, common/
- Structure:
  - `layout/`: Header, Navigation, Footer, ThemeToggle, HeroSection
  - `calendar/`: Calendar, DateNavigator, CalendarDay
  - `news/`: NewsCard, NewsList, TopicCard, CategoryBadge
  - `search/`: SearchBar, SearchResults, SearchHighlight
  - `common/`: LoadingSpinner, ErrorMessage, EmptyState

**frontend/src/lib/services/:**
- Purpose: Data fetching and client-side utilities
- Contains: dataLoader.ts, searchIndex.ts, dateUtils.ts, markdown.ts, sanitize.ts
- Key files:
  - `dataLoader.ts`: Fetch JSON with caching, preload adjacent dates
  - `searchIndex.ts`: Lunr.js integration for client-side search
  - `dateUtils.ts`: Date parsing, formatting, navigation helpers
  - `safeHtml.ts`: HTML sanitization for user-facing content

**frontend/src/lib/stores/:**
- Purpose: Svelte stores for global state
- Contains: dateStore.ts, themeStore.ts
- Key files:
  - `dateStore.ts`: Current date, available dates, navigation actions with URL query params
  - `themeStore.ts`: Dark/light mode toggle with localStorage persistence

**frontend/src/routes/:**
- Purpose: SvelteKit file-based routing
- Contains: +page.svelte (home), archive/+page.svelte, feeds/+page.svelte
- Key files:
  - `+page.svelte`: Main route, handles both date overview and category views via query params
  - `+layout.svelte`: Root layout, initializes date store, theme
  - `archive/+page.svelte`: Calendar browser for all available dates
  - `feeds/+page.svelte`: RSS feed directory with subscribe links

**config/:**
- Purpose: Configuration files for data sources
- Contains: Feed lists, account lists, model releases
- Key files:
  - `rss_feeds.txt`: RSS feed URLs (one per line)
  - `twitter_accounts.txt`: Twitter usernames
  - `bluesky_accounts.txt`: Bluesky handles
  - `mastodon_accounts.txt`: Mastodon accounts (username@instance)
  - `reddit_subreddits.txt`: Reddit subreddit names
  - `research_feeds.txt`: LessWrong, AI Alignment Forum
  - `model_releases.yaml`: Curated AI model release dates (source of truth)
  - `ecosystem_context.yaml`: Auto-generated cache (merged releases + OpenRouter)

**data/:**
- Purpose: Runtime data storage
- Contains: raw/, processed/, admin/
- Structure:
  - `raw/`: Collected JSON from gatherers (`{category}_items_{date}.json`)
  - `processed/`: Analysis results from orchestrator (`orchestrator_result_{date}.json`)
  - `admin/`: Pipeline run history, scheduling data

**web/data/:**
- Purpose: Generated JSON for frontend consumption
- Contains: Date-specific directories, feeds/, index.json, search-index.json
- Structure:
  - `{date}/`: summary.json, {category}.json, hero.webp
  - `feeds/`: Atom RSS feeds (main.xml, summaries*.xml, category feeds)
  - `index.json`: Date manifest with available dates
  - `search-index.json`, `search-documents.json`: Lunr.js index

**scripts/:**
- Purpose: Maintenance and utility scripts
- Contains: regenerate_hero.py, cleanup scripts, conversion utilities
- Key files:
  - `regenerate_hero.py`: Manual hero image regeneration for specific dates

**assets/:**
- Purpose: Reference assets for generators
- Contains: skunk-reference.png (AATF mascot for hero images)

## Key File Locations

**Entry Points:**
- `run_pipeline.py`: CLI entry point for pipeline execution
- `entrypoint.sh`: Docker container entry point
- `frontend/src/routes/+page.svelte`: Frontend main route

**Configuration:**
- `.env`: Environment variables (API keys, model config)
- `docker-compose.yml`: Production deployment config
- `nginx.conf`: Static file serving config for Docker
- `frontend/svelte.config.js`: SvelteKit configuration
- `frontend/tailwind.config.js`: Tailwind CSS theme (Trend Red #E63946)

**Core Logic:**
- `agents/orchestrator.py`: Pipeline coordination (7 phases)
- `agents/base.py`: Base classes and dataclasses
- `agents/llm_client.py`: Anthropic client with extended thinking
- `generators/json_generator.py`: JSON data generation with sanitization
- `frontend/src/lib/stores/dateStore.ts`: Date navigation state

**Testing:**
- `test_news_filter.py`: News gatherer link filtering tests
- `test_news_analyzer.py`: News analyzer prompt tests
- No unit test framework configured (manual testing only)

## Naming Conventions

**Files:**
- Python modules: snake_case (`news_gatherer.py`, `llm_client.py`)
- Svelte components: PascalCase (`NewsCard.svelte`, `DateNavigator.svelte`)
- TypeScript modules: camelCase (`dateStore.ts`, `dataLoader.ts`)
- Config files: lowercase with underscores (`rss_feeds.txt`, `twitter_accounts.txt`)
- Data files: descriptive with date (`orchestrator_result_2026-01-23.json`)

**Directories:**
- Python packages: lowercase (`agents`, `generators`)
- Frontend structure: lowercase (`components`, `services`, `stores`)
- Component categories: lowercase (`layout`, `calendar`, `news`)

**Variables/Functions:**
- Python: snake_case (`analyze_batch`, `build_items_context`)
- TypeScript/Svelte: camelCase (`loadDaySummary`, `navigateToDate`)
- Constants: UPPER_SNAKE_CASE (`BATCH_SIZE`, `MAX_CONCURRENT_BATCHES`)
- Classes: PascalCase (`MainOrchestrator`, `AnthropicClient`)

**Dataclasses:**
- Python: PascalCase (`CollectedItem`, `CategoryReport`, `TopTopic`)
- TypeScript interfaces: PascalCase (`DaySummary`, `CategoryData`)

## Where to Add New Code

**New Data Source:**
- Primary code: `agents/gatherers/{source}_gatherer.py` extending BaseGatherer
- Tests: `test_{source}_gatherer.py` in root
- Config: `config/{source}_accounts.txt` or similar
- Registration: Add to `MainOrchestrator.__init__()` in `agents/orchestrator.py`

**New Category:**
- Gatherer: `agents/gatherers/{category}_gatherer.py`
- Analyzer: `agents/analyzers/{category}_analyzer.py` extending BaseAnalyzer
- Frontend: Add to `CATEGORY_CONFIG` in `frontend/src/lib/types/index.ts`
- Orchestrator: Register in `MainOrchestrator.__init__()`

**New Generator:**
- Implementation: `generators/{generator_name}.py`
- Integration: Call from `run_pipeline.py` after orchestrator completes
- Output: Write to `web/data/` or `web/data/{date}/`

**New Frontend Feature:**
- Component: `frontend/src/lib/components/{category}/{Component}.svelte`
- Service: `frontend/src/lib/services/{service}.ts`
- Store: `frontend/src/lib/stores/{store}Store.ts`
- Route: `frontend/src/routes/{route}/+page.svelte`

**New Pipeline Phase:**
- Add phase method to `MainOrchestrator` in `agents/orchestrator.py`
- Call from `MainOrchestrator.run()` in appropriate sequence
- Update phase counter in log messages

**Utilities:**
- Python shared helpers: `agents/base.py` or new module in `agents/`
- Frontend utilities: `frontend/src/lib/services/`
- Scripts: `scripts/` for one-off maintenance tasks

## Special Directories

**frontend/.svelte-kit/:**
- Purpose: SvelteKit build artifacts
- Generated: Yes (by `npm run build`)
- Committed: No (.gitignore)

**frontend/node_modules/:**
- Purpose: NPM dependencies
- Generated: Yes (by `npm install`)
- Committed: No (.gitignore)

**venv/:**
- Purpose: Python virtual environment
- Generated: Yes (by `python3 -m venv venv`)
- Committed: No (.gitignore)

**web/_app/:**
- Purpose: Built frontend assets (JS, CSS chunks)
- Generated: Yes (by `npm run build` in frontend/)
- Committed: Yes (for static serving in Docker)

**web/data/:**
- Purpose: Generated JSON data and hero images
- Generated: Yes (by pipeline generators)
- Committed: Yes (historical data)

**data/raw/, data/processed/:**
- Purpose: Intermediate pipeline data
- Generated: Yes (by pipeline)
- Committed: No (.gitignore)

**.planning/:**
- Purpose: GSD codebase analysis documents
- Generated: Yes (by /gsd:map-codebase)
- Committed: Yes (for GSD commands)

**logs/:**
- Purpose: Pipeline execution logs
- Generated: Yes (by pipeline runs)
- Committed: No (.gitignore)

---

*Structure analysis: 2026-01-24*
