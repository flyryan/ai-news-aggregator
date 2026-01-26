# Codebase Concerns

**Analysis Date:** 2026-01-24

## Tech Debt

**No Test Coverage:**
- Issue: Only 2 manual test scripts exist (`test_news_filter.py`, `test_news_analyzer.py`). No automated test suite, no CI test runs.
- Files: `test_news_filter.py`, `test_news_analyzer.py` (root)
- Impact: Changes to core pipeline logic risk breaking production with no safety net. LLM prompt changes have no regression detection. Recent XSS vulnerability (fixed in Jan) could have been caught with tests.
- Fix approach: Add pytest suite for gatherers (mocked API responses), analyzers (LLM response parsing), and JSON generation. Add integration tests for end-to-end pipeline with fixture data.

**Bare Exception Handlers:**
- Issue: 30+ instances of `except:` or `except Exception as e:` that log but don't fail gracefully. Some swallow errors silently.
- Files: `agents/base.py:244` (bare except with pass), `generators/search_indexer.py:92`, `generators/feed_generator.py:295` (silent failures), `agents/orchestrator.py:187`
- Impact: Silent failures during data collection/generation can produce incomplete reports. Users see stale data with no indication of backend failure.
- Fix approach: Replace bare excepts with specific exception types. Add status tracking for each pipeline phase. Fail loudly if critical steps fail (e.g., LLM API down).

**Rate Limiting:**
- Issue: Hard-coded sleep values scattered across gatherers without exponential backoff or proper rate limit detection.
- Files: `agents/gatherers/social_gatherer.py:227` (0.3s), `agents/gatherers/reddit_gatherer.py:190` (1s), `agents/gatherers/research_gatherer.py:679` (3.5s arXiv)
- Impact: Fixed delays waste time when APIs allow faster requests. No retry strategy when rate limited (429 responses).
- Fix approach: Centralize rate limiting with configurable per-API limits. Use aiohttp RateLimiter or similar. Add exponential backoff retry logic.

**LLM Token Budget Hardcoded:**
- Issue: Thinking budget levels (4K, 8K, 16K, 32K) are hardcoded as IntEnum constants. RDSec proxy has 64K limit hardcoded.
- Files: `agents/llm_client.py:27-32` (ThinkingLevel enum), `agents/llm_client.py:36` (MODEL_MAX_TOKENS = 64000)
- Impact: If RDSec increases limits or model capabilities change, must edit code. Cost optimization requires code changes.
- Fix approach: Move thinking budgets to config file or environment variables. Make max_tokens configurable per deployment.

**Monolithic Base Classes:**
- Issue: `BaseGatherer` (826 lines) and `BaseAnalyzer` (in `agents/base.py`) contain too much logic. Hard to understand and modify.
- Files: `agents/base.py` (826 lines with 7 dataclasses, 2 base classes, utility functions)
- Impact: Changes to one gatherer's deduplication logic affect all gatherers. Testing analyzer behavior requires understanding 800+ lines.
- Fix approach: Split into separate files: `models.py` (dataclasses), `gatherer.py`, `analyzer.py`, `utils.py`. Extract common logic into mixins.

**Ecosystem Context Drift:**
- Issue: `model_releases.yaml` is manually curated with GA dates from Wikipedia. Auto-enrichment (Phase 4.6) adds detected releases but may add false positives.
- Files: `config/model_releases.yaml` (curated), `config/ecosystem_context.yaml` (generated cache), `agents/ecosystem_context.py:581` (enrichment logic)
- Impact: Incorrect model dates in context grounding can cause LLMs to under/over-emphasize "new" releases. Cache regenerates daily so bad data persists until manual correction.
- Fix approach: Add confidence scores to auto-detected releases. Require manual review before adding to curated file. Add validation script to check date consistency.

