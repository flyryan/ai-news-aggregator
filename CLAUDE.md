# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI News Aggregator - A Python-based multi-agent pipeline that collects AI/ML news from multiple sources (RSS feeds, arXiv API, Twitter, Reddit, Bluesky, Mastodon), analyzes them using Claude Opus 4.6 with extended thinking, and serves a modern Svelte SPA frontend with AATF branding.

**Testing:** The user always runs tests themselves. Do not run the pipeline or tests unless explicitly asked.

## Commands

### Docker (Production)
```bash
docker-compose build                    # Build container
docker-compose up -d                    # Start services (serves existing content only)
docker-compose down                     # Stop services
docker logs ai-news-aggregator          # View container logs

# Manual pipeline run (trigger data collection)
docker exec ai-news-aggregator python3 /app/run_pipeline.py --config-dir /app/config --data-dir /app/data --web-dir /app/web

# Enable scheduled collection (cron)
ENABLE_CRON=true docker-compose up -d
```

### Local Development (Pipeline)
```bash
source venv/bin/activate                            # Activate virtual environment
pip install -r requirements.txt                     # Install dependencies
python3 run_pipeline.py --create-config             # Generate default config
python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web

# Run for a specific date (useful for testing/backfilling)
TARGET_DATE="2026-01-02" python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web
```

### Frontend Development
```bash
cd frontend
npm install                     # Install dependencies
npm run dev                     # Start dev server at http://localhost:5173
npm run build                   # Build production (outputs to ../web)
npm run preview                 # Preview production build
npm run check                   # TypeScript type checking
```

There are no unit tests, linting, or type checking configured.

## Architecture

### Multi-Agent Pipeline (run_pipeline.py)

```
Phase 0: Ecosystem Context Initialization
    ↓
Phase 1: Parallel Gathering (4 gatherers)
    ↓
Phase 2: Parallel Analysis (4 analyzers with grounding context)
    ↓
Phase 3: Cross-Category Topic Detection (ULTRATHINK)
    ↓
Phase 4: Executive Summary Generation
    ↓
Phase 4.5: Link Enrichment (adds internal links to summaries)
    ↓
Phase 4.6: Ecosystem Enrichment (detect new model releases)
    ↓
Phase 4.7: Hero Image Generation (Gemini 3 Pro via configured provider)
    ↓
Phase 5: Assembly & Output
    ↓
Phase 6: JSON Data Generation (for SPA frontend)
    ↓
Phase 6.5: RSS Feed Generation (Atom 1.0 with Media RSS)
    ↓
Phase 7: Search Index Update (Lunr.js compatible)
```

### Agent Pairs

| Agent Pair | Gatherer Sources | Analysis Focus |
|------------|------------------|----------------|
| **News** | RSS feeds + articles from Twitter links | Product releases, company news |
| **Research** | arXiv API + research blogs (LessWrong) | Research findings, breakthroughs |
| **Social** | Twitter, Bluesky, Mastodon | Industry discussions, reactions |
| **Reddit** | Reddit JSON API | Community discussions, debates |

### Directory Structure

```
agents/
├── __init__.py
├── llm_client.py              # Anthropic client with extended thinking
├── base.py                    # Base classes (BaseGatherer, BaseAnalyzer)
├── orchestrator.py            # Main coordinator
├── link_enricher.py           # Adds internal links to summaries
├── cost_tracker.py            # LLM API cost tracking
├── ecosystem_context.py       # AI model release tracking for grounding
├── gatherers/
│   ├── news_gatherer.py       # RSS + Twitter-linked articles
│   ├── research_gatherer.py   # arXiv + research blogs (LessWrong)
│   ├── social_gatherer.py     # Twitter, Bluesky, Mastodon (with status tracking)
│   ├── reddit_gatherer.py     # Reddit
│   └── link_follower.py       # Smart link extraction from social posts
└── analyzers/
    ├── news_analyzer.py
    ├── research_analyzer.py
    ├── social_analyzer.py
    └── reddit_analyzer.py

generators/
├── json_generator.py          # Generates JSON data for SPA frontend
├── search_indexer.py          # Builds Lunr.js search index
├── hero_generator.py          # Daily hero image with skunk mascot
└── feed_generator.py          # Atom RSS feeds with Media RSS support

scripts/
└── regenerate_hero.py         # Manual hero image regeneration

assets/
└── skunk-reference.png        # AATF skunk mascot reference image

frontend/                       # Svelte SPA frontend
├── src/
│   ├── lib/
│   │   ├── components/        # Svelte components
│   │   ├── stores/            # State management
│   │   ├── services/          # Data loading, search
│   │   └── types/             # TypeScript types
│   └── routes/                # SvelteKit file-based routing
├── static/assets/             # Static assets (logo, etc.)
├── svelte.config.js
├── tailwind.config.js
└── package.json
```

