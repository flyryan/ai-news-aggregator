# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI News Aggregator - A Python-based multi-agent pipeline that collects AI/ML news from multiple sources (RSS feeds, arXiv API, Twitter, Reddit, Bluesky, Mastodon), analyzes them using Claude Opus 4.8 with adaptive thinking, and serves a modern Svelte SPA frontend with AATF branding.

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

# Resume after a crash (auto-detect latest checkpoint)
python3 run_pipeline.py --resume --config-dir ./config --data-dir ./data --web-dir ./web

# Resume from a specific phase (loads earlier phases from checkpoint)
python3 run_pipeline.py --resume-from 3 --config-dir ./config --data-dir ./data --web-dir ./web
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

### Web-Only Host Deployment
```bash
git fetch origin
git reset --hard origin/main
docker compose -f docker-compose.web.yml up -d --build
```

The production web host serves a web-only Docker image. `web/_app/` is intentionally ignored and built on the host, so do not commit rebuilt Svelte bundle files just to update the site. Data-only updates can be picked up by a git sync of `web/data`; frontend/source changes need the web-only image rebuild above.

## Daily Automation

The production publishing workflow lives in `.github/workflows/daily-pipeline.yml` and is guarded to run only in the configured publishing repository. Do not enable scheduled publishing in mirrors or forks unless the workflow guard, secrets, and output ownership have been intentionally reconfigured. The schedule uses two UTC cron entries with a local-time gate so exactly the nominal 3 AM ET invocation continues, even if GitHub starts the runner late.

The workflow writes ignored `config/providers.yaml` from the `PIPELINE_PROVIDERS_YAML` secret. `ANTHROPIC_MODEL` or the `anthropic_model` dispatch input only overrides legacy single-provider configs; it must not clobber `llm.routes`. The workflow runs the pipeline and commits only generated public outputs (`web/data`, `config/model_releases.yaml`, and `config/ecosystem_context.yaml`) when `commit_outputs=true`. Use `workflow_dispatch` with `commit_outputs=false` for a full hosted dry run that uploads artifacts without committing. Hosted runs also upload a `pipeline-diagnostics` artifact with LLM request metrics and cost reports when those files exist.

Hosted runner egress can be proxied with `PIPELINE_PROXY_URL` for all sources or `LESSWRONG_PROXY_URL` for LessWrong only. `REDDIT_PROXY_URL` is legacy: Reddit now collects via the ScrapeCreators API which unblocks server-side, so the Reddit gatherer goes direct (`requests` `trust_env=False`) and ignores both `REDDIT_PROXY_URL` and the pipeline-wide `ALL_PROXY` exports; use `SCRAPECREATORS_PROXY_URL` only if that specific traffic must be proxied. LLM clients ignore proxy environment variables by default because `LLM_TRUST_ENV_PROXY=false`; set it true only when LLM traffic should use the runner proxy too. If neither pipeline nor Reddit proxy URL is set and `MULLVAD_ACCOUNT` is configured, the workflow creates a Mullvad WireGuard tunnel and exposes Mullvad's local SOCKS proxy as both `PIPELINE_PROXY_URL` and `REDDIT_PROXY_URL`. `MULLVAD_WG_PRIVATE_KEY` pins CI to one registered Mullvad device across runs.

Use `scripts/post_pipeline_verify.sh` for hosted-site verification. It is configured with environment variables: set `AWS_HOST` directly, or set `AWS_PROFILE` plus `AWS_INSTANCE_ID`/`AWS_INSTANCE_NAME` for EC2 lookup. Set `REBUILD_WEB=true` when the deployment includes frontend source or web-image changes.

### Deploy Webhook Security

Pushing to the publishing repo's `main` triggers a deploy on the origin host via
an [`adnanh/webhook`](https://github.com/adnanh/webhook) listener (`webhook/hooks.json`,
git-ignored on the host; `webhook/hooks.example.json` is the tracked template).
The `deploy` hook runs `scripts/deploy.sh` and is **HMAC-gated** (CWE-306 fix,
2026-07-07): its `trigger-rule` requires a valid `X-Hub-Signature-256` (shared
secret set on both the GitHub repo webhook and the host `hooks.json`) **and**
`payload.ref == refs/heads/main`. When editing the hook, keep both conditions —
a missing `trigger-rule` means match-all/unauthenticated. Rotate the secret
GitHub-first, then the host, to avoid failed deliveries.

