"""
Reddit Gatherer - Collects posts from Reddit subreddits via the ScrapeCreators API.

Reddit's free unauthenticated ``.json`` endpoint was killed at the endpoint level
(HTTP 403 from every exit IP) and OAuth is also unavailable. This gatherer uses the
ScrapeCreators third-party API (header ``x-api-key``), which unblocks Reddit
server-side and returns ``.json``-equivalent data plus post bodies and comments.

Two endpoints are used:
  * ``GET /v1/reddit/subreddit``      -> listing (discovery + ranking, ~23 posts/page)
  * ``GET /v1/reddit/post/comments``  -> per-post body (selftext) + top comments

Collection strategy: ``sort=new`` (strictly reverse-chronological) is paged
newest -> oldest and stopped once the coverage window is passed. This is both
credit-cheap (only pages that overlap the window are fetched) and complete for a
date-bounded run. Top-scoring posts are then enriched: self posts get their body
text; high-discussion link posts get a digest of the top community comments
(the same call returns both, so comments come "for free").
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional

import requests

from ..base import BaseGatherer, CollectedItem, deduplicate_items

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read a non-negative int from the environment, falling back on bad input."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# ScrapeCreators API configuration
SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY", "")
SCRAPECREATORS_BASE = os.getenv("SCRAPECREATORS_BASE", "https://api.scrapecreators.com")

# Egress: ScrapeCreators unblocks Reddit server-side, so its calls go DIRECT and must
# NOT be captured by the pipeline-wide HTTPS_PROXY/ALL_PROXY exports (Mullvad). The old
# REDDIT_PROXY_URL is now a no-op for Reddit; a dedicated override is provided for the
# rare case the ScrapeCreators traffic itself should be proxied.
SCRAPECREATORS_PROXY_URL = os.getenv("SCRAPECREATORS_PROXY_URL", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "AI-News-Aggregator/1.0")

# Tunables (all env-overridable)
REDDIT_SORT = os.getenv("REDDIT_SORT", "new")
REDDIT_MAX_PAGES = _env_int("REDDIT_MAX_PAGES", 20, minimum=1)
REDDIT_BODY_TOP_N = _env_int("REDDIT_BODY_TOP_N", 12, minimum=0)
REDDIT_MIN_COMMENTS_FOR_DIGEST = _env_int("REDDIT_MIN_COMMENTS_FOR_DIGEST", 8, minimum=0)
REDDIT_CREDIT_BUDGET = _env_int("REDDIT_CREDIT_BUDGET", 600, minimum=1)
REDDIT_FETCH_WORKERS = _env_int("REDDIT_FETCH_WORKERS", 6, minimum=1)
# Consecutive older-than-window posts that trigger a stop. >1 absorbs out-of-order
# pinned/stickied posts (which ScrapeCreators does not flag) at the top of a listing.
REDDIT_OLDER_STOP_THRESHOLD = _env_int("REDDIT_OLDER_STOP_THRESHOLD", 3, minimum=1)
REDDIT_REQUEST_TIMEOUT = _env_int("REDDIT_REQUEST_TIMEOUT", 60, minimum=5)

# Listing/detail status codes that are retryable transient failures
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class FatalScrapeError(Exception):
    """Non-recoverable ScrapeCreators error (bad key / out of credits) - abort the run."""


class RedditGatherer(BaseGatherer):
    """Gathers posts from Reddit subreddits via the ScrapeCreators API."""

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None
    ):
        super().__init__(config_dir, data_dir, lookback_hours, target_date)
        self.subreddits = self.load_config_list('reddit_subreddits.txt')

        # Config snapshot (instance copies so tests/overrides are explicit)
        self.sort = REDDIT_SORT
        self.max_pages = REDDIT_MAX_PAGES
        self.body_top_n = REDDIT_BODY_TOP_N
        self.min_comments_for_digest = REDDIT_MIN_COMMENTS_FOR_DIGEST
        self.credit_budget = REDDIT_CREDIT_BUDGET
        self.fetch_workers = REDDIT_FETCH_WORKERS
        self.older_stop_threshold = REDDIT_OLDER_STOP_THRESHOLD
        self.timeout = REDDIT_REQUEST_TIMEOUT

        # Shared, thread-safe run state (gathering runs across a thread pool)
        self._lock = Lock()
        self._calls_made = 0            # logical API calls issued this run (budget unit)
        self._credits_remaining: Optional[int] = None  # latest observed balance
        self._stop_calls = False        # set when budget hit or a fatal error occurs

        if not self.subreddits:
            # Default subreddits if none configured
            self.subreddits = [
                'MachineLearning',
                'artificial',
                'LocalLLaMA',
                'ChatGPT',
                'OpenAI'
            ]

    @property
    def category(self) -> str:
        return 'reddit'

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    async def gather(self) -> List[CollectedItem]:
        """Gather posts from configured subreddits."""
        if not SCRAPECREATORS_API_KEY:
            logger.error(
                "SCRAPECREATORS_API_KEY is not set - Reddit collection is disabled. "
                "Set the env var / GitHub secret to restore Reddit data."
            )
            self.save_to_file([], f'reddit_{self.target_date}.json')
            return []

        # Backfill is depth-limited: sort=new pages from "now" backwards, so a coverage
        # window many days in the past costs a lot of pages and may hit the page cap.
        days_back = (datetime.now().date() - datetime.strptime(self.coverage_date, '%Y-%m-%d').date()).days
        if days_back > 2:
            logger.warning(
                f"Reddit coverage date {self.coverage_date} is {days_back} days back; "
                f"sort=new backfill is depth-limited (max_pages={self.max_pages}) and may under-collect."
            )

        logger.info(f"Starting Reddit collection from {len(self.subreddits)} subreddits (sort={self.sort})")

        # Run blocking code in an owned thread pool so Reddit's work doesn't consume the
        # shared default executor used by the concurrently-running social/research gatherers.
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='reddit-driver') as driver:
            all_posts = await loop.run_in_executor(driver, self._gather_sync)

        logger.info(f"Collected {len(all_posts)} posts from Reddit")
        self.save_to_file(all_posts, f'reddit_{self.target_date}.json')
        return all_posts

    # ------------------------------------------------------------------ #
    # Orchestration (synchronous, thread-pool based)
    # ------------------------------------------------------------------ #

    def _gather_sync(self) -> List[CollectedItem]:
        """Fetch all subreddits concurrently; runs in a worker thread."""
        start_balance = self._fetch_credit_balance()
        if start_balance is not None:
            logger.info(f"ScrapeCreators credit balance at start: {start_balance}")

        all_posts: List[CollectedItem] = []
        with ThreadPoolExecutor(max_workers=self.fetch_workers, thread_name_prefix='reddit-sub') as ex:
            future_to_sub = {ex.submit(self._fetch_subreddit, sub): sub for sub in self.subreddits}
            for future in as_completed(future_to_sub):
                sub = future_to_sub[future]
                try:
                    all_posts.extend(future.result())
                except Exception as e:  # defensive: a worker should not crash the run
                    logger.error(f"r/{sub} worker failed: {e}")

        # Belt-and-suspenders dedup across subs (per-sub dedup already applied).
        all_posts = deduplicate_items(all_posts)

        with self._lock:
            calls = self._calls_made
            remaining = self._credits_remaining
            stopped = self._stop_calls
        consumed = (start_balance - remaining) if (start_balance is not None and remaining is not None) else None
        logger.info(
            f"ScrapeCreators usage: {calls} calls this run; "
            f"credits_remaining={remaining}; credits_consumed={consumed}"
            + ("; STOPPED EARLY (budget/fatal)" if stopped else "")
        )

        # Surface credit usage/balance in the end-of-run cost summary.
        try:
            from ..cost_tracker import get_tracker
            # ScrapeCreators bills 1 credit per call at ~$0.99 / 1000 credits.
            billed = consumed if consumed is not None else calls
            get_tracker().record_external_api(
                "ScrapeCreators (Reddit)",
                calls=calls,
                credits_consumed=consumed,
                balance=remaining,
                est_cost_usd=round((billed or 0) * 0.99 / 1000, 4),
                note=("STOPPED EARLY (budget/fatal)" if stopped else None),
            )
        except Exception as e:  # never let reporting break collection
            logger.debug(f"Could not record ScrapeCreators usage: {e}")

        return all_posts

    # ------------------------------------------------------------------ #
    # Per-subreddit collection
    # ------------------------------------------------------------------ #

    def _fetch_subreddit(self, subreddit: str) -> List[CollectedItem]:
        """Fetch in-window posts for one subreddit, then enrich the top-scoring ones."""
        session = self._make_session()
        pairs: List[tuple] = []  # (CollectedItem, raw_post_dict) kept aligned for enrichment
        seen_ids: set = set()
        after = None
        pages = 0
        consecutive_older = 0

        try:
            while pages < self.max_pages:
                if self._stop_calls:
                    break

                params = {"subreddit": subreddit, "sort": self.sort}
                if after:
                    params["after"] = after

                data = self._api_get(session, "/v1/reddit/subreddit", params)
                if data is None:  # soft failure or stop_calls
                    break

                posts = data.get("posts") or []
                if not posts:
                    break

                for post in posts:
                    post_id = post.get("id", "")
                    if not post_id or post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    # ScrapeCreators does not flag stickied posts (always null); defensive no-op.
                    if post.get("stickied"):
                        continue

                    created = post.get("created_utc")
                    if not created:
                        continue
                    try:
                        pub_dt = datetime.fromtimestamp(created)
                    except (ValueError, OSError, OverflowError):
                        continue

                    if pub_dt > self.end_time:
                        # "Today overhang": newer than the coverage window. Keep paging.
                        consecutive_older = 0
                        continue
                    if pub_dt < self.start_time:
                        # Older than the window. With sort=new everything below is older too,
                        # but absorb a few out-of-order pinned posts before committing to stop.
                        consecutive_older += 1
                        continue

                    consecutive_older = 0
                    pairs.append((self._build_item(subreddit, post, pub_dt), post))

                if consecutive_older >= self.older_stop_threshold:
                    break

                after = data.get("after")
                if not after:
                    break
                pages += 1

            logger.info(f"r/{subreddit}: collected {len(pairs)} in-window posts across {pages + 1} page(s)")
            if pages >= self.max_pages:
                logger.warning(
                    f"r/{subreddit}: hit max_pages={self.max_pages} before exhausting the window; "
                    f"may be under-collecting."
                )

            if pairs and not self._stop_calls:
                self._enrich_pairs(session, subreddit, pairs)

        except FatalScrapeError as e:
            with self._lock:
                self._stop_calls = True
            logger.error(f"Aborting Reddit collection (fatal): {e}")
        except Exception as e:
            logger.error(f"Error fetching r/{subreddit}: {e}")
        finally:
            session.close()

        return [item for item, _ in pairs]

    def _build_item(self, subreddit: str, post: Dict[str, Any], pub_dt: datetime) -> CollectedItem:
        """Map a ScrapeCreators listing post to a CollectedItem (body filled in later)."""
        post_id = post.get("id", "")
        title = post.get("title", "") or ""
        domain = (post.get("domain") or "").lower()
        return CollectedItem(
            id=self.generate_id('reddit', post_id),
            title=title,
            content="",  # enriched later for top-N posts
            url=f"https://reddit.com{post.get('permalink', '')}",
            author=f"u/{post.get('author', '')}",
            published=pub_dt.isoformat(),
            source=f"r/{subreddit}",
            source_type='reddit',
            tags=[],  # flair not exposed by ScrapeCreators listings
            metadata={
                'platform_id': post_id,
                'subreddit': subreddit,
                'external_url': post.get('url', ''),
                'is_self': domain.startswith('self.'),
                'engagement': {
                    'score': post.get('score', 0) or 0,
                    'upvote_ratio': post.get('upvote_ratio', 0) or 0,
                    'num_comments': post.get('num_comments', 0) or 0,
                },
            },
            keywords=self.extract_keywords(title),
        )

    # ------------------------------------------------------------------ #
    # Body / comment enrichment
    # ------------------------------------------------------------------ #

    def _enrich_pairs(self, session: requests.Session, subreddit: str, pairs: List[tuple]) -> None:
        """Enrich the top-N posts (by score) with body text and/or a comment digest."""
        ranked = sorted(pairs, key=lambda p: p[1].get("score", 0) or 0, reverse=True)
        for item, post in ranked[:self.body_top_n]:
            if self._stop_calls:
                break
            try:
                self._enrich_one(session, item, post)
            except FatalScrapeError:
                raise  # bubble to _fetch_subreddit to stop the whole run
            except Exception as e:
                logger.warning(f"Enrichment failed for {item.url}: {e}")

    def _enrich_one(self, session: requests.Session, item: CollectedItem, post: Dict[str, Any]) -> None:
        """One post/comments call: self -> selftext; link -> top-comment discussion digest."""
        is_self = (post.get("domain") or "").lower().startswith("self.")
        num_comments = post.get("num_comments", 0) or 0

        # Link posts with little discussion aren't worth a credit (their substance is the
        # linked article, captured elsewhere). Self posts are always enriched (body text).
        if not is_self and num_comments < self.min_comments_for_digest:
            return

        permalink = post.get("permalink", "")
        if not permalink:
            return

        data = self._api_get(session, "/v1/reddit/post/comments", {"url": f"https://www.reddit.com{permalink}"})
        if data is None:
            return

        detail = data.get("post") or {}
        comments = data.get("comments") or []

        content = ""
        if is_self:
            content = (detail.get("selftext") or "").strip()
        if not content:
            # Link post (or empty self post) -> digest the community discussion.
            content = self._build_comment_digest(comments)

        if content:
            item.content = content
            item.keywords = self.extract_keywords(f"{item.title} {content}")

    @staticmethod
    def _build_comment_digest(comments: List[Dict[str, Any]], max_comments: int = 6, max_len: int = 220) -> str:
        """Build a compact, markdown (HTML-safe via downstream nh3) top-comments digest."""
        cleaned = []
        for c in comments:
            body = (c.get("body") or "").strip()
            if not body:
                continue
            author = (c.get("author") or "").lower()
            if author in ("automoderator", "[deleted]"):
                continue
            low = body.lower()
            if "i am a bot" in low or "performed automatically" in low:
                continue
            body = " ".join(body.split())  # collapse newlines so each comment is one bullet
            if len(body) > max_len:
                body = body[:max_len].rstrip() + "…"
            cleaned.append((c.get("score", 0) or 0, body))

        if not cleaned:
            return ""

        cleaned.sort(key=lambda x: x[0], reverse=True)
        lines = ["**Top community comments:**", ""]
        for score, body in cleaned[:max_comments]:
            lines.append(f"- (▲{score}) {body}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # HTTP layer
    # ------------------------------------------------------------------ #

    def _make_session(self) -> requests.Session:
        """Create a session that ignores ambient proxy env vars (direct egress by default)."""
        session = requests.Session()
        session.headers.update({"User-Agent": REDDIT_USER_AGENT})
        # Ignore HTTPS_PROXY/ALL_PROXY exported pipeline-wide (Mullvad) - ScrapeCreators
        # unblocks server-side and must go direct.
        session.trust_env = False
        if SCRAPECREATORS_PROXY_URL:
            session.proxies.update({"http": SCRAPECREATORS_PROXY_URL, "https": SCRAPECREATORS_PROXY_URL})
            logger.info("ScrapeCreators session using explicit SCRAPECREATORS_PROXY_URL")
        return session

    def _fetch_credit_balance(self) -> Optional[int]:
        """Read the real account balance (does not consume a credit)."""
        try:
            session = self._make_session()
            resp = session.get(
                f"{SCRAPECREATORS_BASE}/v1/account/credit-balance",
                headers={"x-api-key": SCRAPECREATORS_API_KEY},
                timeout=self.timeout,
            )
            session.close()
            if resp.status_code == 200:
                data = resp.json()
                return data.get("creditCount", data.get("credits_remaining"))
            logger.warning(f"Credit-balance probe returned HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Could not fetch ScrapeCreators credit balance: {e}")
        return None

    def _api_get(self, session: requests.Session, path: str, params: dict) -> Optional[Dict[str, Any]]:
        """
        Budgeted GET against ScrapeCreators with retry/backoff.

        Returns the parsed JSON on success, or None on a soft failure / when the credit
        budget is exhausted. Raises FatalScrapeError on bad-key / out-of-credits.
        """
        # Budget gate (one logical call == one budget unit).
        with self._lock:
            if self._stop_calls:
                return None
            if self._calls_made >= self.credit_budget:
                if not self._stop_calls:
                    self._stop_calls = True
                    logger.warning(
                        f"Reddit credit budget ({self.credit_budget} calls) reached; "
                        f"stopping further ScrapeCreators calls."
                    )
                return None
            self._calls_made += 1

        url = f"{SCRAPECREATORS_BASE}{path}"
        headers = {"x-api-key": SCRAPECREATORS_API_KEY}

        for attempt in range(3):
            try:
                resp = session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.exceptions.RequestException as e:
                delay = 2 ** attempt
                logger.warning(f"ScrapeCreators request error for {path} ({e}); retrying in {delay}s")
                time.sleep(delay)
                continue

            status = resp.status_code

            # Bad key / out of credits are not transient - abort fast (note the documented
            # quirk where a bad/empty key may surface as 402 "out of credits").
            if status in (401, 402):
                raise FatalScrapeError(f"ScrapeCreators HTTP {status} for {path}: {resp.text[:200]}")

            if status in _RETRYABLE_STATUS:
                delay = 2 ** attempt
                logger.warning(f"ScrapeCreators HTTP {status} for {path}; retrying in {delay}s")
                time.sleep(delay)
                continue

            if status != 200:
                logger.warning(f"ScrapeCreators HTTP {status} for {path}; skipping")
                return None

            try:
                data = resp.json()
            except ValueError:
                logger.warning(f"ScrapeCreators returned non-JSON for {path}; skipping")
                return None

            if not data.get("success", False):
                message = str(data.get("message", ""))
                low = message.lower()
                if "credit" in low or "api key" in low or "unauthor" in low:
                    raise FatalScrapeError(f"ScrapeCreators success=false (fatal) for {path}: {message[:200]}")
                delay = 2 ** attempt
                logger.warning(f"ScrapeCreators success=false for {path} ({message[:120]}); retrying in {delay}s")
                time.sleep(delay)
                continue

            credits = data.get("credits_remaining")
            if credits is not None:
                with self._lock:
                    self._credits_remaining = credits
            return data

        logger.warning(f"ScrapeCreators request to {path} failed after retries; skipping")
        return None