### Key Files
- `run_pipeline.py` - Async entry point using MainOrchestrator
- `agents/orchestrator.py` - Main coordinator for all agents
- `agents/llm_client.py` - Anthropic SDK with extended thinking
- `agents/link_enricher.py` - Adds internal links to summaries using LLM
- `agents/cost_tracker.py` - Tracks LLM API usage and costs
- `agents/ecosystem_context.py` - Model release tracking for LLM grounding
- `generators/json_generator.py` - JSON data for SPA frontend
- `generators/search_indexer.py` - Builds Lunr.js search index
- `generators/hero_generator.py` - Daily hero image generation via Gemini
- `generators/feed_generator.py` - Atom RSS feeds with Media RSS namespace
- `scripts/regenerate_hero.py` - Manual hero image regeneration script
- `config/` - Feed lists (rss_feeds.txt, twitter_accounts.txt, etc.)
- `config/model_releases.yaml` - Curated AI model release dates (source of truth)
- `config/ecosystem_context.yaml` - Auto-generated cache (merged releases + OpenRouter)
- `data/raw/` - Collected JSON, `data/processed/` - Analyzed JSON
- `web/data/` - Generated JSON data for frontend

### External Dependencies
- **Anthropic SDK** - Direct Claude API with extended thinking (Bearer auth)
- **TwitterAPI.io** - Twitter/X data collection ($0.15/1000 tweets)
- **Reddit JSON** - Free Reddit endpoint (add .json to Reddit URLs)
- **Bluesky Public API** - Free, no auth required
- **Mastodon Public API** - Free, no auth required
- **OpenRouter API** - Model discovery and API availability dates (free, no auth)

## Environment Variables

```
ANTHROPIC_API_BASE    # Anthropic API endpoint (no /v1 suffix)
ANTHROPIC_API_KEY     # Bearer token for authentication
ANTHROPIC_MODEL       # Model name (default: claude-4.6-opus-aws)
TWITTERAPI_IO_KEY     # TwitterAPI.io API key
TARGET_DATE           # Report date (YYYY-MM-DD), coverage is day before. Defaults to today.
ENABLE_CRON           # Enable scheduled collection (default: false)
COLLECTION_SCHEDULE   # Cron schedule (default: 0 6 * * *), requires ENABLE_CRON=true
LOOKBACK_HOURS        # Data window in hours (default: 24)
TZ                    # Timezone (default: America/New_York)
```

## Extended Thinking

The pipeline uses Claude's extended thinking feature with budget levels:

| Component | Thinking Level | Budget Tokens |
|-----------|---------------|---------------|
| Link relevance check | QUICK | 4,096 |
| Item summarization | QUICK | 4,096 |
| Category theme detection | STANDARD | 8,192 |
| Item ranking | DEEP | 16,000 |
| Cross-category topics | ULTRATHINK | 32,000 |
| Executive summary | DEEP | 16,000 |
| Link enrichment | STANDARD | 8,192 |
| Ecosystem enrichment | STANDARD | 8,192 |

## Ecosystem Context

The pipeline uses an ecosystem context system to ground LLM analysis with accurate model release dates. This prevents hallucinations like treating news about "GPT-5.2" as a new release when it was actually released weeks earlier.

### How It Works
- **Phase 0**: Loads curated `model_releases.yaml` and fetches fresh data from OpenRouter API
- **Phase 4.6**: Analyzes daily news to auto-detect new model releases and updates `model_releases.yaml`
- Grounding context is injected as a system prompt to all analyzers

### Data Sources
| Source | Purpose |
|--------|---------|
| `config/model_releases.yaml` | Curated GA dates (from Wikipedia, announcements) |
| OpenRouter API | API availability dates, new model discovery |
| Daily news (auto) | Phase 4.6 detects releases and updates curated file |

### Date Types
- **GA date**: General Availability - when model was publicly announced/released
- **API date**: When model became available via public APIs (OpenRouter, etc.)

### Adding/Updating Model Releases
Edit `config/model_releases.yaml` directly:
```yaml
openai:
  GPT-5.3:
    ga_date: "2026-01-20"   # From announcement/Wikipedia
    api_date: "2026-01-21"  # From OpenRouter or "unknown"
```

The enrichment phase (4.6) will also auto-add high-confidence releases detected in daily news.

### Generated Files
- `config/ecosystem_context.yaml` - Auto-generated cache merging curated + OpenRouter data. Do not edit manually; regenerated on each pipeline run.

## Hero Image Generation

Each daily report includes a hero image featuring the AATF skunk mascot in a scene representing the day's top stories.