The origin is fronted by a Cloudflare tunnel (`cloudflared`, outbound-only); its
EC2 security group only allows `:22` inbound, so `:443`/`:9000` are reachable
only via `news.aatf.ai` / `webhook.aatf.ai` through Cloudflare, never at the raw
IP. See `docs/security/remediation-2026-07.md` for the full remediation record.

### Commit signing (CWE-345 deploy gate, 2026-07-07)

Commits that reach `main` MUST be SSH-signed by a trusted key, or
`scripts/deploy.sh` aborts the production deploy (it runs `git verify-commit` on
the `origin/main` tip against `deploy/allowed_signers.example`). This is not a
per-commit step — it is driven by git config, so once configured every
`git commit` signs automatically. On a **fresh clone or new machine**, configure
it before committing:

```bash
git config --global gpg.format ssh
git config --global commit.gpgsign true
git config --global user.signingkey ~/.ssh/id_flyryan.pub   # or your trusted key
```

CI signs via the `PIPELINE_SIGNING_KEY` secret in `daily-pipeline.yml`. Trusted
keys live in `deploy/allowed_signers.example`; add a new signer there and in the
host copy (`/home/ubuntu/deploy_allowed_signers`). If a deploy must ship an
unsigned tip, set `ALLOW_UNSIGNED_DEPLOY=1` (logged loudly). Full details:
`deploy/README.md`.

### Content Security Policy (split contract — CWE-693 fix, 2026-07-07)

Script policy lives ONLY in the SvelteKit build-time `<meta>` CSP
(`frontend/svelte.config.js` `kit.csp`, mode `hash`): each prerendered page
carries a per-page `sha256-` hash for its inline hydration script. The nginx
header (`nginx.conf`) intentionally has **no `script-src` and no
`default-src`** — header and meta CSPs are enforced simultaneously, so adding
either directive to nginx re-blocks the hashed inline script and blanks the
site. Keep `img-src 'self' data:` (no `https:` — beacon-exfil vector).
`scripts/check_csp.sh [BASE_URL]` verifies the contract; run it after any
nginx or svelte.config.js change. Item URLs are scheme-allowlisted
(http/https/mailto) both server-side (`generators/json_generator.py
_safe_url`) and in the frontend (`sanitize.ts isSafeUrl`).

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
Phase 7: Search Corpus Update (client-built MiniSearch index)
```

### Agent Pairs

| Agent Pair | Gatherer Sources | Analysis Focus |
|------------|------------------|----------------|
| **News** | RSS feeds + articles from Twitter links | Product releases, company news |
| **Research** | arXiv API + research blogs (LessWrong) | Research findings, breakthroughs |
| **Social** | Twitter, Bluesky, Mastodon | Industry discussions, reactions |
| **Reddit** | Reddit via ScrapeCreators API | Community discussions, debates |

### Directory Structure

```
agents/
├── __init__.py
├── llm_client.py              # Anthropic client with adaptive/manual thinking profiles
├── base.py                    # Base classes (BaseGatherer, BaseAnalyzer)
├── orchestrator.py            # Main coordinator
├── link_enricher.py           # Adds internal links to summaries
├── cost_tracker.py            # LLM API cost tracking
├── phase_tracker.py           # Phase status tracking and end-of-run summary
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
├── search_indexer.py          # Builds the MiniSearch corpus
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
- `agents/llm_client.py` - Anthropic SDK with adaptive/manual thinking profiles
- `agents/link_enricher.py` - Adds internal links to summaries using LLM
- `agents/cost_tracker.py` - Tracks LLM API usage and costs
- `agents/ecosystem_context.py` - Model release tracking for LLM grounding
- `agents/phase_tracker.py` - Phase status tracking, timing, and end-of-run summary
- `generators/json_generator.py` - JSON data for SPA frontend
- `generators/search_indexer.py` - Builds the MiniSearch corpus (single search-corpus.json)
- `generators/hero_generator.py` - Daily hero image generation via Gemini
- `generators/feed_generator.py` - Atom RSS feeds with Media RSS namespace
- `scripts/regenerate_hero.py` - Manual hero image regeneration script
- `config/` - Feed lists (rss_feeds.txt, twitter_accounts.txt, etc.)
- `config/model_releases.yaml` - Curated AI model release dates (source of truth)
- `config/ecosystem_context.yaml` - Auto-generated cache (merged releases + OpenRouter)
- `data/raw/` - Collected JSON, `data/processed/` - Analyzed JSON, `data/checkpoints/` - Phase checkpoints for resume
- `web/data/` - Generated JSON data for frontend

