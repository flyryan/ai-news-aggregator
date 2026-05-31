# Task: Replace dead Reddit `.json` source with ScrapeCreators API

## Background / why this is needed
The Reddit gatherer in this repo (`agents/gatherers/reddit_gatherer.py`) collects hot posts
from a list of AI/ML subreddits using Reddit's **free unauthenticated** endpoint
`https://www.reddit.com/r/<sub>/hot.json`.

As of **2026-05-29** that endpoint returns **HTTP 403 "Blocked"** from every exit IP we tried
(GitHub Actions runner, Mullvad VPN, residential). It is an **endpoint-level kill, not IP
reputation** — `.json` is dead for everyone. Reddit OAuth (`client_credentials`) has also been
killed and is **not** an option (do not reintroduce it). `.rss` feeds still work but lack
engagement metadata (score/comments) and cap at ~25 items, so they are not acceptable parity.

**Decision:** switch the gatherer to the **ScrapeCreators** third-party API (same pattern the
Twitter gatherer already uses with TwitterAPI.io). It restores full `.json`-equivalent data.

Result of last good vs broken runs (for context): `reddit_<date>.json` was ~1.2–1.8 MB through
2026-05-28, then 205 bytes / 0 items on 2026-05-29 and 2026-05-30.

---

## ScrapeCreators API — everything you need

- **Base URL:** `https://api.scrapecreators.com`
- **Auth:** header `x-api-key: <API_KEY>` on every request (same shape as TwitterAPI.io's
  `X-API-Key`). Key will be provided via env var (see below) — **do not hardcode it**.