### How It Works
- Uses Gemini 3 Pro Image API via configured provider
- Takes the skunk reference image (`assets/skunk-reference.png`) and all detected topics (typically 3-6)
- Generates a 21:9 ultra-wide banner image
- Outputs to `web/data/{date}/hero.webp` (optimized WebP at 1280px, q75)

### Prompt Design
The prompt includes:
1. **Mascot preservation**: Explicit instructions to keep the circuit board pattern on the skunk
2. **Story context**: Full topic descriptions (cleaned of markdown links) so the model understands the news
3. **Visual direction**: Keyword-to-visual mappings (e.g., "safety" → shields, "robotics" → robot arms)

### Manual Regeneration
```bash
# Regenerate hero for a specific date
python3 scripts/regenerate_hero.py 2026-01-06

# With custom prompt override
python3 scripts/regenerate_hero.py 2026-01-06 --prompt "Custom scene description"
```

## RSS Feeds

The pipeline generates Atom 1.0 RSS feeds with Media RSS namespace support for thumbnail images.

### Feed Types

| Feed | File | Content |
|------|------|---------|
| **Main Feed** | `main.xml` | Executive summary + top 5 items per category (recommended) |
| **Daily Briefing** | `summaries-executive.xml` | Executive summaries only with hero image (most popular) |
| **All Summaries** | `summaries.xml` | Executive + all 4 category summaries per day |
| **News Summaries** | `summaries-news.xml` | News category summaries only |
| **Research Summaries** | `summaries-research.xml` | Research category summaries only |
| **Social Summaries** | `summaries-social.xml` | Social category summaries only |
| **Reddit Summaries** | `summaries-reddit.xml` | Reddit category summaries only |
| **News** | `news.xml` | All news items |
| **Research** | `research-{25,50,100,full}.xml` | Research items (configurable count) |
| **Social** | `social-{25,50,100,full}.xml` | Social items (configurable count) |
| **Reddit** | `reddit-{25,50,100,full}.xml` | Reddit items (configurable count) |

### Hero Image in Feeds

Executive summary entries include the hero image via:
- `<media:thumbnail>` element (for Feedly and compatible readers)
- Inline `<img>` tag in HTML content (fallback for basic readers)

Requires Media RSS namespace: `xmlns:media="http://search.yahoo.com/mrss/"`

### Manual Feed Regeneration
```bash
# Regenerate feeds for last 30 days
source venv/bin/activate
python3 generators/feed_generator.py web/ 30
```

### Feed Location
Feeds are output to `web/data/feeds/` and accessible at `/data/feeds/*.xml` on the frontend.

## Important Notes

- **arXiv**: Uses arXiv RSS feeds for today's collection (no rate limits, more reliable) with automatic API fallback. For historical dates, uses API directly since RSS only contains current announcements. Only collects papers with `announce_type` of "new" or "cross" (skips replacements). arXiv only publishes papers on weekdays (Mon-Fri). Weekend dates will return 0 papers.
- **Link Following**: The News gatherer receives social posts and uses LLM to decide which linked articles to fetch.
- **Link Enrichment**: Executive summaries, category summaries, and topic descriptions are enriched with internal links to referenced items. Links use format `/?date={date}&category={category}#item-{id}`.
- **Date Semantics**: TARGET_DATE represents the report date. Coverage period is the day BEFORE the report date (00:00-23:59 ET). For example, TARGET_DATE=2026-01-05 generates a "January 5th report" covering news from January 4th.
- **Collection Status**: Each gatherer tracks success/partial/failed status. Social gatherer tracks per-platform status (Twitter, Bluesky, Mastodon). Status is logged at end of run and included in JSON output for frontend display.
- **Output Quality**: LLM prompts are tuned for factual, briefing-style output. Avoid generic "thought leader" language.
- **Source Diversity**: The ranking algorithm prioritizes news articles (RSS, arXiv) over social discussions (Reddit) to ensure top stories reflect actual developments.
- **Item IDs**: Generated as 12-character SHA256 hashes (~280 trillion unique values) for compact URLs.
- **Ecosystem Grounding**: All analyzers receive model release dates as system context to prevent hallucinations about "new" releases that are actually weeks/months old.

## Adding New Sources

- RSS feeds: Add URLs to `config/rss_feeds.txt` (one per line)
- Research blogs: Add URLs to `config/research_feeds.txt` (LessWrong, AI Alignment Forum, etc.)
- Bluesky: Add handles to `config/bluesky_accounts.txt` (e.g., `karpathy.bsky.social`)
- Mastodon: Add accounts to `config/mastodon_accounts.txt` (format: `username@instance.social`)
- Twitter: Add usernames to `config/twitter_accounts.txt` (requires TWITTERAPI_IO_KEY)
- Reddit: Add subreddits to `config/reddit_subreddits.txt` (free, no API key needed)