### External Dependencies
- **Anthropic SDK** - Direct Claude API with adaptive thinking support (Bearer auth)
- **TwitterAPI.io** - Twitter/X data collection ($0.15/1000 tweets)
- **ScrapeCreators API** - Reddit data collection (~$0.99/1000 calls; 1 call = 1 credit). Replaces the dead free Reddit `.json` endpoint; unblocks Reddit server-side. Requires `SCRAPECREATORS_API_KEY`.
- **Bluesky Public API** - Free, no auth required
- **Mastodon Public API** - Free, no auth required
- **OpenRouter API** - Model discovery and API availability dates (free, no auth)

## Environment Variables

```
ANTHROPIC_API_BASE    # Anthropic API endpoint (no /v1 suffix)
ANTHROPIC_API_KEY     # Bearer token for authentication
ANTHROPIC_MODEL       # Legacy single-provider model name (default: claude-4.8-opus-aws)
TWITTERAPI_IO_KEY     # TwitterAPI.io API key
SCRAPECREATORS_API_KEY # ScrapeCreators API key for Reddit collection (required for Reddit data)
SCRAPECREATORS_BASE   # ScrapeCreators base URL (default: https://api.scrapecreators.com)
SCRAPECREATORS_PROXY_URL # Optional proxy for ScrapeCreators traffic only; default direct (ignores ALL_PROXY)
REDDIT_SORT           # Reddit listing sort: new|hot|top (default: new, window-bounded paging)
REDDIT_MAX_PAGES      # Max listing pages per subreddit, safety cap (default: 20)
REDDIT_BODY_TOP_N     # Top-scoring posts/sub to enrich with body+comments (default: 12)
REDDIT_MIN_COMMENTS_FOR_DIGEST # Min comments before a link post gets a comment digest (default: 8)
REDDIT_CREDIT_BUDGET  # Hard per-run ScrapeCreators call ceiling; aborts gracefully if hit (default: 600)
REDDIT_FETCH_WORKERS  # Concurrent subreddit fetch threads (default: 6)
REDDIT_PROXY_URL      # Legacy proxy for direct Reddit requests; now a no-op for Reddit (ScrapeCreators goes direct)
REDDIT_USER_AGENT     # User-Agent sent on ScrapeCreators requests (optional)
LESSWRONG_PROXY_URL   # HTTP(S) or SOCKS proxy for LessWrong GraphQL/browser fallback requests (optional)
PIPELINE_PROXY_URL    # HTTP(S) or SOCKS proxy for the whole pipeline (optional)
NEWS_USER_AGENT       # User-Agent sent to RSS/feed sources, incl. research blog feeds (optional)
LINK_FOLLOWER_MAX_URLS # Max distinct URLs the link follower fetches per run; gate errors fail closed (default: 50)
RESEARCH_FEED_TIMEOUT # Network timeout (seconds) for research blog feed fetches (default: 20)
LLM_TRUST_ENV_PROXY   # Let LLM clients use HTTP(S)/ALL_PROXY env vars (default: false)
LLM_TIMEOUT_SECONDS   # Override provider-config LLM request timeout (Actions default: 240)
LLM_MAX_CONCURRENT_REQUESTS # Async LLM request cap per provider route; 0 disables it (default: 8)
LLM_ADAPTIVE_MAX_TOKENS # Response output ceiling for adaptive calls; not a thinking budget (default: 65536)
LLM_MAX_RETRIES       # Anthropic SDK retry count for transient request failures (default: 2)
LLM_LOG_REQUESTS      # Log LLM queue/start/done metadata without raw prompt content (default: true)
LLM_HEARTBEAT_SECONDS # Seconds between in-flight LLM progress logs; 0 disables it (default: 60)
LLM_METRICS_PATH      # Optional JSONL path for per-request LLM metrics (Actions default: data/llm_metrics.jsonl)
ANALYZER_BATCH_SIZE   # Items per analyzer map batch (default: 75)
ANALYZER_MAX_CONCURRENT_BATCHES # Per-category analyzer map concurrency (default: 3)
MULLVAD_ACCOUNT       # Mullvad account number for CI proxy setup (optional)
MULLVAD_WG_PRIVATE_KEY # Stable WireGuard private key for the CI Mullvad device (optional)
MULLVAD_RELAY_FILTER  # Mullvad relay hostname prefix for CI tunnel selection (optional)
TARGET_DATE           # Report date (YYYY-MM-DD), coverage is day before. Defaults to today.
ENABLE_CRON           # Enable scheduled collection (default: false)
COLLECTION_SCHEDULE   # Cron schedule (default: 0 6 * * *), requires ENABLE_CRON=true
LOOKBACK_HOURS        # Data window in hours (default: 24)
TZ                    # Timezone (default: America/New_York)
```