- **Docs:** https://docs.scrapecreators.com  (LLM-friendly: https://docs.scrapecreators.com/llms-full.txt)
- **No rate limits** advertised (keep concurrency < 500).
- **Pricing:** pay-as-you-go credits, ~$0.99 / 1,000 calls. **1 API call = 1 credit.**
- **Response codes:** 200 ok, 400 bad params, 401 bad key, 402 out of credits, 500 server error.
  ⚠️ Known quirk: their auth/credit error mapping is inconsistent — a bad/empty key sometimes
  returns `402 {"success":false,"message":"...out of credits..."}` instead of 401, and 402s can
  appear transiently. Treat any `success:false` / non-200 as a soft failure: retry with backoff,
  and if it persists, log + skip that item (don't crash the whole run). Check real balance via
  `GET /v1/account/credit-balance`.

### Two endpoints are required (this is the key design point)

**1. Subreddit listing** — for discovery + ranking. **1 call per page (~25 posts).**
```
GET /v1/reddit/subreddit?subreddit=<sub>&sort=hot&after=<cursor>
```
- Returns: `{ "success": true, "credits_remaining": N, "posts": [ {...}, ... ], "after": "t3_xxx" }`
- Each entry in `posts[]` is **already the post object** (NOT wrapped in `{"data": {...}}` like
  raw Reddit). Fields present and VERIFIED:
  `id, title, permalink, author, created_utc, subreddit, url, domain, score, ups, downs,
   num_comments, upvote_ratio, over_18, is_video, post_hint, spoiler, total_awards_received`
- Pagination: pass the top-level `after` cursor back as `&after=`. VERIFIED working — page 2
  returns fresh post IDs, zero overlap with page 1.
- ⚠️ **The listing does NOT include `selftext` (post body), `is_self`, `link_flair_text`, or
  `stickied`.** `trim=true/false` does not change this. The body must come from endpoint #2.

**2. Post detail** — for the body of text posts. **1 call per post.**
```
GET /v1/reddit/post/comments?url=<full_permalink_url>
```
- `url` = `https://www.reddit.com` + the post's `permalink` (single post per call).
- Returns: `{ "success": true, "credits_remaining": N, "post": {...}, "comments": [...], "more": [...] }`
- The `post` object **DOES include `selftext` and `selftext_html`** (VERIFIED — pulled real body
  text), plus `title, author, created_utc, score, ups, downs, num_comments, upvote_ratio,
  subreddit, permalink, url, is_video, over_18, spoiler, total_awards_received`.

### Cost / call-count reality (important)
- There is **no bulk-body endpoint.** Body fetches are **per-post**, not per-subreddit.
- So full cost = (listing pages across all subs) + (1 body call per text post you choose to enrich).
- **Link posts do NOT need a body call** — their substance is the linked article (already captured
  via `url`/`external_url`); `selftext` is empty for them anyway.
- **Recommended optimization:** only body-fetch posts where `is_self` is true AND that pass the
  existing date-range + dedup filters, and optionally cap to the top N by `score` per subreddit
  (e.g. top 10–15). This keeps credit usage low while preserving analysis quality. The current
  config monitors ~15 subreddits (`config/reddit_subreddits.txt`).
- Note: the listing omits `is_self`. Infer text-vs-link cheaply without an extra call using
  `domain` (a self post's domain looks like `self.<subreddit>`) or `post_hint`/`url` containing
  the permalink. Use that heuristic to decide whether a body fetch is worth a credit. (If you want
  it authoritative, the post-detail `post.selftext`/`is_self` is the source of truth, but that
  costs the call you're trying to decide on — so prefer the `domain == "self." + subreddit`
  heuristic for gating.)

---

## What to change in `agents/gatherers/reddit_gatherer.py`

Keep the class structure, async wrapper, pagination loop, dedup (`seen_ids`), date-range
filtering, and `CollectedItem` output **the same**. Only the request layer + JSON envelope
change, plus an added optional body-enrichment step.

1. **Config constants (top of file):**
   - Add `SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY", "")`
   - Add `SCRAPECREATORS_BASE = os.getenv("SCRAPECREATORS_BASE", "https://api.scrapecreators.com")`
   - Keep `REDDIT_USER_AGENT`. The Mullvad/`REDDIT_PROXY_URL` proxy is now **optional** — ScrapeCreators
     does the un-blocking server-side, so the proxy is no longer required for Reddit (leaving it set
     is harmless but unnecessary). Don't remove the proxy plumbing; just don't depend on it.

2. **`_request_listing(subreddit, params)`** — repoint to ScrapeCreators:
   - URL: `f"{SCRAPECREATORS_BASE}/v1/reddit/subreddit"`
   - Query: `{"subreddit": subreddit, "sort": "hot"}` plus `"after": params["after"]` when set.
     (Drop the `limit=100` param — listing returns ~25/page; rely on the `after` cursor to page.)
   - Header: `{"x-api-key": SCRAPECREATORS_API_KEY}`
   - Keep the retry/backoff loop. Add 402/`success:false` to the soft-retry set.

3. **Envelope extraction in `_fetch_subreddit`:**
   - `data = response.json()`
   - `post_list = data.get("posts", [])`   (was `data["data"]["children"]`)
   - Each `post_data` is the object directly — **remove the `post_wrapper.get("data", {})` unwrap.**
   - Next-page cursor: `after = data.get("after")`   (was `data["data"]["after"]`)
   - `stickied` is absent from the listing — the stickied-skip will simply no-op; that's fine
     (or skip via `data.get("stickied")` defensively).

4. **Field mapping → `CollectedItem`** (all sourced from the listing object, names unchanged):
   `id`→`id`, `title`→`title`, `url`→`https://reddit.com`+`permalink`, `author`→`u/`+`author`,
   `published`→`created_utc`, `source`→`r/`+`subreddit`, `external_url`→`url`,
   `engagement.score`→`score`, `engagement.upvote_ratio`→`upvote_ratio`,
   `engagement.num_comments`→`num_comments`. `tags` (flair) is absent from the listing → default `[]`.

5. **Body enrichment (new step):** after collecting a subreddit's in-range posts, for the subset
   that look like text posts (heuristic: `domain == f"self.{subreddit}"`) — optionally capped to
   top N by `score` — call:
   `GET /v1/reddit/post/comments?url=<https://www.reddit.com + permalink>`,
   read `resp.json()["post"]["selftext"]`, and set the `CollectedItem.content` +
   re-run `extract_keywords(f"{title} {selftext}")`. Link posts keep `content=""` (unchanged from
   today's behavior for non-self posts). Wrap each body call in its own try/except so one failure
   doesn't drop the post — fall back to title-only.

---

## Secrets / CI wiring (GitHub Actions: `.github/workflows/daily-pipeline.yml`)
- Add a repo secret **`SCRAPECREATORS_API_KEY`** and expose it as an `env:` var in the workflow
  (mirror how `REDDIT_PROXY_URL` / `TWITTERAPI_IO_KEY` are wired ~line 50).
- The Mullvad tunnel step can stay; it's no longer required for Reddit but doesn't hurt. You may
  optionally gate it off for Reddit-only runs later.
- For local runs, export `SCRAPECREATORS_API_KEY` in the environment / `.env`.

## Acceptance / verification
- `reddit_<date>.json` is back to a healthy size (hundreds of items, not 205 bytes).
- Spot-check a few `is_self` text posts: `content` is non-empty and matches the Reddit body.
- Engagement (`score`/`num_comments`/`upvote_ratio`) populated on all items.
- Pagination works (more than ~25 items per busy sub).
- Credit burn per run is sane (log `credits_remaining` at start/end of the run). With ~15 subs and
  top-N body gating, expect well under ~$0.50/run.

## Out of scope / do not do
- Do NOT reintroduce Reddit OAuth or the public `.json` endpoint — both are dead.
- Do NOT hardcode the API key. Env var only.
- Don't change `CollectedItem`'s schema or the analyzer; only the gatherer's source + an optional
  body-enrichment step.