**Frontend Data Cache with No Invalidation:**
- Issue: `dataLoader.ts` uses in-memory Map cache with no TTL or invalidation strategy. Cache persists for entire browser session.
- Files: `frontend/src/lib/services/dataLoader.ts:7` (cache Map), no clearCache() calls
- Impact: If JSON data is regenerated server-side (e.g., link enrichment fix), users see stale data until full page reload. Recent cache header fixes (commit 4559333) help but don't solve in-page staleness.
- Fix approach: Add cache TTL (5 minutes). Clear cache on route changes or add ETag-based validation.

**Docker Multi-Stage Build Complexity:**
- Issue: Dockerfile has 2-stage build (Node for frontend, Python for backend). Build context includes entire repo including venv/node_modules.
- Files: `Dockerfile:1-62`, `.dockerignore` (missing)
- Impact: Slow builds due to large context. No .dockerignore means unnecessary files copied. Build failures hard to debug.
- Fix approach: Add `.dockerignore` to exclude venv/, node_modules/, data/, .git/. Consider separate Dockerfiles for dev vs prod.

## Known Bugs

**arXiv Weekend Schedule:**
- Symptoms: Saturday/Sunday pipeline runs collect 0 arXiv papers. Logs show "skip" mode but no warning to user.
- Files: `agents/gatherers/research_gatherer.py:79-100` (_get_arxiv_collection_mode)
- Trigger: Run pipeline on Saturday or Sunday with TARGET_DATE set to that date.
- Workaround: None. arXiv only publishes papers Mon-Fri. Reports for Sat/Sun will always have 0 research papers.
- Note: Not actually a bug - arXiv doesn't publish on weekends. But frontend should indicate "No papers published (weekend)" instead of appearing empty.

**Twitter Collection Fails Silently:**
- Symptoms: If TWITTERAPI_IO_KEY is invalid or account has no recent posts, Twitter gatherer returns 0 items with status "success" (should be "partial" or "failed").
- Files: `agents/gatherers/social_gatherer.py:67-75` (marks as "skipped" if no key, but not if key is invalid)
- Trigger: Set TWITTERAPI_IO_KEY to expired/invalid key. Pipeline completes without error but social feed is incomplete.
- Workaround: Check collection_status in summary.json. Twitter status will show count=0.

## Security Considerations

**XSS Vulnerability (Recently Fixed):**
- Risk: Stored XSS via malicious content in RSS feeds, arXiv abstracts, or social posts. HTML was rendered unsanitized in frontend.
- Files: Multiple fixes in Jan 2026 (commits d83e927, e904fe0, ba1b17b). Now uses `nh3` for backend sanitization and DOMPurify for frontend.
- Current mitigation: Backend sanitizes with nh3 (Python), frontend uses DOMPurify for all user-generated content.
- Recommendations: Add Content Security Policy headers to Nginx config. Consider subresource integrity for CDN dependencies.

**API Key Exposure Risk:**
- Risk: `.env` file contains ANTHROPIC_API_KEY and TWITTERAPI_IO_KEY in plaintext. File is in .gitignore but present on disk (chmod 600).
- Files: `.env` (root), `.env.example` (template)
- Current mitigation: .env in .gitignore, file permissions restricted to owner-only (600).
- Recommendations: Use secrets management (Docker secrets, HashiCorp Vault) for production. Add pre-commit hook to prevent accidental .env commits. Consider AWS SSM Parameter Store for cloud deployments.

**Unrestricted LLM Input:**
- Risk: No input validation on content sent to LLM. Malicious RSS feeds could inject prompt injection attacks to manipulate analysis.
- Files: `agents/analyzers/*.py` (all analyzers send raw content to LLM)
- Current mitigation: None. Relies on Claude's built-in prompt injection defenses.
- Recommendations: Add input length limits per item (e.g., 10K chars max). Strip control characters. Add LLM output validation to detect attempted manipulation (e.g., unexpected JSON keys).

