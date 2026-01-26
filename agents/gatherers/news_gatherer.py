"""
News Gatherer - Collects news articles from RSS feeds and linked articles.

Combines RSS collection with smart link following from social media posts.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
from dateutil import parser as date_parser

from ..base import BaseGatherer, CollectedItem, deduplicate_items
from ..llm_client import AnthropicClient
from .link_follower import LinkFollower

logger = logging.getLogger(__name__)


class NewsGatherer(BaseGatherer):
    """
    Gathers news articles from RSS feeds and linked articles from social posts.

    This gatherer:
    1. Collects articles from configured RSS feeds
    2. Uses LinkFollower to extract articles linked in social media posts
    3. Deduplicates across both sources
    """

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None,
        llm_client: Optional[AnthropicClient] = None,
        max_workers: int = 10,
        prompt_accessor=None
    ):
        super().__init__(config_dir, data_dir, lookback_hours, target_date)

        self.llm_client = llm_client
        self.max_workers = max_workers
        self.link_follower = LinkFollower(llm_client=llm_client, prompt_accessor=prompt_accessor)

        # Load RSS feeds
        self.feeds = self.load_config_list('rss_feeds.txt')
        if not self.feeds:
            logger.warning("No RSS feeds configured")

    @property
    def category(self) -> str:
        return 'news'

    async def gather(self, social_posts: Optional[List[CollectedItem]] = None) -> List[CollectedItem]:
        """
        Gather news articles from RSS and linked articles.

        Args:
            social_posts: Optional list of social posts to extract links from.

        Returns:
            List of collected news articles.
        """
        all_articles = []

        # Phase 1: Collect from RSS feeds
        logger.info(f"Collecting from {len(self.feeds)} RSS feeds")
        rss_articles = await self._collect_rss()
        all_articles.extend(rss_articles)
        logger.info(f"Collected {len(rss_articles)} articles from RSS")

        # Phase 2: Extract linked articles from social posts
        if social_posts:
            logger.info(f"Processing {len(social_posts)} social posts for linked articles")
            linked_articles = await self.link_follower.process_social_posts(
                social_posts,
                self.start_time,
                self.end_time
            )
            all_articles.extend(linked_articles)
            logger.info(f"Extracted {len(linked_articles)} linked articles")

        # Deduplicate by URL
        unique_articles = deduplicate_items(all_articles)
        logger.info(f"Total unique news articles: {len(unique_articles)}")

        # Save to file
        self.save_to_file(unique_articles, f'news_{self.target_date}.json')

        return unique_articles

    async def _collect_rss(self) -> List[CollectedItem]:
        """Collect articles from RSS feeds."""
        loop = asyncio.get_event_loop()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            tasks = [
                loop.run_in_executor(executor, self._fetch_feed, feed_url)
                for feed_url in self.feeds
            ]
            results = await asyncio.gather(*tasks)

        # Flatten results
        articles = []
        for result in results:
            articles.extend(result)

        return articles

    def _fetch_feed(self, feed_url: str) -> List[CollectedItem]:
        """Fetch and parse a single RSS feed."""
        articles = []

        try:
            logger.debug(f"Fetching feed: {feed_url}")
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                # CharacterEncodingOverride is benign - feedparser handles it correctly
                exc = feed.bozo_exception
                if exc and 'CharacterEncodingOverride' in type(exc).__name__:
                    logger.debug(f"Feed encoding override for {feed_url}: {exc}")
                else:
                    logger.warning(f"Feed warning for {feed_url}: {exc}")

            feed_title = feed.feed.get('title', 'Unknown Source')

            for entry in feed.entries:
                try:
                    # Parse publication date
                    pub_date = self._parse_date(
                        entry.get('published_parsed') or entry.get('updated_parsed')
                    )

                    # Skip if outside date range
                    if not self.is_in_date_range(pub_date):
                        continue

                    # Extract content
                    content = ''
                    if hasattr(entry, 'content'):
                        content = entry.content[0].value
                    elif hasattr(entry, 'summary'):
                        content = entry.summary
                    elif hasattr(entry, 'description'):
                        content = entry.description

                    # Strip HTML tags from content
                    import re
                    content_text = re.sub(r'<[^>]+>', '', content)

                    title = entry.get('title', 'No Title')
                    url = entry.get('link', '')

                    article = CollectedItem(
                        id=self.generate_id(url, title),
                        title=title,
                        content=content_text,
                        url=url,
                        author=entry.get('author', 'Unknown'),
                        published=pub_date.isoformat(),
                        source=feed_title,
                        source_type='rss',
                        tags=[tag.term for tag in entry.get('tags', [])],
                        metadata={
                            'feed_url': feed_url,
                            'raw_summary': entry.get('summary', '')[:500]
                        },
                        keywords=self.extract_keywords(f"{title} {content_text}")
                    )

                    articles.append(article)

                except Exception as e:
                    logger.error(f"Error processing entry from {feed_url}: {e}")

            logger.debug(f"Collected {len(articles)} articles from {feed_url}")

        except Exception as e:
            logger.error(f"Error fetching feed {feed_url}: {e}")

        return articles

    def _parse_date(self, date_struct) -> datetime:
        """Parse date from feedparser date structure."""
        if not date_struct:
            return datetime.now()

        try:
            # feedparser returns time.struct_time
            if hasattr(date_struct, 'tm_year'):
                return datetime(*date_struct[:6])

            # Try parsing string
            if isinstance(date_struct, str):
                return date_parser.parse(date_struct)

        except Exception as e:
            logger.warning(f"Failed to parse date: {e}")

        return datetime.now()

    def add_linked_article_urls(self, seen_urls: set):
        """
        Register URLs already collected from RSS to avoid duplicates.

        Args:
            seen_urls: Set of normalized URLs already collected.
        """
        self.link_follower.seen_urls.update(seen_urls)
