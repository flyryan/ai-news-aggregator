"""
News Gatherer - Collects news articles from RSS feeds and linked articles.

Combines RSS collection with smart link following from social media posts.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import requests
from dateutil import parser as date_parser

from ..base import BaseGatherer, CollectedItem, deduplicate_items
from ..llm_client import AnthropicClient
from .link_follower import LinkFollower

logger = logging.getLogger(__name__)

NEWS_USER_AGENT = os.getenv(
    "NEWS_USER_AGENT",
    os.getenv("REDDIT_USER_AGENT", "AI-News-Aggregator/1.0")
)
PIPELINE_PROXY_URL = (
    os.getenv("PIPELINE_PROXY_URL")
    or os.getenv("ALL_PROXY")
    or os.getenv("HTTPS_PROXY")
    or os.getenv("HTTP_PROXY")
)


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
        _feed_headers = {
            "User-Agent": NEWS_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        }
        # Proxied session (default route). Used unless a feed is tagged proxy=off.
        self.feed_session = requests.Session()
        self.feed_session.headers.update(_feed_headers)
        # Direct session: never carries the proxy, and ignores ambient *_PROXY env
        # (trust_env=False) so a feed tagged proxy=off truly bypasses Mullvad.
        self.direct_session = requests.Session()
        self.direct_session.headers.update(_feed_headers)
        self.direct_session.trust_env = False
        if PIPELINE_PROXY_URL:
            self.feed_session.proxies.update({
                "http": PIPELINE_PROXY_URL,
                "https": PIPELINE_PROXY_URL,
            })
            logger.info("News gatherer using configured pipeline proxy for RSS")

        # Load RSS feeds (with optional per-feed routing directives)
        self.feed_specs = self.load_config_feeds('rss_feeds.txt')
        self.feeds = [spec.url for spec in self.feed_specs]
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
                loop.run_in_executor(executor, self._fetch_feed, spec)
                for spec in self.feed_specs
            ]
            results = await asyncio.gather(*tasks)

        # Flatten results
        articles = []
        for result in results:
            articles.extend(result)

        return articles

    def _fetch_feed(self, feed) -> List[CollectedItem]:
        """Fetch and parse a single RSS feed.

        Accepts a FeedSpec (preferred) or a bare URL string. When a proxy is
        configured, feeds tagged proxy=off are fetched via the direct session.
        """
        articles = []

        # Accept either a FeedSpec or a plain URL for backward compatibility.
        feed_url = getattr(feed, 'url', feed)
        use_proxy = getattr(feed, 'use_proxy', None)
        # Default: proxied session (which only carries a proxy if one is set).
        # proxy=off -> direct session that bypasses the proxy entirely.
        session = self.direct_session if use_proxy is False else self.feed_session

        try:
            logger.debug(f"Fetching feed: {feed_url} (proxy={'direct' if use_proxy is False else 'default'})")
            response = session.get(feed_url, timeout=30)
            response.raise_for_status()
            feed = feedparser.parse(
                response.content,
                response_headers={
                    "content-type": response.headers.get("content-type", "")
                }
            )

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