## Adding a New Agent

### Creating a Gatherer
Create a new file in `agents/gatherers/` following the pattern:
- Extend `BaseGatherer` from `agents/base.py`
- Implement `async gather()` method returning `List[CollectedItem]`
- Add to `MainOrchestrator.__init__()` in `agents/orchestrator.py`

### Creating an Analyzer
Create a new file in `agents/analyzers/` following the pattern:
- Extend `BaseAnalyzer` from `agents/base.py`
- Implement `async analyze(items)` returning `CategoryReport`
- Use `self.llm_client.call_with_thinking()` for analysis
- Add to `MainOrchestrator.__init__()` in `agents/orchestrator.py`

## SPA Frontend

The Svelte 5 + SvelteKit SPA frontend provides:
- **AATF Branding**: Trend Red (#E63946) color scheme, skunk logo
- **Calendar Navigation**: Interactive date picker, prev/next navigation
- **Full-text Search**: Client-side search using pre-built Lunr.js indexes
- **Dark Mode**: System-aware theme toggle with manual override
- **Responsive Design**: Mobile-first with Tailwind CSS

### Frontend Components

```
frontend/src/lib/
├── components/
│   ├── layout/
│   │   ├── Header.svelte       # Logo, title, date display, search toggle
│   │   ├── Navigation.svelte   # Category nav with date-aware links
│   │   ├── Footer.svelte       # Attribution
│   │   ├── ThemeToggle.svelte  # Dark/light mode toggle
│   │   └── HeroSection.svelte  # Daily hero image banner
│   ├── calendar/
│   │   ├── Calendar.svelte     # Month view calendar picker
│   │   └── DateNavigator.svelte # Prev/next date controls
│   ├── news/
│   │   ├── NewsCard.svelte     # Individual item card
│   │   ├── NewsList.svelte     # List of items
│   │   └── TopicCard.svelte    # Top topic display
│   ├── search/
│   │   ├── SearchBar.svelte    # Search input with category filter
│   │   └── SearchResults.svelte # Search results dropdown
│   └── common/
│       ├── LoadingSpinner.svelte
│       ├── ErrorMessage.svelte
│       └── EmptyState.svelte
├── stores/
│   ├── dateStore.ts            # Current date, available dates, navigation
│   └── themeStore.ts           # Dark/light mode state
├── services/
│   ├── dataLoader.ts           # Fetch JSON data with caching
│   ├── searchIndex.ts          # Lunr.js search integration
│   └── dateUtils.ts            # Date formatting helpers
└── types/
    └── index.ts                # TypeScript interfaces
```

### JSON Data Structure

Data is output to `web/data/`. The dev server serves from there via Vite alias.

```
web/data/
├── index.json              # Date manifest (list of available dates)
├── search-index.json       # Pre-built Lunr.js index
├── search-documents.json   # Document lookup for search results
├── feeds/                  # Atom RSS feeds
│   ├── main.xml            # Main feed (executive + top items)
│   ├── summaries*.xml      # Summary-only feeds (6 variants)
│   ├── news.xml            # All news items
│   ├── research-*.xml      # Research feeds (25/50/100/full)
│   ├── social-*.xml        # Social feeds (25/50/100/full)
│   └── reddit-*.xml        # Reddit feeds (25/50/100/full)
└── {date}/
    ├── summary.json        # Executive summary + top items per category + coverage info
    ├── hero.webp           # Daily hero image with skunk mascot
    ├── news.json           # Full news items
    ├── research.json       # Full research items (arXiv + blogs)
    ├── social.json         # Full social items
    └── reddit.json         # Full reddit items

### summary.json includes:
- `date`: Report date (YYYY-MM-DD)
- `coverage_date`: Date of news coverage (day before report date)
- `coverage_start`: ISO datetime for coverage start
- `coverage_end`: ISO datetime for coverage end
- `hero_image_url`: Relative URL to hero image (e.g., `/data/2026-01-05/hero.webp`)
- `hero_image_prompt`: Prompt used to generate the hero image
```

### URL Routing

Uses query parameters for bookmarkable/shareable URLs:

| Route | Content |
|-------|---------|
| `/` | Redirects to `/?date=LATEST` |
| `/?date=2026-01-05` | Specific date overview |
| `/?date=2026-01-05&category=research` | Category page for date |
| `/archive` | Calendar browser with all available dates |
| `/feeds` | RSS feed directory with subscribe links |

Legacy path-based URLs (`/{date}` and `/{date}/{category}`) are automatically redirected to query param format.

### Route Validation

- Date param validated as YYYY-MM-DD format, invalid dates redirect to home
- Category param validated against valid categories (news, research, social, reddit)
- Navigation links are disabled until date store is initialized
