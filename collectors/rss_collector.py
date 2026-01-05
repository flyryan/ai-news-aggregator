#!/usr/bin/env python3
"""
RSS Feed Collector
Collects articles from RSS feeds and stores them in a standardized format.
"""

import feedparser
import json
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RSSCollector:
    """Collects and processes RSS feeds."""

    def __init__(self, feeds: List[str], lookback_hours: int = 24, max_workers: int = 10, target_date: str = None):
        """
        Initialize RSS collector.

        Args:
            feeds: List of RSS feed URLs
            lookback_hours: Only collect items from the last N hours (used if target_date not set)
            max_workers: Number of parallel workers for fetching feeds
            target_date: Specific date to collect (format: YYYY-MM-DD). Collects 00:00Z-23:59Z for that date.
        """
        self.feeds = feeds
        self.lookback_hours = lookback_hours
        self.max_workers = max_workers
        self.target_date = target_date

        if target_date:
            # Parse target date and set start/end times
            date_obj = datetime.strptime(target_date, '%Y-%m-%d')
            self.start_time = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            self.end_time = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
            self.cutoff_time = self.start_time
            logger.info(f"Collecting RSS items for {target_date} (00:00Z - 23:59Z)")
        else:
            self.cutoff_time = datetime.now() - timedelta(hours=lookback_hours)
            self.start_time = self.cutoff_time
            self.end_time = datetime.now()
        
    def _generate_content_hash(self, url: str, title: str) -> str:
        """Generate a unique hash for content deduplication."""
        content = f"{url}:{title}".encode('utf-8')
        return hashlib.sha256(content).hexdigest()
    
    def _parse_date(self, date_str: str) -> datetime:
        """Parse various date formats from RSS feeds."""
        if not date_str:
            return datetime.now()
        
        try:
            # feedparser returns a time.struct_time
            if hasattr(date_str, 'tm_year'):
                return datetime(*date_str[:6])
            # Try parsing string directly
            from dateutil import parser
            return parser.parse(date_str)
        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")
            return datetime.now()
    
    def _fetch_feed(self, feed_url: str) -> List[Dict[str, Any]]:
        """
        Fetch and parse a single RSS feed.
        
        Args:
            feed_url: URL of the RSS feed
            
        Returns:
            List of normalized article dictionaries
        """
        articles = []
        
        try:
            logger.info(f"Fetching feed: {feed_url}")
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                logger.warning(f"Feed parsing warning for {feed_url}: {feed.bozo_exception}")
            
            feed_title = feed.feed.get('title', 'Unknown Source')
            
            for entry in feed.entries:
                try:
                    # Parse publication date
                    pub_date = self._parse_date(entry.get('published_parsed') or entry.get('updated_parsed'))

                    # Skip if outside date range
                    if pub_date < self.start_time or pub_date > self.end_time:
                        continue
                    
                    # Extract content
                    content = ''
                    if hasattr(entry, 'content'):
                        content = entry.content[0].value
                    elif hasattr(entry, 'summary'):
                        content = entry.summary
                    elif hasattr(entry, 'description'):
                        content = entry.description
                    
                    # Normalize article data
                    article = {
                        'id': self._generate_content_hash(entry.get('link', ''), entry.get('title', '')),
                        'title': entry.get('title', 'No Title'),
                        'url': entry.get('link', ''),
                        'content': content,
                        'summary': entry.get('summary', '')[:500],  # First 500 chars
                        'author': entry.get('author', 'Unknown'),
                        'published': pub_date.isoformat(),
                        'source': feed_title,
                        'source_url': feed_url,
                        'source_type': 'rss',
                        'tags': [tag.term for tag in entry.get('tags', [])],
                        'collected_at': datetime.now().isoformat()
                    }
                    
                    articles.append(article)
                    
                except Exception as e:
                    logger.error(f"Error processing entry from {feed_url}: {e}")
                    continue
            
            logger.info(f"Collected {len(articles)} articles from {feed_url}")
            
        except Exception as e:
            logger.error(f"Error fetching feed {feed_url}: {e}")
        
        return articles
    
    def collect(self) -> List[Dict[str, Any]]:
        """
        Collect articles from all RSS feeds in parallel.
        
        Returns:
            List of all collected articles
        """
        all_articles = []
        
        logger.info(f"Starting RSS collection from {len(self.feeds)} feeds")
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all feed fetching tasks
            future_to_feed = {
                executor.submit(self._fetch_feed, feed_url): feed_url 
                for feed_url in self.feeds
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_feed):
                feed_url = future_to_feed[future]
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                except Exception as e:
                    logger.error(f"Feed {feed_url} generated an exception: {e}")
        
        elapsed_time = time.time() - start_time
        logger.info(f"RSS collection completed in {elapsed_time:.2f}s. Total articles: {len(all_articles)}")
        
        return all_articles
    
    def save_to_file(self, articles: List[Dict[str, Any]], output_path: str):
        """Save collected articles to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'collected_at': datetime.now().isoformat(),
                'count': len(articles),
                'articles': articles
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved {len(articles)} articles to {output_path}")


def load_feed_list(feed_list_path: str) -> List[str]:
    """Load RSS feed URLs from a text file (one per line)."""
    with open(feed_list_path, 'r') as f:
        feeds = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    return feeds


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python rss_collector.py <feed_list_file> <output_file>")
        sys.exit(1)
    
    feed_list_file = sys.argv[1]
    output_file = sys.argv[2]
    
    # Load feeds
    feeds = load_feed_list(feed_list_file)
    logger.info(f"Loaded {len(feeds)} RSS feeds")
    
    # Collect articles
    collector = RSSCollector(feeds, lookback_hours=24, max_workers=10)
    articles = collector.collect()
    
    # Save results
    collector.save_to_file(articles, output_file)
