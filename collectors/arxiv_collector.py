#!/usr/bin/env python3
"""
arXiv Paper Collector
Collects papers from arXiv using the API with date range queries.
"""

import feedparser
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ArxivCollector:
    """Collects papers from arXiv using the API."""

    # arXiv categories relevant to AI
    CATEGORIES = {
        'cs.AI': 'Artificial Intelligence',
        'cs.LG': 'Machine Learning',
        'cs.CL': 'Computation and Language',
        'cs.CV': 'Computer Vision',
        'cs.NE': 'Neural and Evolutionary Computing',
        'cs.RO': 'Robotics',
        'stat.ML': 'Machine Learning (Statistics)'
    }

    API_BASE = "https://export.arxiv.org/api/query"

    def __init__(self, categories: List[str] = None, lookback_hours: int = 24, target_date: str = None):
        """
        Initialize arXiv collector.

        Args:
            categories: List of arXiv category codes (e.g., ['cs.AI', 'cs.LG'])
            lookback_hours: Only collect papers from the last N hours (used if target_date not set)
            target_date: Specific date to collect (format: YYYY-MM-DD). Collects 00:00Z-23:59Z for that date.
        """
        self.categories = categories or list(self.CATEGORIES.keys())
        self.lookback_hours = lookback_hours
        self.target_date = target_date

        if target_date:
            # Parse target date and set start/end times (in UTC)
            date_obj = datetime.strptime(target_date, '%Y-%m-%d')
            self.start_time = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            self.end_time = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
            logger.info(f"Collecting arXiv papers for {target_date} (00:00Z - 23:59Z)")
        else:
            self.start_time = datetime.utcnow() - timedelta(hours=lookback_hours)
            self.end_time = datetime.utcnow()
            logger.info(f"Collecting arXiv papers from last {lookback_hours} hours")

    def _generate_content_hash(self, arxiv_id: str) -> str:
        """Generate a unique hash for paper deduplication."""
        return hashlib.sha256(arxiv_id.encode('utf-8')).hexdigest()

    def _parse_arxiv_id(self, link: str) -> str:
        """Extract arXiv ID from link."""
        # Link format: http://arxiv.org/abs/2301.12345v1
        try:
            return link.split('/abs/')[-1].split('v')[0]
        except:
            return link

    def _format_date_for_api(self, dt: datetime) -> str:
        """Format datetime for arXiv API query (YYYYMMDDHHMM in UTC)."""
        return dt.strftime('%Y%m%d%H%M')

    def _parse_api_date(self, date_str: str) -> datetime:
        """Parse date string from arXiv API response."""
        try:
            # arXiv API returns ISO format: 2026-01-04T18:00:00Z
            if date_str:
                # Remove timezone suffix and parse
                clean_date = date_str.replace('Z', '').replace('T', ' ')
                if '.' in clean_date:
                    return datetime.strptime(clean_date.split('.')[0], '%Y-%m-%d %H:%M:%S')
                return datetime.strptime(clean_date, '%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")
        return datetime.now()

    def _fetch_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Fetch papers from a single arXiv category using the API.

        Args:
            category: arXiv category code (e.g., 'cs.AI')

        Returns:
            List of normalized paper dictionaries
        """
        papers = []

        # Build the API query with date range
        # Format: cat:cs.AI AND submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]
        start_date_str = self._format_date_for_api(self.start_time)
        end_date_str = self._format_date_for_api(self.end_time)

        # Build search query
        search_query = f"cat:{category} AND submittedDate:[{start_date_str} TO {end_date_str}]"

        params = {
            'search_query': search_query,
            'start': 0,
            'max_results': 100,  # Max per category per day
            'sortBy': 'submittedDate',
            'sortOrder': 'descending'
        }

        try:
            logger.info(f"Fetching arXiv category: {category} (date range: {start_date_str} to {end_date_str})")

            response = requests.get(self.API_BASE, params=params, timeout=60)
            response.raise_for_status()

            # Parse the Atom XML response using feedparser
            feed = feedparser.parse(response.content)

            category_name = self.CATEGORIES.get(category, category)

            # Check for total results
            total_results = int(feed.feed.get('opensearch_totalresults', 0))
            logger.info(f"arXiv API returned {total_results} total results for {category}")

            for entry in feed.entries:
                try:
                    # Parse publication date from the entry
                    pub_date = self._parse_api_date(entry.get('published', ''))

                    # Extract arXiv ID from the entry ID
                    # Format: http://arxiv.org/abs/2601.00123v1
                    entry_id = entry.get('id', '')
                    arxiv_id = self._parse_arxiv_id(entry_id)

                    # Extract authors
                    authors = []
                    if hasattr(entry, 'authors'):
                        authors = [author.get('name', '') for author in entry.authors]
                    elif hasattr(entry, 'author'):
                        authors = [entry.author]

                    # Extract abstract (summary in Atom)
                    abstract = entry.get('summary', '').replace('\n', ' ').strip()

                    # Extract primary category
                    primary_category = category
                    if hasattr(entry, 'arxiv_primary_category'):
                        primary_category = entry.arxiv_primary_category.get('term', category)

                    # Normalize paper data
                    paper = {
                        'id': self._generate_content_hash(arxiv_id),
                        'arxiv_id': arxiv_id,
                        'title': entry.get('title', 'No Title').replace('\n', ' ').strip(),
                        'url': entry_id,
                        'abstract': abstract,
                        'authors': authors,
                        'published': pub_date.isoformat(),
                        'category': primary_category,
                        'category_name': category_name,
                        'source': 'arXiv',
                        'source_type': 'arxiv',
                        'pdf_url': entry_id.replace('/abs/', '/pdf/') + '.pdf',
                        'collected_at': datetime.now().isoformat()
                    }

                    papers.append(paper)

                except Exception as e:
                    logger.error(f"Error processing paper from {category}: {e}")
                    continue

            logger.info(f"Collected {len(papers)} papers from {category}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching arXiv category {category}: {e}")
        except Exception as e:
            logger.error(f"Error processing arXiv category {category}: {e}")

        return papers

    def collect(self) -> List[Dict[str, Any]]:
        """
        Collect papers from all arXiv categories in parallel.

        Returns:
            List of all collected papers
        """
        all_papers = []
        seen_ids = set()  # For deduplication across categories

        logger.info(f"Starting arXiv collection from {len(self.categories)} categories")
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=min(len(self.categories), 3)) as executor:
            # Submit all category fetching tasks
            # Note: Limit workers to 3 to be nice to arXiv API
            future_to_category = {
                executor.submit(self._fetch_category, category): category
                for category in self.categories
            }

            # Collect results as they complete
            for future in as_completed(future_to_category):
                category = future_to_category[future]
                try:
                    papers = future.result()
                    # Deduplicate across categories (papers can be in multiple categories)
                    for paper in papers:
                        if paper['arxiv_id'] not in seen_ids:
                            seen_ids.add(paper['arxiv_id'])
                            all_papers.append(paper)
                except Exception as e:
                    logger.error(f"Category {category} generated an exception: {e}")

        elapsed_time = time.time() - start_time
        logger.info(f"arXiv collection completed in {elapsed_time:.2f}s. Total unique papers: {len(all_papers)}")

        return all_papers

    def save_to_file(self, papers: List[Dict[str, Any]], output_path: str):
        """Save collected papers to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'collected_at': datetime.now().isoformat(),
                'count': len(papers),
                'papers': papers
            }, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved {len(papers)} papers to {output_path}")


if __name__ == '__main__':
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python arxiv_collector.py <output_file> [categories...]")
        print("Example: python arxiv_collector.py papers.json cs.AI cs.LG cs.CL")
        sys.exit(1)

    output_file = sys.argv[1]
    categories = sys.argv[2:] if len(sys.argv) > 2 else None

    # Collect papers
    collector = ArxivCollector(categories=categories, lookback_hours=24)
    papers = collector.collect()

    # Save results
    collector.save_to_file(papers, output_file)