## Adaptive Thinking Profiles

The pipeline uses internal AATF analysis profiles that map to Claude Opus 4.8 adaptive `output_config.effort`. QUICK/STANDARD/DEEP/ULTRATHINK are not provider thinking levels for Opus 4.8. `LLM_ADAPTIVE_MAX_TOKENS` controls the response output ceiling separately; `budget_tokens` is only used for older Claude models that still support manual thinking.

| Component | Profile | Opus 4.8 Effort |
|-----------|---------|-----------------|
| Link relevance check | QUICK | high |
| Item summarization | QUICK | high |
| Category theme detection | STANDARD | xhigh |
| Item ranking | DEEP | max |
| Cross-category topics | ULTRATHINK | max |
| Executive summary | DEEP | max |
| Link enrichment | STANDARD | xhigh |
| Ecosystem enrichment | STANDARD | xhigh |

## Multi-Provider LLM Routing

`config/providers.yaml` can define `llm.routes` for async LLM calls. Routes inherit root `llm` settings unless overridden, and new async calls rotate across routes. `LLM_MAX_CONCURRENT_REQUESTS` is applied per route, so three routes at the default cap of 8 allow up to 24 active LLM requests while analyzer/category concurrency remains controlled by `ANALYZER_MAX_CONCURRENT_BATCHES`.

Retryable transport failures, timeouts, 429s, and 5xx responses retry on a different route. Prompt/schema/client errors and JSON parse failures do not cross-provider retry. Hosted diagnostics include provider IDs, provider model IDs, route attempts, fallback source, retry reason, `thinking_type`, `analysis_profile`, `adaptive_effort`, `response_max_tokens`, queue/active counts, and content block counts; they must stay secret-safe and prompt-free.

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
- **Fallback**: If cross-category topic detection fails (Phase 3), hero generation falls back to top themes from each category (deduplicated, sorted by importance, top 6)

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

