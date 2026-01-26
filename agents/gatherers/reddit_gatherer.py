"""
Reddit Gatherer - Collects posts from Reddit subreddits.

Uses the free Reddit JSON endpoint (no API key needed).
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import List, Optional, Set

import requests

from ..base import BaseGatherer, CollectedItem

logger = logging.getLogger(__name__)

# Reddit JSON endpoint configuration
REDDIT_USER_AGENT = "AI-News-Aggregator/1.0"


class RedditGatherer(BaseGatherer):
    """Gathers posts from Reddit subreddits."""

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None
    ):
        super().__init__(config_dir, data_dir, lookback_hours, target_date)
        self.subreddits = self.load_config_list('reddit_subreddits.txt')
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

    async def gather(self) -> List[CollectedItem]:
        """Gather posts from configured subreddits."""
        logger.info(f"Starting Reddit collection from {len(self.subreddits)} subreddits")

        # Run blocking code in thread pool to not block event loop
        # This allows other gatherers (Social, Papers) to run in parallel
        loop = asyncio.get_event_loop()
        all_posts = await loop.run_in_executor(None, self._gather_sync)

        logger.info(f"Collected {len(all_posts)} posts from Reddit")

        # Save to file
        self.save_to_file(all_posts, f'reddit_{self.target_date}.json')

        return all_posts

    def _gather_sync(self) -> List[CollectedItem]:
        """Synchronous gathering - runs in thread pool to not block event loop."""
        seen_ids = set()
        all_posts = []

        for sub in self.subreddits:
            posts = self._fetch_subreddit(sub, seen_ids)
            all_posts.extend(posts)

        return all_posts

    def _fetch_subreddit(self, subreddit: str, seen_ids: set = None) -> List[CollectedItem]:
        """Fetch hot posts from a single subreddit with pagination."""
        posts = []
        headers = {"User-Agent": REDDIT_USER_AGENT}
        after = None
        max_search_pages = 30  # Search up to 30 pages to find first post in range
        max_drain_pages = 10  # After finding posts, continue up to 10 more pages
        page = 0
        drain_pages = 0
        found_any_match = False
        if seen_ids is None:
            seen_ids = set()

        try:
            logger.info(f"Fetching posts from r/{subreddit}")

            # Paginate through pages - hot posts aren't date-sorted
            # so matching posts could be on any page
            while page < max_search_pages:
                # Build params with pagination cursor
                params = {"limit": 100}
                if after:
                    params["after"] = after

                response = requests.get(
                    f"https://www.reddit.com/r/{subreddit}/hot.json",
                    params=params,
                    headers=headers,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                post_list = data.get('data', {}).get('children', [])
                if not post_list:
                    break

                found_on_this_page = False
                for post_wrapper in post_list:
                    try:
                        post_data = post_wrapper.get('data', {})

                        # Skip duplicates (same post seen on different pages)
                        post_id = post_data.get('id', '')
                        if post_id in seen_ids:
                            continue
                        seen_ids.add(post_id)

                        # Skip stickied posts
                        if post_data.get('stickied', False):
                            continue

                        # Parse created time
                        created_utc = post_data.get('created_utc', 0)
                        pub_date = datetime.fromtimestamp(created_utc)

                        # Skip if outside date range
                        if not self.is_in_date_range(pub_date):
                            continue

                        title = post_data.get('title', '')
                        selftext = post_data.get('selftext', '')

                        post = CollectedItem(
                            id=self.generate_id('reddit', post_id),
                            title=title,
                            content=selftext,
                            url=f"https://reddit.com{post_data.get('permalink', '')}",
                            author=f"u/{post_data.get('author', '')}",
                            published=pub_date.isoformat(),
                            source=f"r/{subreddit}",
                            source_type='reddit',
                            tags=[post_data.get('link_flair_text', '')] if post_data.get('link_flair_text') else [],
                            metadata={
                                'platform_id': post_id,
                                'subreddit': subreddit,
                                'external_url': post_data.get('url', ''),
                                'is_self': post_data.get('is_self', False),
                                'engagement': {
                                    'score': post_data.get('score', 0),
                                    'upvote_ratio': post_data.get('upvote_ratio', 0),
                                    'num_comments': post_data.get('num_comments', 0)
                                }
                            },
                            keywords=self.extract_keywords(f"{title} {selftext}")
                        )

                        posts.append(post)
                        found_on_this_page = True

                    except Exception as e:
                        logger.error(f"Error processing Reddit post from r/{subreddit}: {e}")

                # Track pagination phases
                if found_on_this_page:
                    found_any_match = True
                elif found_any_match:
                    # Stop on first empty page after finding matches
                    logger.info(f"Stopping r/{subreddit} - no matches on page {page + 1}")
                    break

                # Get next page cursor
                after = data.get('data', {}).get('after')
                if not after:
                    break

                # Check page limits based on phase
                if found_any_match:
                    drain_pages += 1
                    if drain_pages >= max_drain_pages:
                        logger.info(f"Stopping r/{subreddit} after {max_drain_pages} drain pages")
                        break

                page += 1
                time.sleep(1)  # Rate limit between pages

            logger.info(f"Collected {len(posts)} posts from r/{subreddit} ({page + 1} pages)")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching posts from r/{subreddit}: {e}")

        # Rate limit between subreddits
        time.sleep(1)

        return posts