**SSRF via Link Following:**
- Risk: LinkFollower fetches arbitrary URLs from Twitter/social posts. Could be exploited to scan internal networks.
- Files: `agents/gatherers/link_follower.py:418` (follows links from untrusted social posts)
- Current mitigation: User-Agent set, follows redirects. No URL whitelist.
- Recommendations: Add URL blacklist (localhost, 10.0.0.0/8, 169.254.0.0/16, etc.). Enforce HTTPS-only for followed links. Add max redirect limit (currently unlimited).

## Performance Bottlenecks

**Sequential LLM Calls in Batch Analysis:**
- Problem: Analyzers make sequential LLM calls for batches of items despite async architecture. Each batch waits for previous batch to complete.
- Files: `agents/base.py:384` (BaseAnalyzer._analyze_batch), `agents/analyzers/*.py`
- Cause: _analyze_batch uses await in loop instead of asyncio.gather. LLM calls are I/O bound so parallelism would help.
- Improvement path: Use asyncio.gather() to parallelize LLM calls within each batch. Could reduce analysis time by 50-70%.

**Full Index Rebuild on Every Run:**
- Problem: Search index (`search-index.json`) is regenerated from scratch on every pipeline run, even if only 1 day's data changed.
- Files: `generators/search_indexer.py:146` (loops through all dates in rolling window)
- Cause: No incremental update logic. Lunr.js index is serialized/deserialized entirely.
- Improvement path: Add incremental index update. Store per-date index segments, merge at query time. For 30-day window with daily runs, saves 96% of indexing work.

**Hero Image Generation Blocking:**
- Problem: Hero image generation (Gemini API call + image optimization) blocks pipeline completion. Takes 15-30 seconds per image.
- Files: `agents/orchestrator.py:187` (hero generation in main orchestrator flow), `generators/hero_generator.py:140-322`
- Cause: Synchronous call to RDSec Gemini API. No timeout on image generation. Image optimization (PIL) runs in main thread.
- Improvement path: Move hero generation to background task. Pipeline completes faster, hero image appears when ready. Add 30s timeout for Gemini calls.

**RSS Feed Generation for All Date Windows:**
- Problem: Feed generator creates 20+ RSS feeds (main, summaries, categories with count variants) on every run. Most feeds don't change.
- Files: `generators/feed_generator.py:353-525` (loops through all combinations of feed types and date windows)
- Cause: No change detection. Regenerates all feeds even if no new data for that feed type.
- Improvement path: Hash feed content, skip regeneration if unchanged. Or generate feeds on-demand via API endpoint.

## Fragile Areas

**JSON Output Assembly:**
- Files: `generators/json_generator.py:63-90` (DateEncoder with ValueError fallbacks)
- Why fragile: Custom DateEncoder handles datetime/date serialization. Falls back to str(o) for unknown types which hides serialization bugs. Multiple nested try-except blocks mask errors.
- Safe modification: Always test JSON generation end-to-end after changes. Add schema validation (pydantic models) for generated JSON. Check all JSON files are valid after pipeline run.
- Test coverage: None. Manual verification only.

**Link Enrichment Regex:**
- Files: `agents/link_enricher.py:289` (JSON parsing from LLM response)
- Why fragile: Relies on LLM returning valid JSON with specific structure. Regex-based link insertion could break if summary text contains special regex characters.
- Safe modification: Never change enrichment prompt without testing on historical data. LLM response format changes break silently (logs error but returns un-enriched text).
- Test coverage: None. No fixtures for LLM responses.

**Date Range Calculation:**
- Files: `agents/base.py:210-231` (_calculate_date_range), `run_pipeline.py:41-58` (parse_date)
- Why fragile: Complex timezone handling (ET timezone hardcoded). TARGET_DATE semantics (report date vs coverage date) are confusing and error-prone.
- Safe modification: Test with multiple TARGET_DATE values including edge cases (month boundaries, DST transitions, weekends). Verify coverage_start/coverage_end in output.
- Test coverage: None.

