# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI News Aggregator - A Python-based pipeline that collects AI/ML news from multiple sources (RSS feeds, arXiv API, Twitter, Reddit, Bluesky, Mastodon), analyzes them with Claude Opus 4.5 via LiteLLM, and generates a static HTML website.

## Commands

### Docker (Production)
```bash
docker-compose build                    # Build container
docker-compose up -d                    # Start services
docker-compose down                     # Stop services
docker logs ai-news-aggregator          # View container logs
docker exec ai-news-aggregator python3 /app/run_pipeline.py  # Manual pipeline run
```

### Local Development
```bash
source venv/bin/activate                            # Activate virtual environment
pip install -r requirements.txt                     # Install dependencies
python3 run_pipeline.py --create-config             # Generate default config
python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web

# Run for a specific date (useful for testing/backfilling)
TARGET_DATE="2026-01-02" python3 run_pipeline.py --config-dir ./config --data-dir ./data --web-dir ./web
```

There are no unit tests, linting, or type checking configured.

## Architecture

### 4-Phase Pipeline (run_pipeline.py)

```
Phase 1: Collection → Phase 2: Processing → Phase 3: LLM Analysis → Phase 4: HTML Generation
```

1. **Collection** (`collectors/`): Parallel fetching from RSS, arXiv, Twitter/Reddit
2. **Processing** (`processors/data_processor.py`): Normalize, deduplicate, enrich
3. **Analysis** (`processors/llm_analyzer.py`): Claude Opus 4.5 summarization and ranking
4. **Generation** (`generators/html_generator.py`): Jinja2 templates → static HTML

### Key Files
- `run_pipeline.py` - Main orchestration script (entry point)
- `entrypoint.sh` - Docker container startup (cron + nginx)
- `config/` - Runtime-created feed lists (rss_feeds.txt, twitter_accounts.txt, reddit_subreddits.txt)
- `data/raw/` - Collected JSON, `data/processed/` - Analyzed JSON
- `web/` - Generated static website

### External Dependencies
- **LiteLLM** - Gateway to Claude Opus 4.5 (configured via LITELLM_* env vars)
- **TwitterAPI.io** - Twitter/X data collection ($0.15/1000 tweets)
- **Reddit JSON** - Free Reddit endpoint (add .json to Reddit URLs)
- **Bluesky Public API** - Free, no auth required
- **Mastodon Public API** - Free, no auth required

## Environment Variables

```
LITELLM_API_BASE      # LiteLLM endpoint URL
LITELLM_API_KEY       # API key
LITELLM_MODEL         # Model name (default: claude-opus-4.5)
TWITTERAPI_IO_KEY     # TwitterAPI.io API key
TARGET_DATE           # Specific date to collect (YYYY-MM-DD), defaults to yesterday
COLLECTION_SCHEDULE   # Cron schedule (default: 0 6 * * *)
LOOKBACK_HOURS        # Data window in hours (default: 24)
TZ                    # Timezone (default: America/New_York)
```

## Important Notes

- **arXiv**: Uses the arXiv API with `submittedDate` queries. arXiv only publishes papers on weekdays (Mon-Fri). Weekend dates will return 0 papers.
- **Output Quality**: LLM prompts in `processors/llm_analyzer.py` are tuned for factual, briefing-style output. Avoid generic "thought leader" language.
- **Source Diversity**: The ranking algorithm prioritizes news articles (RSS, arXiv) over social discussions (Reddit) to ensure top stories reflect actual developments.

## Adding New Sources

- RSS feeds: Add URLs to `config/rss_feeds.txt` (one per line)
- Bluesky: Add handles to `config/bluesky_accounts.txt` (e.g., `karpathy.bsky.social`)
- Mastodon: Add accounts to `config/mastodon_accounts.txt` (format: `username@instance.social`)
- Twitter: Add usernames to `config/twitter_accounts.txt` (requires TWITTERAPI_IO_KEY)
- Reddit: Add subreddits to `config/reddit_subreddits.txt` (free, no API key needed)

## Adding a New Collector

Create a new file in `collectors/` following the pattern:
- Implement `collect()` method returning list of normalized items
- Implement `save_to_file()` for JSON persistence
- Add to the collection phase in `run_pipeline.py`
