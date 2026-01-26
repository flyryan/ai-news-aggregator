# Technology Stack

**Analysis Date:** 2026-01-24

## Languages

**Primary:**
- Python 3.11 - Backend pipeline, data collection, LLM orchestration
- TypeScript 5.0+ - Frontend type safety and validation

**Secondary:**
- JavaScript (Node 20) - Frontend build tooling, Svelte components

## Runtime

**Environment:**
- Python 3.11-slim (production container)
- Node.js 20-alpine (frontend build stage)

**Package Manager:**
- Python: pip
- Node: npm
- Lockfile: `package-lock.json` present (frontend), no Python lockfile

## Frameworks

**Core:**
- Svelte 5.0 - Component framework for SPA frontend
- SvelteKit 2.0 - SSG framework with static adapter
- Anthropic SDK >=0.40.0 - Claude API integration with extended thinking

**Frontend Build:**
- Vite 6.4.1 - Build tool and dev server
- Tailwind CSS 3.4.17 - Utility-first CSS framework
- PostCSS 8.4.49 - CSS processing pipeline

**Testing:**
- None configured (no unit tests, linting, or type checking beyond `svelte-check`)

**Infrastructure:**
- nginx - Static file serving and reverse proxy
- Docker - Multi-stage containerization
- cron - Scheduled pipeline execution (optional)

## Key Dependencies

**Critical:**
- `anthropic>=0.40.0` - Claude API with extended thinking support (Bearer auth)
- `httpx>=0.27.0` - Async HTTP client for custom authentication
- `feedparser==6.0.11` - RSS/Atom feed parsing (news, arXiv, research blogs)
- `requests==2.31.0` - HTTP requests for REST APIs (Twitter, Bluesky, Reddit)
- `aiohttp>=3.9.0` - Async HTTP for OpenRouter API calls
- `beautifulsoup4==4.12.3` - HTML parsing for article extraction

**Frontend:**
- `@sveltejs/adapter-static@^3.0.0` - SvelteKit static site generation
- `lunr@^2.3.9` - Client-side full-text search
- `dompurify@^3.0.0` - XSS sanitization for markdown rendering
- `date-fns@^4.1.0` - Date formatting and manipulation

**Data Processing:**
- `python-dateutil==2.8.2` - Date parsing from various formats
- `PyYAML>=6.0` - YAML config parsing (model releases, ecosystem context)
- `lunr>=0.7.0` - Python-side search index generation
- `Pillow>=10.0.0` - Image optimization for hero images
- `nh3>=0.2.0` - HTML sanitization (Mozilla's ammonia in Python)

**Utilities:**
- `jinja2==3.1.3` - Template rendering
- `python-dotenv>=1.0.0` - Environment variable loading
- `schedule==1.2.1` - Cron-like scheduling (not used in production)

## Configuration

**Environment:**
- Variables loaded via `python-dotenv` in dev, direct env vars in production
- Required variables: `ANTHROPIC_API_BASE`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- Optional: `TWITTERAPI_IO_KEY` (Twitter collection), `TARGET_DATE`, `ENABLE_CRON`, `COLLECTION_SCHEDULE`, `LOOKBACK_HOURS`, `TZ`
- No `.env` file committed (development only)

**Build:**
- `Dockerfile` - Multi-stage build (Node frontend builder + Python runtime)
- `docker-compose.yml` - Service definition with volume mounts and port 7100 mapping
- `nginx.conf` - Web server configuration with security headers and caching rules
- `entrypoint.sh` - Container initialization and optional cron setup
- `svelte.config.js` - SvelteKit static adapter targeting `../web` output
- `vite.config.ts` - Dev server with custom middleware for `/data` serving
- `tailwind.config.js` - AATF brand colors (Trend Red #E63946) and custom theme
- `tsconfig.json` - TypeScript strict mode with bundler module resolution
- `postcss.config.js` - Tailwind and autoprefixer plugins

## Platform Requirements

**Development:**
- Python 3.11+
- Node.js 20+
- Environment variables in `.env` file
- Required: `ANTHROPIC_API_BASE`, `ANTHROPIC_API_KEY`
- Optional: `TWITTERAPI_IO_KEY` for Twitter collection

**Production:**
- Docker 3.8+ with multi-stage build support
- Port 7100 exposed (HTTP)
- Volume mounts: `./config`, `./data`, `./web/data`, `./web/assets`, `./logs`
- Healthcheck: HTTP GET on `http://localhost/` every 30s
- Cron available for scheduled execution (controlled by `ENABLE_CRON`)

---

*Stack analysis: 2026-01-24*