**Frontend Route Validation:**
- Files: `frontend/src/lib/stores/dateStore.ts:62-116` (navigation functions), `frontend/src/routes/+page.svelte` (route handling)
- Why fragile: Date param validation happens in multiple places. Invalid dates can cause infinite redirects. Category param validation is inconsistent.
- Safe modification: Test all route combinations: `/?date=invalid`, `/?date=2026-01-01` (no data), `/?category=invalid`. Check browser console for errors.
- Test coverage: None. No E2E tests.

**Ecosystem Context Merge Logic:**
- Files: `agents/ecosystem_context.py:305-331` (OpenRouter API parsing), `agents/ecosystem_context.py:372` (YAML parsing with bare except)
- Why fragile: Merges curated YAML with live API data. YAML parse errors are silently caught. Date format inconsistencies (GA vs API dates) can cause confusion.
- Safe modification: Always validate model_releases.yaml after manual edits. Check ecosystem_context.yaml after pipeline runs to ensure merging worked correctly.
- Test coverage: None.

## Scaling Limits

**Single-Threaded Pipeline Orchestrator:**
- Current capacity: ~100-200 items per category. Pipeline takes 8-12 minutes end-to-end (mostly LLM calls).
- Limit: If data sources double (e.g., add 100 more Twitter accounts), analysis time could exceed 30 minutes. Docker healthcheck would fail.
- Scaling path: Move to distributed queue (Celery + Redis). Run gatherers and analyzers as separate workers. Use async LLM calls with parallelism >1.

**In-Memory Data Storage:**
- Current capacity: All collected/analyzed items held in memory during pipeline run. With 500-1000 items across 4 categories (~5MB JSON), still comfortable.
- Limit: If adding video transcripts or full paper PDFs, memory usage could spike to 100s of MB. Docker container has no memory limits set.
- Scaling path: Stream data to disk during collection. Use SQLite or DuckDB for intermediate storage. Set Docker memory limits (e.g., 2GB).

**Client-Side Search Index:**
- Current capacity: 30-day rolling window produces ~150KB Lunr.js index + 500KB document store. Loads in <1s on broadband.
- Limit: If extending to 90-day or 365-day window, index size could exceed 5MB. Mobile users would see slow initial load.
- Scaling path: Move search to backend API (Elasticsearch or Typesense). Keep client-side for offline access but paginate results. Add service worker for offline caching.

**Static File Serving:**
- Current capacity: Nginx serves pre-generated JSON files. With ~30 dates × 5 files/date = 150 JSON files. Total ~20MB.
- Limit: If keeping 365 days of data, grows to ~250MB of JSON. Nginx handles this fine but no CDN caching configured.
- Scaling path: Add CloudFront or similar CDN. Use ETags for cache validation. Consider compressing JSON files (gzip pre-compression).

## Dependencies at Risk

**feedparser (6.0.11):**
- Risk: Last major update in 2022. No active development. Some RSS feeds with non-standard markup fail to parse correctly.
- Impact: New RSS feed formats (e.g., YouTube RSS, podcast feeds) may not work. Security fixes slow to arrive.
- Migration plan: Evaluate alternatives (atoma, mf2py) or vendor feedparser into repo with local patches. Add fallback HTML parsing for critical feeds.

**requests (2.31.0):**
- Risk: Sync-only library. Blocks async event loop in social gatherer when run in ThreadPoolExecutor.
- Impact: Performance bottleneck for high-volume API calls. Harder to implement request cancellation/timeouts.
- Migration plan: Replace with httpx (already used for Anthropic client). httpx has sync and async APIs with same interface. Low-risk migration.

**Pillow (>=10.0.0):**
- Risk: Large dependency (~10MB) used only for hero image optimization. Has history of security vulnerabilities in image parsing.
- Impact: Increases Docker image size. Attack surface for malicious image files (though images come from trusted Gemini API).
- Migration plan: Consider using imagemagick via subprocess or pillow-heif for smaller footprint. Or use CDN image optimization (Cloudflare, Imgix) instead of local processing.