Summary feed entries keep the AATF report URL as the first `rel="alternate"` and `rel="canonical"` link. A representative external source, when present, remains as a secondary alternate with a distinct content type plus `rel="via"` for Feedly compatibility. Summary entries also emit `<content type="html">` with the same HTML as `<summary type="html">`.

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
- **LessWrong**: Uses GraphQL for date-range collection because RSS only exposes the newest posts. The helper tries direct GraphQL, cached cookies, and a Playwright browser warm-up. `LESSWRONG_PROXY_URL` can target only this source; otherwise `PIPELINE_PROXY_URL` is reused.
- **Reddit (ScrapeCreators)**: The free Reddit `.json` endpoint and OAuth are dead, so Reddit collects via the ScrapeCreators API (`x-api-key`). Listings page `sort=new` newest→oldest and stop once the coverage window is passed (credit-cheap, complete; `REDDIT_MAX_PAGES` safety cap). The top `REDDIT_BODY_TOP_N` posts/sub are enriched via one `post/comments` call: self posts get their `selftext`; high-discussion link posts get a digest of top community comments (analyzer `content`). A hard `REDDIT_CREDIT_BUDGET` aborts calls gracefully if exceeded. Egress is direct (`trust_env=False`), bypassing the pipeline proxy/Mullvad. `sort=new` backfill of dates >2 days old is depth-limited and logs a warning.
- **External API Usage**: Non-LLM paid APIs report per-run usage and live balance into the end-of-run cost summary (and `cost_report_{date}.json` under `external_apis`): ScrapeCreators shows calls/credits-consumed/remaining-balance, and TwitterAPI.io shows calls/tweets/`recharge_credits` balance ($1 = 100,000 credits). Balance probes are free.
- **Link Following**: The News gatherer receives social posts and uses LLM to decide which linked articles to fetch.
- **Link Enrichment**: Executive summaries, category summaries, and topic descriptions are enriched with internal links to referenced items. Links use format `/?date={date}&category={category}#item-{id}`.
- **Date Semantics**: TARGET_DATE represents the report date. Coverage period is the day BEFORE the report date (00:00-23:59 ET). For example, TARGET_DATE=2026-01-05 generates a "January 5th report" covering news from January 4th.
- **Collection Status**: Each gatherer tracks success/partial/failed status. Social gatherer tracks per-platform status (Twitter, Bluesky, Mastodon). Status is logged at end of run and included in JSON output for frontend display.
- **Output Quality**: LLM prompts are tuned for factual, briefing-style output. Avoid generic "thought leader" language.
- **Source Diversity**: The ranking algorithm prioritizes news articles (RSS, arXiv) over social discussions (Reddit) to ensure top stories reflect actual developments.
- **Item IDs**: Generated as 12-character SHA256 hashes (~280 trillion unique values) for compact URLs.
- **Ecosystem Grounding**: All analyzers receive model release dates as system context to prevent hallucinations about "new" releases that are actually weeks/months old.
- **Phase Tracking**: Each phase is tracked with status (success/partial/failed/skipped), timing, and details. End-of-run summary prints before cost report. Phase status is included in `OrchestratorResult` JSON output.
- **Checkpointing**: Major phases save checkpoints to `data/checkpoints/{date}/`. Use `--resume` for auto crash recovery or `--resume-from N` to re-run from a specific phase. Checkpoints persist between runs.
- **Hero Fallback**: When topic detection (Phase 3) fails or returns no topics, hero image generation falls back to top category themes instead of being skipped entirely.

## Adding New Sources

- RSS feeds: Add URLs to `config/rss_feeds.txt` (one per line)
- Research blogs: Add URLs to `config/research_feeds.txt` (LessWrong, AI Alignment Forum, etc.)
- Bluesky: Add handles to `config/bluesky_accounts.txt` (e.g., `karpathy.bsky.social`)
- Mastodon: Add accounts to `config/mastodon_accounts.txt` (format: `username@instance.social`)
- Twitter: Add usernames to `config/twitter_accounts.txt` (requires TWITTERAPI_IO_KEY)
- Reddit: Add subreddits to `config/reddit_subreddits.txt` (requires `SCRAPECREATORS_API_KEY`)

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
- **Full-text Search**: Client-side MiniSearch index built in a Web Worker from a compact corpus
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
│   ├── searchIndex.ts          # MiniSearch worker proxy
│   ├── searchWorker.ts         # Web Worker: builds + queries MiniSearch index
│   └── dateUtils.ts            # Date formatting helpers
└── types/
    └── index.ts                # TypeScript interfaces
```

### JSON Data Structure

Data is output to `web/data/`. The dev server serves from there via Vite alias.

```
web/data/
├── index.json              # Date manifest (list of available dates)
├── search-corpus.json      # Search corpus (30-day window); index built in-browser
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
