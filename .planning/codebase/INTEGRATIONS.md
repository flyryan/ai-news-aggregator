# External Integrations

**Analysis Date:** 2026-01-24

## APIs & External Services

**LLM/AI Services:**
- **Anthropic Claude API** - Extended thinking analysis and content generation
  - SDK/Client: `anthropic>=0.40.0` with custom `httpx` transport
  - Auth: Bearer token via `ANTHROPIC_API_KEY` env var
  - Endpoint: `ANTHROPIC_API_BASE` env var (no `/v1` suffix)
  - Model: `ANTHROPIC_MODEL` (default: `claude-opus-4-5-20251101`)
  - Features: Extended thinking budgets (4K-32K tokens), cost tracking
  - Implementation: `agents/llm_client.py` (AnthropicClient, AsyncAnthropicClient)

- **RDSec Gemini API** - Hero image generation with AATF skunk mascot
  - Endpoint: `https://api.rdsec.trendmicro.com/prod/aiendpoint/v1/chat/completions`
  - Model: `gemini-3-pro-image`
  - Auth: Bearer token via `ANTHROPIC_API_KEY` (same as Claude)
  - Implementation: `generators/hero_generator.py`

- **OpenRouter API** - Model discovery and API availability dates
  - Endpoint: `https://openrouter.ai/api/v1/models`
  - Auth: None (free, public endpoint)
  - Purpose: Ecosystem context enrichment (AI model release tracking)
  - Implementation: `agents/ecosystem_context.py`

**Social Media:**
- **TwitterAPI.io** - Twitter/X data collection (paid service)
  - SDK/Client: Direct REST API via `requests`
  - Auth: `TWITTERAPI_IO_KEY` env var (`X-API-Key` header)
  - Endpoint: `https://api.twitterapi.io`
  - Pricing: $0.15/1000 tweets
  - Accounts: `config/twitter_accounts.txt`
  - Implementation: `agents/gatherers/social_gatherer.py`

- **Bluesky Public API** - Social posts (free)
  - Endpoint: `https://{instance}/xrpc/app.bsky.feed.getAuthorFeed`
  - Auth: None (public API)
  - Accounts: `config/bluesky_accounts.txt`
  - Implementation: `agents/gatherers/social_gatherer.py`

- **Mastodon Public API** - Social posts (free)
  - Endpoint: `https://{instance}/api/v1/accounts/{id}/statuses`
  - Auth: None (public API)
  - Accounts: `config/mastodon_accounts.txt` (format: `username@instance.social`)
  - Implementation: `agents/gatherers/social_gatherer.py`

**Content Sources:**
- **arXiv** - Research paper metadata and abstracts
  - Primary: RSS feeds at `https://rss.arxiv.org/rss/{category}`
  - Fallback: OAI-PMH API at `https://export.arxiv.org/oai2`
  - Auth: None (public, rate-limited to 1 request per 3+ seconds)
  - Categories: cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, cs.RO, stat.ML
  - Implementation: `agents/gatherers/research_gatherer.py`, `agents/gatherers/arxiv_oai.py`

- **RSS/Atom Feeds** - News articles and research blogs
  - Client: `feedparser==6.0.11`
  - News feeds: `config/rss_feeds.txt` (TechCrunch, VentureBeat, etc.)
  - Research feeds: `config/research_feeds.txt` (LessWrong, AI Alignment Forum)
  - Implementation: `agents/gatherers/news_gatherer.py`, `agents/gatherers/research_gatherer.py`

- **Reddit JSON API** - Community discussions (free)
  - Endpoint: `https://www.reddit.com/r/{subreddit}/new/.json`
  - Auth: None (public endpoint with User-Agent required)
  - User-Agent: `AI-News-Aggregator/1.0`
  - Subreddits: `config/reddit_subreddits.txt`
  - Implementation: `agents/gatherers/reddit_gatherer.py`

## Data Storage

**Databases:**
- None (filesystem-based JSON storage)

**File Storage:**
- Local filesystem only
  - Raw data: `data/raw/{date}/{category}.json`
  - Processed data: `data/processed/{date}/{category}.json`
  - Web output: `web/data/{date}/*.json` and `web/data/feeds/*.xml`
  - Hero images: `web/data/{date}/hero.webp`
  - Search indexes: `web/data/search-index.json`, `web/data/search-documents.json`
  - Static assets: `assets/skunk-reference.png`

**Caching:**
- Ecosystem context cache: `config/ecosystem_context.yaml` (auto-generated, merges curated + OpenRouter data)

## Authentication & Identity

**Auth Provider:**
- None (no user authentication)

**API Authentication:**
- Anthropic/RDSec: Bearer token authentication via custom `httpx.Auth` handler
- TwitterAPI.io: API key header (`X-API-Key`)
- Other services: Public/unauthenticated endpoints

## Monitoring & Observability

**Error Tracking:**
- None

**Logs:**
- Python logging to stdout (Docker captures to logs volume)
- nginx access/error logs: `/app/logs/nginx-access.log`, `/app/logs/nginx-error.log`
- Cron logs: `/app/logs/cron.log` (when `ENABLE_CRON=true`)
- Log level: INFO (configurable via `logging.basicConfig`)

**Cost Tracking:**
- LLM API usage tracked in `agents/cost_tracker.py`
- Per-request token counts and cost estimates
- Cumulative daily cost reporting

## CI/CD & Deployment

**Hosting:**
- Self-hosted Docker container
- nginx serving static SPA on port 7100 (mapped from internal port 80)

**CI Pipeline:**
- None

**Deployment:**
- Manual via `docker-compose build && docker-compose up -d`
- Two deployment branches: `main` (development), `backend-control` (production)
- Must sync branches: `git checkout backend-control && git merge main --no-edit && git push`

## Environment Configuration

**Required env vars:**
- `ANTHROPIC_API_BASE` - Anthropic API endpoint (no `/v1` suffix)
- `ANTHROPIC_API_KEY` - Bearer token for Claude and Gemini APIs

**Optional env vars:**
- `ANTHROPIC_MODEL` - Model name (default: `claude-opus-4-5-20251101`)
- `TWITTERAPI_IO_KEY` - Twitter API key (skip Twitter if not set)
- `TARGET_DATE` - Report date YYYY-MM-DD (default: today)
- `LOOKBACK_HOURS` - Data collection window (default: 24)
- `ENABLE_CRON` - Enable scheduled collection (default: `false`)
- `COLLECTION_SCHEDULE` - Cron schedule (default: `0 6 * * *`)
- `TZ` - Timezone (default: `America/New_York`)

**Secrets location:**
- Development: `.env` file in project root (not committed)
- Production: Docker environment variables in `docker-compose.yml` or host environment

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

---

*Integration audit: 2026-01-24*
