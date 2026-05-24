# AI News Aggregator

![Pipeline Banner](assets/pipeline-banner.webp)

> Multi-agent AI news pipeline powered by Claude Opus 4.7 with adaptive thinking

> **Live Site:** [https://news.aatf.ai](https://news.aatf.ai)

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)

Daily AI/ML news briefings curated by specialized agents with extended thinking. Updated every morning at 6 AM ET.

---

## Navigation

| Section | Description |
|---------|-------------|
| [What It Does](#what-it-does) | Key stats and capabilities |
| [How It Works](#how-it-works) | Pipeline phases, thinking levels, architecture |
| [Quick Start](#quick-start) | Docker and local setup |
| [Configuration](#configuration) | Provider modes, prompts, data sources |
| [Daily Automation](#daily-automation) | GitHub Actions publication workflow |
| [Features](#features) | Multi-agent, continuity detection, frontend |
| [Architecture](#architecture) | Directory structure, agent pairs, data output |
| [Frontend Development](#frontend-development) | Dev server, build, URL routes |
| [Operational Notes](#operational-notes) | arXiv schedule, date semantics |
| [Local Development](#local-development) | Pipeline dev, hero regeneration |
| [Contributing](#contributing) | How to contribute |

---

## What It Does

A Python-based pipeline that collects AI/ML news from multiple sources, analyzes them using specialized agents with Claude's extended thinking, and serves a modern Svelte SPA frontend.

**Key Stats:**
- **100+ RSS feeds** from AI news sites, blogs, and research organizations
- **7 arXiv categories** (cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, cs.RO, stat.ML)
- **6 social platforms** (Twitter, Bluesky, Mastodon, Reddit, LessWrong, research blogs)
- **~80-85K thinking tokens** per daily run
- **Daily hero image** generated with AATF skunk mascot

---

## How It Works

![Pipeline Architecture](assets/pipeline-architecture.webp)

### The Multi-Phase Pipeline

| Phase | Description | Thinking Level |
|-------|-------------|----------------|
| **0. Ecosystem Context** | Load AI model release dates for LLM grounding | - |
| **1. Parallel Gathering** | 4 gatherers collect from RSS, arXiv, Twitter, Reddit, Bluesky, Mastodon | - |
| **2. Parallel Analysis** | MAP-REDUCE pattern: batch items (75 each), analyze, then synthesize | STANDARD (8K) → DEEP (16K) |
| **2.5. Continuity Detection** | Track developing stories, detect rehashes, link related coverage | - |
| **3. Cross-Category Topics** | Identify 3-6 themes spanning all categories | ULTRATHINK (32K) |
| **4. Executive Summary** | Generate daily briefing (500-800 words) | DEEP (16K) |
| **4.5. Link Enrichment** | Inject internal links to referenced items | STANDARD (8K) |
| **4.6. Ecosystem Enrichment** | Auto-detect new model releases from news | STANDARD (8K) |
| **4.7. Hero Image** | Generate branded banner with Gemini 3 Pro | - |
| **5-7. Output** | JSON data generation + RSS feeds + Lunr.js search index | - |

### Extended Thinking Levels

| Level | Budget | Use Case |
|-------|--------|----------|
| QUICK | 4,096 tokens | Link relevance decisions |
| STANDARD | 8,192 tokens | Batch analysis, link enrichment |
| DEEP | 16,000 tokens | Category ranking, executive summary |
| ULTRATHINK | 32,000 tokens | Cross-category topic detection |

### Agent Architecture

![Agent Architecture](assets/agent-architecture.webp)

---

## Quick Start

### Option A: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/flyryan/ai-news-aggregator.git
cd ai-news-aggregator

# Create config file
cp config/providers.yaml.example config/providers.yaml
# Edit config/providers.yaml with your API keys

# Build and run
docker-compose build
docker-compose up -d
```

Open [http://localhost:8080](http://localhost:8080)

### Option B: Local Development

```bash
# Clone and setup
git clone https://github.com/trend-ai-acceleration-task-force/ai-news-aggregator.git
cd ai-news-aggregator

# Python setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create config
cp config/providers.yaml.example config/providers.yaml
# Edit config/providers.yaml with your API keys

# Run pipeline
python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web

# Frontend development (separate terminal)
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```


### Option C: Web-Only Docker (Recommended for AWS / VPS)

If you only need to **serve the frontend** (pipeline runs elsewhere and pushes data via git), use the lightweight web-only image. It skips Python, Playwright, and all scraper dependencies — resulting in a ~50 MB image instead of ~2 GB.

```bash
# Clone the repository
git clone https://github.com/flyryan/ai-news-aggregator.git
cd ai-news-aggregator

# Build and run (web-only)
docker-compose -f docker-compose.web.yml up -d --build
```

The web-only image uses `nginx:alpine` and mounts `web/data` and `web/assets` as volumes so a `git pull` on the host picks up new pipeline data automatically.

Open [http://localhost:7100](http://localhost:7100)

---

## Utility Scripts

Two standalone helper scripts live in `scripts/` for operational debugging:

```bash
# Check the latest pipeline log and emit a human-readable summary
python3 scripts/pipeline_health.py

# Same report as structured JSON
python3 scripts/pipeline_health.py --json

# Warm a headless browser on LessWrong, cache cookies, and test GraphQL access
python3 scripts/lesswrong_cookie_fetch.py --after 2026-03-27 --before 2026-03-28
```

`lesswrong_cookie_fetch.py` exists because direct `requests` calls to LessWrong GraphQL may hit Vercel's bot challenge (HTTP 429), while a real browser context can sometimes pass. The script caches browser cookies in `~/.cache/lesswrong_cookies.json` and retries with those cookies before re-solving.

### Manual Pipeline Run

```bash
# Run pipeline (local)
python3 run_pipeline.py

# Run pipeline (Docker)
docker exec ai-news-aggregator python3 /app/run_pipeline.py

# Run for a specific date
python3 run_pipeline.py -d 2026-01-05

# Enable scheduled collection (cron, Docker only)
ENABLE_CRON=true docker-compose up -d

# Resume after a crash (auto-detects latest checkpoint)
python3 run_pipeline.py --resume

# Resume from a specific phase (loads earlier phases from checkpoint)
python3 run_pipeline.py --resume-from 3      # Re-run topic detection onward
python3 run_pipeline.py --resume-from 4.7    # Just regenerate hero image
```

## Daily Automation

The `flyryan/ai-news-aggregator` repository runs the pipeline daily with GitHub Actions. The workflow is intentionally guarded so scheduled runs only execute in that repository:

```yaml
if: github.repository == 'flyryan/ai-news-aggregator'
```

The AATF org mirror should not run the pipeline. The AWS mirror script also removes the flyryan-only workflow before force-pushing to the org repository.

### Schedule

GitHub Actions cron runs in UTC, so the workflow has two UTC entries and a local-time guard. Only the invocation that is actually `3 AM America/New_York` continues; the other exits as a no-op.

### Required Repository Secrets

Set these on the publishing repository:

| Secret | Purpose |
|--------|---------|
| `PIPELINE_PROVIDERS_YAML` | Full contents of ignored `config/providers.yaml`; preferred for production because it preserves the exact provider mode and image settings |
| `ANTHROPIC_API_KEY` | LLM/proxy API key, also used by the fallback generated provider config |
| `ANTHROPIC_API_BASE` | OpenAI-compatible proxy base URL when used |
| `TWITTERAPI_IO_KEY` | Optional Twitter/X collection |
| `REDDIT_CLIENT_ID` | Optional Reddit OAuth app client ID; recommended for GitHub-hosted runs |
| `REDDIT_CLIENT_SECRET` | Optional Reddit OAuth app secret; used with `REDDIT_CLIENT_ID` |
| `REDDIT_PROXY_URL` | Optional HTTP(S) or SOCKS proxy URL for Reddit requests if runner egress is blocked |
| `PIPELINE_PROXY_URL` | Optional HTTP(S) or SOCKS proxy URL for the whole pipeline; useful when hosted runner egress is blocked by multiple sources |
| `MULLVAD_ACCOUNT` | Optional Mullvad account number; used to create a WireGuard tunnel when no `PIPELINE_PROXY_URL` is set |
| `MULLVAD_WG_PRIVATE_KEY` | Optional stable WireGuard private key for the CI Mullvad device; avoids creating a new Mullvad device on every run |
| `GOOGLE_API_KEY` | Optional Gemini native image generation when not using a proxy image provider |
| `PIPELINE_PUSH_TOKEN` | Optional PAT if the default `GITHUB_TOKEN` is not enough for downstream webhook behavior |

### Optional Repository Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_MODEL` | `claude-opus-4-7` | Model name used by the fallback generated provider config |
| `PIPELINE_BASE_URL` | `https://news.aatf.ai` | Base URL used in feeds |
| `PIPELINE_IMAGE_MODEL` | `gemini-3-pro-image-preview` | Native Gemini image model used by fallback config |
| `PIPELINE_COMMIT_PATHS` | `web/data config/model_releases.yaml config/ecosystem_context.yaml` | Space-separated generated outputs to commit |
| `REDDIT_USER_AGENT` | `AI-News-Aggregator/1.0 (by u/flyryan)` | User-Agent sent to Reddit API requests |
| `NEWS_USER_AGENT` | `REDDIT_USER_AGENT` value | User-Agent sent to RSS/feed sources |
| `MULLVAD_RELAY_FILTER` | `us` | Mullvad WireGuard relay hostname prefix used for CI egress |

### Manual Dry Runs

Use `workflow_dispatch` with `commit_outputs=false` to run the full hosted pipeline without committing or pushing. The workflow uploads `web/data`, `config/model_releases.yaml`, and `config/ecosystem_context.yaml` as an artifact for inspection.

### Generated Outputs

The daily commit includes persistent generated site and grounding outputs:

- `web/data/**` for the frontend, search index, feeds, and hero images
- `config/model_releases.yaml` for curated and auto-detected model release facts
- `config/ecosystem_context.yaml` as the last successful OpenRouter-enriched grounding cache

Runtime scrape data, checkpoints, and logs under `data/**` and `logs/**` stay ignored. They are useful for local debugging but are not public site state.

### Reddit Collection on Hosted Runners

The Reddit gatherer prefers official app-only OAuth when `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are set, and then calls `https://oauth.reddit.com`. Without those credentials, it falls back to public `.json` endpoints.

If a CI provider's IP ranges are blocked only for Reddit, set `REDDIT_PROXY_URL` to an HTTP(S) or SOCKS proxy URL. If multiple sources block hosted runner egress, set `PIPELINE_PROXY_URL` instead; the workflow exports it as the standard `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` variables for the pipeline process. The RSS gatherer fetches feeds with `requests`, so SOCKS proxy URLs are honored when `PySocks` is installed.

The GitHub workflow also supports `MULLVAD_ACCOUNT`: when set and `PIPELINE_PROXY_URL` is empty, it creates a WireGuard tunnel with Mullvad's official `wg-tools` script, narrows the route to Mullvad's SOCKS proxy address, and sets `PIPELINE_PROXY_URL` plus `REDDIT_PROXY_URL` to `socks5h://10.64.0.1:1080` for the pipeline. Set `MULLVAD_WG_PRIVATE_KEY` to reuse one registered CI device across runs.

---

## Configuration

All configuration is done via `config/providers.yaml`. Copy the example file and customize:

```bash
cp config/providers.yaml.example config/providers.yaml
```

### LLM Provider

Supports two modes:

| Mode | Description | Auth | Extended Thinking |
|------|-------------|------|-------------------|
| `anthropic` (default) | Direct Anthropic API | x-api-key header | Full support |
| `openai-compatible` | LiteLLM, vLLM, or other proxies | Bearer token | May not be supported |

**Direct Anthropic API:**

```yaml
llm:
  mode: "anthropic"
  api_key: "${ANTHROPIC_API_KEY}"  # Use env var reference
  # base_url: "https://api.anthropic.com"  # Default, uncomment to override
  model: "claude-opus-4-7"
  timeout: 300
```

**OpenAI-compatible proxies (LiteLLM, etc.):**

```yaml
llm:
  mode: "openai-compatible"
  api_key: "${PROXY_API_KEY}"
  base_url: "https://your-litellm-proxy.example.com"
  model: "claude-opus-4-7"  # Your proxy's model alias
  timeout: 300
```

### Image Provider (Optional)

Hero image generation is optional. Comment out the entire `image:` section to skip.

| Mode | Description | Requirements |
|------|-------------|--------------|
| `native` (default) | Google Gemini API via google-genai SDK | Google AI API key |
| `openai-compatible` | OpenAI-compatible image endpoint | Proxy endpoint + key |

```yaml
image:
  mode: "native"
  api_key: "${GOOGLE_API_KEY}"
  model: "gemini-3-pro-image-preview"
```

If no image provider is configured, the pipeline runs successfully without hero images.

### Pipeline Settings

```yaml
pipeline:
  base_url: "http://localhost:8080"  # Your deployment URL (used in RSS feeds)
  lookback_hours: 24  # How far back to collect news
```

### Environment Variables

You can reference environment variables in your YAML config using `${VAR_NAME}` syntax:

```bash
export ANTHROPIC_API_KEY="your-key-here"
export GOOGLE_API_KEY="your-key-here"
export TWITTERAPI_IO_KEY="your-key-here"  # Optional, for Twitter collection
```

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes |
| `GOOGLE_API_KEY` | Google AI API key | No (hero images) |
| `TWITTERAPI_IO_KEY` | TwitterAPI.io key ($0.15/1000 tweets) | No |
| `REDDIT_CLIENT_ID` | Reddit app client ID for OAuth collection | No |
| `REDDIT_CLIENT_SECRET` | Reddit app client secret for OAuth collection | No |
| `REDDIT_PROXY_URL` | HTTP(S) or SOCKS proxy for Reddit requests | No |
| `REDDIT_USER_AGENT` | User-Agent for Reddit requests | No |
| `PIPELINE_PROXY_URL` | HTTP(S) or SOCKS proxy for the whole pipeline | No |
| `NEWS_USER_AGENT` | User-Agent for RSS/feed requests | No |
| `MULLVAD_ACCOUNT` | Mullvad account number for CI proxy setup | No |
| `MULLVAD_WG_PRIVATE_KEY` | Stable WireGuard private key for the CI Mullvad device | No |
| `MULLVAD_RELAY_FILTER` | Mullvad relay hostname prefix for CI tunnel selection | No |
| `TARGET_DATE` | Report date (YYYY-MM-DD) | No |
| `ENABLE_CRON` | Enable scheduled collection | No |
| `COLLECTION_SCHEDULE` | Cron schedule (default: `0 6 * * *`) | No |
| `TZ` | Timezone (default: `America/New_York`) | No |

### Prompt Customization

All LLM prompts are externalized to `config/prompts.yaml`. You can customize analysis behavior without changing code:

```yaml
# Example: Customize the executive summary prompt
orchestration:
  executive_summary: |
    Write a structured executive summary of today's AI news...

    FORMAT YOUR SUMMARY LIKE THIS:
    #### Top Story
    ...
```

Prompt categories:
- **gathering** - Link relevance decisions
- **analysis** - Category-specific analysis (news, research, social, reddit)
- **orchestration** - Cross-category topic detection, executive summary
- **post_processing** - Link enrichment, ecosystem enrichment

Variables use `${var}` syntax and are resolved at runtime.

### Adding Data Sources

Edit files in `config/`:

| Source Type | Config File | Format |
|-------------|-------------|--------|
| RSS feeds | `rss_feeds.txt` | One URL per line |
| Research blogs | `research_feeds.txt` | LessWrong, AI Alignment Forum URLs |
| Twitter | `twitter_accounts.txt` | Usernames (requires TWITTERAPI_IO_KEY) |
| Bluesky | `bluesky_accounts.txt` | Handles (e.g., `karpathy.bsky.social`) |
| Mastodon | `mastodon_accounts.txt` | Full addresses (e.g., `user@mastodon.social`) |
| Reddit | `reddit_subreddits.txt` | Subreddit names (public JSON fallback; OAuth recommended on hosted runners) |

### Model Release Tracking

The pipeline tracks AI model releases to ground LLM analysis:

```yaml
# config/model_releases.yaml
openai:
  GPT-5.2:
    ga_date: "2026-01-10"
    api_date: "2026-01-11"
```

Phase 4.6 auto-detects new releases from daily news and updates this file.

---

## Features

### Multi-Agent Architecture
- **4 Gatherer agents** collecting from different source types in parallel
- **4 Analyzer agents** with MAP-REDUCE batching for scalability
- **Continuity detection** tracks developing stories across days

### Continuity Detection
Automatically identifies when today's stories continue from previous coverage:
- **Continuation types**: `new_development` (builds on prior story), `mainstream_pickup` (gains wider attention), `community_reaction` (discussion response), `rehash` (repetitive coverage), `follow_up` (next chapter)
- **Smart ranking**: Items flagged as `rehash` can be demoted from top stories
- **2-day lookback**: Compares against items from the past 2 days

### Extended Thinking
- Configurable thinking budgets from 4K to 32K tokens
- ULTRATHINK mode for complex cross-category analysis
- **Cost tracking**: Per-phase breakdown with input/output/cache token tracking, logged at end of each run

### Ecosystem Grounding
Prevents hallucinations about AI model releases by injecting accurate release dates into analyzer prompts:
- **Dual date tracking**: GA (General Availability) date vs API date for each model
- **Curated source of truth**: `config/model_releases.yaml` with verified dates from Nov 2025+
- **OpenRouter integration**: Auto-discovers new models and API availability dates
- **Agent enrichment**: Phase 4.6 auto-detects new model releases from daily news and updates the context

### Collection Status Tracking
Each pipeline run tracks collection status per source:
- **Status values**: `success`, `partial` (some items collected), `failed`
- **Per-source tracking**: News, Research, Social, Reddit
- **Per-platform tracking**: Twitter, Bluesky, Mastodon (within Social)
- Status is included in `summary.json` and displayed in the frontend

### Pipeline Reliability
- **Phase tracking**: End-of-run summary showing status, timing, and details for every phase
- **Checkpoint/resume**: Each major phase saves a checkpoint to `data/checkpoints/`; use `--resume` for crash recovery or `--resume-from N` to re-run specific phases
- **Hero image fallback**: When topic detection fails, hero generation falls back to top category themes
- **Clean logging**: httpx noise suppressed; MAP-REDUCE batches show per-batch progress with category tags

### Data Sources

| Category | Sources | Collection Method |
|----------|---------|-------------------|
| **News** | 100+ RSS feeds + linked articles | RSS + LLM-guided link following |
| **Research** | arXiv (7 categories) + LessWrong | RSS/OAI-PMH + GraphQL API |
| **Social** | Twitter, Bluesky, Mastodon | TwitterAPI.io + free APIs |
| **Reddit** | Configurable subreddits | OAuth API or public JSON fallback |

### Frontend Features
- **AATF Branding** - Trend Red (#E63946) color scheme with skunk mascot
- **Calendar Navigation** - Browse historical reports by date
- **Full-text Search** - Client-side search using Lunr.js indexes
- **Dark Mode** - System-aware with manual toggle
- **Responsive Design** - Mobile-first with Tailwind CSS

### Daily Hero Image
Each report includes a generated hero image featuring the AATF skunk mascot in a scene representing the day's top stories, created via Gemini 3 Pro.

### RSS Feeds
Multiple Atom 1.0 feeds for different use cases:
- **Main Feed** - Executive summary + top 5 items per category
- **Daily Briefing** - Executive summaries only with hero image
- **Category Feeds** - News, Research, Social, Reddit separately
- **Summary Feeds** - All category summaries

---

## Architecture

### Directory Structure

```
ai-news-aggregator/
├── agents/
│   ├── llm_client.py          # Anthropic client with extended thinking
│   ├── base.py                # BaseGatherer, BaseAnalyzer classes
│   ├── orchestrator.py        # Main coordinator
│   ├── ecosystem_context.py   # AI model release dates for LLM grounding
│   ├── link_enricher.py       # Adds internal links to summaries
│   ├── cost_tracker.py        # LLM API cost tracking
│   ├── phase_tracker.py       # Phase status tracking and end-of-run summary
│   ├── gatherers/             # News, Research, Social, Reddit gatherers
│   ├── analyzers/             # Category-specific analyzers
│   └── continuity/            # Story tracking across days
├── generators/
│   ├── json_generator.py      # JSON data for SPA frontend
│   ├── search_indexer.py      # Lunr.js search index builder
│   ├── feed_generator.py      # Atom RSS feeds
│   └── hero_generator.py      # Daily hero image with skunk mascot
├── frontend/                  # Svelte SPA
│   ├── src/
│   │   ├── lib/components/    # UI components
│   │   ├── lib/stores/        # State management
│   │   ├── lib/services/      # Data loading, search
│   │   └── routes/            # SvelteKit routing
│   └── static/assets/         # Logo, fonts
├── config/
│   ├── providers.yaml         # Provider configuration
│   ├── prompts.yaml           # LLM prompts (customizable)
│   ├── rss_feeds.txt          # RSS feed URLs
│   ├── model_releases.yaml    # AI model release dates
│   └── ...                    # Other source lists
├── data/
│   ├── raw/                   # Collected JSON
│   ├── processed/             # Analyzed JSON + cost reports
│   └── checkpoints/           # Phase checkpoints for resume (per-date)
├── web/                       # Generated output
├── assets/                    # Pipeline diagrams
├── run_pipeline.py            # Entry point
├── Dockerfile
└── docker-compose.yml
```

### Agent Pairs

| Category | Gatherer | Analyzer Focus |
|----------|----------|----------------|
| **News** | RSS + linked articles from social | Product releases, company news |
| **Research** | arXiv + LessWrong GraphQL | Papers, breakthroughs |
| **Social** | Twitter, Bluesky, Mastodon | Discussions, reactions |
| **Reddit** | Reddit JSON API | Community debates |

### Data Output

```
web/data/
├── index.json              # Date manifest
├── search-index.json       # Lunr.js index (30-day window)
├── search-documents.json   # Document lookup
├── feeds/                  # Atom RSS feeds
│   ├── main.xml
│   ├── summaries-executive.xml
│   └── ...
└── {YYYY-MM-DD}/
    ├── summary.json        # Executive summary + top items
    ├── hero.webp           # Daily hero image
    ├── news.json           # Full news items
    ├── research.json       # Full research items
    ├── social.json         # Full social items
    └── reddit.json         # Full reddit items
```

---

## Frontend Development

```bash
cd frontend
npm install              # Install dependencies
npm run dev              # Start dev server (http://localhost:5173)
npm run build            # Build production (outputs to ../web)
npm run check            # TypeScript type checking
```

### URL Routes

| Route | Content |
|-------|---------|
| `/` | Redirects to latest date |
| `/?date=2026-01-05` | Specific date overview |
| `/?date=2026-01-05&category=research` | Category page |
| `/archive` | Calendar browser |
| `/feeds` | RSS feed directory |
| `/about` | Project info and AI disclaimer |

---

## Operational Notes

### arXiv Collection Schedule
- Papers announced Sun-Thu ~8PM ET
- **Sat/Sun reports**: Skip arXiv (no new papers)
- **Monday reports**: 3-day catchup (Sat-Mon announcements)

### Date Semantics
- `TARGET_DATE` = report date
- Coverage period = day BEFORE report date (00:00-23:59 ET)
- Example: `TARGET_DATE=2026-01-05` covers news from January 4th

### LessWrong Collection
Uses GraphQL API instead of RSS because RSS doesn't support date-range queries - only returns the ~10-20 most recent posts which scroll off within hours.

### Item IDs
12-character SHA256 hashes (~280 trillion unique values) for compact, stable URLs.

---

## Local Development

### Pipeline Development

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run pipeline
python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web
```

### Resuming Failed Runs

```bash
# Auto-resume from latest checkpoint (crash recovery)
python3 run_pipeline.py --resume

# Resume from a specific phase
python3 run_pipeline.py --resume-from 3      # Re-run from topic detection
python3 run_pipeline.py --resume-from 4.7    # Re-run hero image only
python3 run_pipeline.py --resume-from 2      # Re-run from analysis

# Checkpoints persist in data/checkpoints/{date}/
# Full run always saves fresh checkpoints
```

### Hero Image Regeneration

The `regenerate_hero.py` script regenerates hero images for daily reports.

```bash
# Basic usage (prompts for confirmation)
python3 scripts/regenerate_hero.py 2026-01-06

# Auto-confirm (no prompt)
python3 scripts/regenerate_hero.py 2026-01-06 -y

# With custom prompt override
python3 scripts/regenerate_hero.py 2026-01-06 --prompt "Custom scene description"

# Regenerate ALL dates
python3 scripts/regenerate_hero.py -a

# Skip specific dates or ranges
python3 scripts/regenerate_hero.py -a -s 2026-01-05              # Skip one date
python3 scripts/regenerate_hero.py -a -s 2026-01-05:2026-01-08   # Skip range (inclusive)
python3 scripts/regenerate_hero.py -a -s 2026-01-01,2026-01-05   # Skip multiple

# Parallel processing (faster for --all)
python3 scripts/regenerate_hero.py -a -t 4                        # 4 parallel threads

# Edit existing image instead of regenerating
python3 scripts/regenerate_hero.py 2026-01-06 -e "Add a coffee cup to the scene"
```

### Other Utility Scripts

| Script | Purpose |
|--------|---------|
| `daily_pipeline.sh` | Cron wrapper: pulls latest, runs pipeline, auto-commits and pushes results |
| `cleanup_external_links.py` | Strips external links from topic descriptions and re-enriches with internal links only |
| `convert_hero_images.py` | One-time migration: converts PNG hero images to WebP format |
| `patch_news_notice.py` | One-time: adds collection start notice to early dates |

---

## Requirements

- **Python 3.10+**
- **Node.js 18+** (for frontend development)
- **Docker & Docker Compose** (for containerized deployment)
- **Claude Opus 4.7** (recommended for best analysis quality)
- **Gemini 3 Pro** (optional, for hero image generation)

### API Keys

| Service | Required | Cost | Purpose |
|---------|----------|------|---------|
| Anthropic API | Yes | Pay-per-token | LLM analysis |
| Google AI | No | Pay-per-image | Hero images |
| TwitterAPI.io | No | $0.15/1000 tweets | Twitter collection |

---

## Contributing

Contributions are welcome!

- **Bug Reports**: [Open an issue](https://github.com/flyryan/ai-news-aggregator/issues)
- **Feature Requests**: [Open an issue](https://github.com/flyryan/ai-news-aggregator/issues)
- **Pull Requests**: Fork, make changes, submit PR

Please ensure your contributions maintain backwards compatibility with existing configurations.

---

## License

Apache License 2.0 - See [LICENSE](LICENSE) file for details.

Copyright 2026 AI Acceleration Task Force (AATF)

---

## Built by TrendAI

**AI Acceleration Task Force** | [TrendAI](https://www.trendmicro.com)

Originally built as an internal tool to keep our team informed about AI developments, now open-sourced so others can run their own instances.

---

**Interested in being a Trender?** [Join us!](https://www.trendmicro.com/en_us/about/careers.html)