**Python 3.11 (Docker base image):**
- Risk: Production uses Python 3.11, but dev environment uses Python 3.14 (per venv path). Version mismatch could cause subtle bugs.
- Impact: Features available in 3.14 won't work in production. Deprecation warnings differ between versions.
- Migration plan: Standardize on Python 3.11 for both dev and prod. Or upgrade Dockerfile to 3.12+ (test thoroughly first). Add .python-version file for pyenv.

## Missing Critical Features

**No Rollback Mechanism:**
- Problem: If pipeline generates bad data (e.g., LLM hallucination in executive summary), no way to roll back to previous version without manual JSON editing.
- Blocks: Ability to quickly recover from bad pipeline runs. Historical data corrections require manual JSON editing.
- Priority: Medium. Has happened 3-4 times based on git commits (continuation text fixes, link enrichment bugs).

**No Analytics/Monitoring:**
- Problem: No visibility into production usage. Don't know how many users access daily, which categories are popular, or if search is working.
- Blocks: Product decisions about what to improve. Can't detect if service is down unless manually checking.
- Priority: High. Running blind. Add Plausible Analytics or similar privacy-focused tool. Add /health endpoint that checks latest data age.

**No User Feedback Mechanism:**
- Problem: No way for users to report bad summaries, incorrect categorization, or missing items. All feedback comes via email/Slack.
- Blocks: Systematic quality improvement. Don't know which LLM prompts need tuning.
- Priority: Low. Internal tool for AATF team. But feedback form would help capture issues.

**No Historical Data Backfill:**
- Problem: Can't easily regenerate data for past dates with improved prompts or logic. TARGET_DATE works but requires manual execution.
- Blocks: Improving historical data quality. A/B testing prompt changes. Building training datasets for ML models.
- Priority: Medium. Add `scripts/backfill.py` that runs pipeline for date ranges. Store multiple versions (prompt v1, v2, etc.).

## Test Coverage Gaps

**Gatherers:**
- What's not tested: RSS feed parsing, arXiv OAI-PMH fallback, Twitter API error handling, Reddit JSON parsing.
- Files: `agents/gatherers/*.py` (all gatherers)
- Risk: API changes break collection silently. Recent Twitter API changes required manual fixes.
- Priority: High. Add pytest suite with mocked API responses (responses library or pytest-httpx).

**Analyzers:**
- What's not tested: LLM response parsing, JSON extraction from markdown code blocks, importance scoring logic, theme detection.
- Files: `agents/analyzers/*.py` (all analyzers)
- Risk: LLM output format changes break analysis. Importance scores drift over time without detection.
- Priority: High. Add fixtures with sample LLM responses. Test all JSON parsing code paths.

**JSON Generation:**
- What's not tested: DateEncoder serialization, summary.json structure, category JSON format, index.json generation.
- Files: `generators/json_generator.py`, `generators/search_indexer.py`, `generators/feed_generator.py`
- Risk: Schema changes break frontend. Invalid JSON deployed to production.
- Priority: Medium. Add JSON schema validation tests. Use jsonschema library to validate all generated files.

**Frontend Components:**
- What's not tested: Date navigation, search functionality, category filtering, dark mode toggle, calendar interactions.
- Files: `frontend/src/lib/components/**/*.svelte`
- Risk: UI regressions. Broken links. Search not returning results.
- Priority: Medium. Add Playwright E2E tests for critical paths (home → category → search → date nav).

**Link Enrichment:**
- What's not tested: Internal link detection, URL generation, markdown link insertion, LLM response parsing.
- Files: `agents/link_enricher.py`
- Risk: Broken internal links. Links to wrong items. Has broken multiple times (check git history).
- Priority: Medium. Add unit tests with sample text containing item references. Verify generated URLs.

**Hero Image Generation:**
- What's not tested: Prompt construction, topic visual mapping, image optimization, WebP conversion.
- Files: `generators/hero_generator.py`
- Risk: Bad prompts produce off-brand images. Image optimization fails silently. WebP not supported in old browsers.
- Priority: Low. Manual QA sufficient. But add validation test to check hero.webp exists and is valid WebP format.

---

*Concerns audit: 2026-01-24*
