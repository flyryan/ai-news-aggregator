"""
Research Gatherer - Collects research content from arXiv and research blogs.

Combines:
- arXiv papers via RSS feeds (primary) with OAI-PMH fallback
- Research blog posts from configured RSS feeds (LessWrong, AI Alignment Forum, etc.)

arXiv publishing schedule:
- Announcements happen Sun-Thu at ~8PM ET
- No announcements Friday or Saturday night
- RSS feed on day X contains papers with datestamp X
- OAI-PMH fallback queries for datestamp = report_date to match RSS behavior
"""

import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional

import feedparser
import requests

from ..base import BaseGatherer, CollectedItem
from .arxiv_oai import ArxivOAIHarvester

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from lesswrong_cookie_fetch import DEFAULT_USER_AGENT as LESSWRONG_USER_AGENT
    from lesswrong_cookie_fetch import LessWrongClient
except ImportError:
    LESSWRONG_USER_AGENT = "AI-News-Aggregator/1.0"
    LessWrongClient = None

logger = logging.getLogger(__name__)

# arXiv rate limit for API: minimum 3 seconds between requests
ARXIV_REQUEST_DELAY = 3.5  # seconds

# Network timeout (seconds) for research blog feed fetches. feedparser.parse(url) has no
# network timeout, so a single unresponsive feed would hang the whole gatherer (and Phase 1).
RESEARCH_FEED_TIMEOUT = float(os.getenv('RESEARCH_FEED_TIMEOUT', '20'))


class ResearchGatherer(BaseGatherer):
    """Gathers research content from arXiv and research blogs."""

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
    RSS_BASE = "https://rss.arxiv.org/rss"
    TREND_RESEARCH_FEED_MARKERS = (
        'feeds.trendmicro.com/trendmicrosimplysecurity',
        'trend micro research'
    )
    TREND_AI_RELEVANCE_PATTERNS = (
        r'\btrendai\b',
        r'\bai\b',
        r'\bai[- ](?:enabled|augmented|powered|driven|scaled|first)\b',
        r'\bartificial intelligence\b',
        r'\bmachine learning\b',
        r'\bml\b',
        r'\bllm\b',
        r'\bllms\b',
        r'\blarge language model',
        r'\bgenerative ai\b',
        r'\bagentic\b',
        r'\bprompt injection\b',
        r'\bjailbreak',
        r'\bmcp\b',
        r'\bmodel context protocol\b',
        r'\bdeepfake',
        r'\bvibe hacking\b',
    )

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None,
        categories: Optional[List[str]] = None
    ):
        super().__init__(config_dir, data_dir, lookback_hours, target_date)
        self.categories = categories or list(self.CATEGORIES.keys())

        # Load research blog feeds (with optional per-feed routing directives)
        self.research_feed_specs = self.load_config_feeds('research_feeds.txt')
        self.research_feeds = [spec.url for spec in self.research_feed_specs]
        if self.research_feeds:
            logger.info(f"Loaded {len(self.research_feeds)} research blog feeds")
        # Direct session for feeds tagged proxy=off. trust_env=False makes requests
        # ignore ALL_PROXY/HTTPS_PROXY/HTTP_PROXY so these feeds truly bypass the
        # Mullvad tunnel. (An empty proxies={} dict is insufficient: requests still
        # merges env proxies via setdefault.) Default feeds use bare requests.get
        # below and continue to inherit the env proxy, preserving prior behavior.
        self.direct_session = requests.Session()
        self.direct_session.trust_env = False

    @property
    def category(self) -> str:
        return 'research'

    def _is_current_collection(self) -> bool:
        """Check if we're collecting for today's papers (RSS available) vs historical (API only)."""
        today = datetime.now().strftime('%Y-%m-%d')
        # RSS only has today's announcements, which cover yesterday's submissions
        # So RSS is valid when report_date == today
        return self.report_date == today

    def _get_arxiv_collection_mode(self) -> tuple:
        """
        Determine arXiv collection mode based on day of week.

        arXiv publishing schedule:
        - No announcements Friday or Saturday night
        - Saturday/Sunday reports: skip arXiv (no new papers)
        - Monday: catch-up query for Sat-Mon to catch any anomalies

        Returns:
            Tuple of (mode, from_date) where mode is:
            - 'skip': Saturday/Sunday - don't collect arXiv papers
            - 'catchup': Monday - 3-day range (Sat through Mon)
            - 'normal': Tue-Fri - single day query
        """
        from datetime import timedelta

        report_dt = datetime.strptime(self.report_date, '%Y-%m-%d')
        day_of_week = report_dt.strftime('%A')

        if day_of_week in ('Saturday', 'Sunday'):
            return ('skip', None)
        elif day_of_week == 'Monday':
            # Query from Saturday through Monday (3-day range)
            saturday = report_dt - timedelta(days=2)
            return ('catchup', saturday.strftime('%Y-%m-%d'))
        else:
            return ('normal', None)

    async def gather(self) -> List[CollectedItem]:
        """Gather research content from arXiv and research blogs in parallel."""
        logger.info(f"Starting research collection")
        logger.info(f"Report date: {self.report_date}, Coverage date: {self.coverage_date}")

        # Run arXiv and research blog collection in parallel
        arxiv_task = self._collect_arxiv()
        research_blog_task = self._collect_research_blogs()

        arxiv_papers, blog_posts = await asyncio.gather(arxiv_task, research_blog_task)

        # Combine results
        all_items = arxiv_papers + blog_posts

        logger.info(f"Total research items: {len(all_items)} ({len(arxiv_papers)} arXiv, {len(blog_posts)} blog posts)")

        # Save to file
        self.save_to_file(all_items, f'research_{self.target_date}.json')

        return all_items

    async def _collect_arxiv(self) -> List[CollectedItem]:
        """Collect papers from arXiv using RSS feeds (primary) with OAI-PMH fallback.

        RSS is preferred when available (faster, no pagination). When RSS is empty
        or unavailable (historical dates), falls back to OAI-PMH which can query
        any date range by datestamp (announcement date).

        Weekend handling:
        - Saturday/Sunday: Skip arXiv collection entirely (no new papers)
        - Monday: 3-day catchup query (Sat-Mon) to catch any anomalies

        Uses report_date for queries to match RSS behavior:
        - RSS on day X contains papers with datestamp X
        - OAI-PMH queries datestamp = report_date (or range for Monday)
        """
        # Check weekend/Monday mode first
        mode, from_date = self._get_arxiv_collection_mode()

        if mode == 'skip':
            logger.info(f"Skipping arXiv collection on {self.report_date} (weekend - no new papers)")
            return []

        logger.info(f"Collecting from arXiv ({len(self.categories)} categories)")

        loop = asyncio.get_event_loop()

        # Check if RSS is applicable (only has today's papers)
        use_rss = self._is_current_collection() and mode != 'catchup'

        if use_rss:
            # RSS collection - parallel, no rate limits
            logger.info("Using RSS feeds (current day collection, no rate limits)...")

            rss_tasks = [
                loop.run_in_executor(None, self._fetch_category_rss, cat)
                for cat in self.categories
            ]
            rss_results = await asyncio.gather(*rss_tasks, return_exceptions=True)

            # Check total RSS results
            rss_papers = []
            rss_errors = 0
            for cat, rss_result in zip(self.categories, rss_results):
                if isinstance(rss_result, Exception):
                    logger.warning(f"RSS failed for {cat}: {rss_result}")
                    rss_errors += 1
                elif rss_result:
                    rss_papers.extend(rss_result)

            if rss_papers:
                # RSS returned papers - use them
                logger.info(f"Collected {len(rss_papers)} papers via RSS")
                all_papers = rss_papers
            else:
                # RSS empty (all failed) - fall back to OAI-PMH
                logger.info(f"RSS returned 0 papers, falling back to OAI-PMH for {self.report_date}")
                all_papers = await self._fetch_via_oai(self.report_date)
        elif mode == 'catchup':
            # Monday: 3-day catchup query for Sat-Mon
            logger.info(f"Monday catchup: fetching arXiv papers from {from_date} to {self.report_date}")
            all_papers = await self._fetch_via_oai(from_date, self.report_date)
        else:
            # Historical collection - use OAI-PMH directly (RSS doesn't have past data)
            logger.info(f"Using OAI-PMH (historical collection for {self.report_date})")
            all_papers = await self._fetch_via_oai(self.report_date)

        # Deduplicate by arXiv ID
        seen_arxiv_ids = set()
        unique_papers = []

        for paper in all_papers:
            arxiv_id = paper.metadata.get('arxiv_id', '')
            if arxiv_id and arxiv_id not in seen_arxiv_ids:
                seen_arxiv_ids.add(arxiv_id)
                unique_papers.append(paper)

        logger.info(f"Collected {len(unique_papers)} unique papers from arXiv")
        return unique_papers

    async def _fetch_via_oai(self, from_date: str, until_date: Optional[str] = None) -> List[CollectedItem]:
        """Fetch papers via OAI-PMH for a date or date range.

        Args:
            from_date: Start date in YYYY-MM-DD format
            until_date: End date in YYYY-MM-DD format. If None, uses from_date (single day).

        Returns:
            List of CollectedItem for new papers announced in the date range
        """
        loop = asyncio.get_event_loop()
        harvester = ArxivOAIHarvester(list(self.CATEGORIES.keys()))

        # Run harvester in executor (blocking I/O)
        papers = await loop.run_in_executor(None, harvester.harvest_date, from_date, until_date)

        date_desc = from_date if not until_date or from_date == until_date else f"{from_date} to {until_date}"
        logger.info(f"OAI-PMH returned {len(papers)} new papers for {date_desc}")

        # Convert to CollectedItem format
        items = []
        for paper in papers:
            try:
                primary_cat = paper['categories'].split()[0] if paper.get('categories') else 'cs.AI'
                category_name = self.CATEGORIES.get(primary_cat, primary_cat)

                # Clean title and abstract
                title = (paper.get('title') or 'No Title').replace('\n', ' ').strip()
                abstract = (paper.get('abstract') or '').replace('\n', ' ').strip()

                item = CollectedItem(
                    id=self.generate_id(paper['arxiv_id']),
                    title=title,
                    content=abstract,
                    url=f"http://arxiv.org/abs/{paper['arxiv_id']}",
                    author=paper.get('authors', ''),
                    published=paper['datestamp'],  # Use announcement date
                    source=f'arXiv ({category_name})',
                    source_type='arxiv',
                    tags=[primary_cat],
                    metadata={
                        'arxiv_id': paper['arxiv_id'],
                        'pdf_url': f"http://arxiv.org/pdf/{paper['arxiv_id']}.pdf",
                        'category': primary_cat,
                        'category_name': category_name,
                        'versions': paper.get('versions', []),
                        'comments': paper.get('comments'),
                        'journal_ref': paper.get('journal_ref'),
                        'doi': paper.get('doi'),
                    },
                    keywords=self.extract_keywords(f"{title} {abstract}")
                )
                items.append(item)
            except Exception as e:
                logger.error(f"Error converting OAI-PMH paper {paper.get('arxiv_id', 'unknown')}: {e}")

        return items

    async def _collect_research_blogs(self) -> List[CollectedItem]:
        """Collect posts from research blog RSS feeds.

        Routes LessWrong to GraphQL API (for date-range queries) while using
        RSS for other research feeds.
        """
        if not self.research_feeds:
            logger.info("No research blog feeds configured")
            return []

        logger.info(f"Collecting from {len(self.research_feeds)} research blog feeds")

        loop = asyncio.get_event_loop()

        # Separate LessWrong from other feeds (LessWrong needs GraphQL for date-range queries)
        lesswrong_feeds = [s for s in self.research_feed_specs if 'lesswrong.com' in s.url.lower()]
        other_feeds = [s for s in self.research_feed_specs if 'lesswrong.com' not in s.url.lower()]

        all_posts = []
        seen_urls = set()

        # Fetch LessWrong via GraphQL API (only need to call once, not per-feed)
        if lesswrong_feeds:
            logger.info("Using GraphQL API for LessWrong (RSS doesn't support date-range queries)")
            try:
                lesswrong_posts = await loop.run_in_executor(None, self._fetch_lesswrong_graphql)
                for post in lesswrong_posts:
                    if post.url not in seen_urls:
                        seen_urls.add(post.url)
                        all_posts.append(post)
            except Exception as e:
                logger.error(f"Failed to fetch LessWrong via GraphQL: {e}")

        # Fetch other feeds via RSS (existing behavior)
        if other_feeds:
            tasks = [
                loop.run_in_executor(None, self._fetch_research_feed, spec)
                for spec in other_feeds
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for spec, result in zip(other_feeds, results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to fetch research feed {spec.url}: {result}")
                    continue

                for post in result:
                    if post.url not in seen_urls:
                        seen_urls.add(post.url)
                        all_posts.append(post)

        logger.info(f"Collected {len(all_posts)} posts from research blogs")
        return all_posts

    def _fetch_research_feed(self, feed) -> List[CollectedItem]:
        """Fetch and parse a research blog RSS feed.

        Accepts a FeedSpec (preferred) or a bare URL string. Feeds tagged
        proxy=off bypass any ambient *_PROXY env (e.g. the Mullvad tunnel) by
        using a trust_env=False session that fetches direct.
        """
        posts = []

        # Accept either a FeedSpec or a plain URL for backward compatibility.
        feed_url = getattr(feed, 'url', feed)
        use_proxy = getattr(feed, 'use_proxy', None)

        try:
            logger.debug(
                f"Fetching research feed: {feed_url} "
                f"(proxy={'direct' if use_proxy is False else 'default'})"
            )
            # Fetch with an explicit timeout (feedparser.parse(url) has none) so one
            # unresponsive feed can't hang the gatherer. Mirrors _fetch_category_rss; a
            # browser-ish UA avoids 403s from feeds like Nature.
            headers = {'User-Agent': os.environ.get('NEWS_USER_AGENT') or LESSWRONG_USER_AGENT}
            # proxy=off -> use the trust_env=False session so requests ignores all
            # ambient *_PROXY env vars (incl. ALL_PROXY) and fetches direct.
            # Otherwise use the module-level requests (inherits env, default behavior).
            requester = self.direct_session if use_proxy is False else requests
            response = requester.get(
                feed_url, headers=headers, timeout=RESEARCH_FEED_TIMEOUT,
                allow_redirects=True
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            if feed.bozo:
                # CharacterEncodingOverride is benign - feedparser handles it correctly
                exc = feed.bozo_exception
                if exc and 'CharacterEncodingOverride' in type(exc).__name__:
                    logger.debug(f"Feed encoding override for {feed_url}: {exc}")
                else:
                    logger.warning(f"Feed warning for {feed_url}: {exc}")

            feed_title = feed.feed.get('title', 'Research Blog')
            is_trend_feed = self._is_trend_research_feed(feed_url, feed_title)
            trend_filtered = 0
            trend_retained = 0

            for entry in feed.entries:
                try:
                    # Parse publication date
                    pub_date = self._parse_feed_date(
                        entry.get('published_parsed') or entry.get('updated_parsed')
                    )

                    # An undated/unparseable entry can't be placed in a specific
                    # day's coverage window; skip it visibly rather than silently.
                    if pub_date is None:
                        logger.warning(
                            f"Skipping entry with missing/unparseable date from "
                            f"{feed_url}: {entry.get('title', 'No Title')!r}"
                        )
                        continue

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
                    content_text = re.sub(r'<[^>]+>', '', content)
                    # Normalize whitespace
                    content_text = ' '.join(content_text.split())

                    title = entry.get('title', 'No Title')
                    url = entry.get('link', '')
                    author = entry.get('author', entry.get('dc_creator', 'Unknown'))
                    tags = [tag.term for tag in entry.get('tags', []) if getattr(tag, 'term', None)]

                    if is_trend_feed and not self._is_trend_ai_relevant(title, content_text, tags):
                        trend_filtered += 1
                        continue

                    # Handle author as list (some feeds)
                    if isinstance(author, list):
                        author = ', '.join(author)

                    post = CollectedItem(
                        id=self.generate_id(url, title),
                        title=title,
                        content=content_text,
                        url=url,
                        author=author,
                        published=pub_date.isoformat(),
                        source=feed_title,
                        source_type='research_blog',
                        tags=tags,
                        metadata={
                            'feed_url': feed_url,
                            'raw_summary': entry.get('summary', '')[:500]
                        },
                        keywords=self.extract_keywords(f"{title} {content_text}")
                    )

                    posts.append(post)
                    if is_trend_feed:
                        trend_retained += 1

                except Exception as e:
                    logger.error(f"Error processing entry from {feed_url}: {e}")

            if is_trend_feed:
                logger.info(
                    "Trend Micro AI/security filter retained %s posts and filtered %s posts",
                    trend_retained,
                    trend_filtered
                )
            logger.info(f"Collected {len(posts)} posts from {feed_title}")

        except Exception as e:
            logger.error(f"Error fetching research feed {feed_url}: {e}")

        return posts

    def _is_trend_research_feed(self, feed_url: str, feed_title: str = '') -> bool:
        """Identify Trend Micro's broad threat-research feed for source-specific filtering."""
        marker_text = f"{feed_url} {feed_title}".lower()
        return any(marker in marker_text for marker in self.TREND_RESEARCH_FEED_MARKERS)

    def _is_trend_ai_relevant(self, title: str, content: str, tags: List[str]) -> bool:
        """Keep Trend threat research only when it intersects AI/security topics."""
        text = f"{title} {content} {' '.join(tags)}".lower()
        return any(re.search(pattern, text) for pattern in self.TREND_AI_RELEVANCE_PATTERNS)

    def _fetch_lesswrong_graphql(self) -> List[CollectedItem]:
        """Fetch posts from LessWrong using GraphQL API for date-range queries.

        The RSS feed only contains the ~10-20 most recent posts, which scroll off
        within hours. The GraphQL API allows date-range queries to fetch historical
        posts that are no longer in the RSS feed.
        """
        posts = []

        # GraphQL query for posts within date range
        # LessWrong uses 'after' and 'before' as date strings (YYYY-MM-DD)
        query = '''
        query GetPosts($after: Date, $before: Date) {
          posts(input: {
            terms: {
              view: "new",
              after: $after,
              before: $before,
              limit: 100
            }
          }) {
            results {
              _id
              title
              slug
              postedAt
              contents {
                html
              }
              user {
                displayName
                username
              }
              baseScore
              voteCount
            }
          }
        }
        '''

        # Use coverage_date as 'after' and report_date as 'before' (exclusive)
        variables = {
            "after": self.coverage_date,
            "before": self.report_date
        }

        try:
            logger.info(f"Fetching LessWrong posts via GraphQL (coverage: {self.coverage_date})")
            data = self._execute_lesswrong_graphql(query, variables)

            if 'errors' in data:
                logger.error(f"LessWrong GraphQL errors: {data['errors']}")
                return posts

            results = data.get('data', {}).get('posts', {}).get('results', [])
            logger.info(f"LessWrong GraphQL returned {len(results)} posts")

            for post_data in results:
                try:
                    post_id = post_data.get('_id', '')
                    slug = post_data.get('slug', '')
                    title = post_data.get('title', 'No Title')

                    # Build URL: https://www.lesswrong.com/posts/{_id}/{slug}
                    url = f"https://www.lesswrong.com/posts/{post_id}/{slug}"

                    # Parse posted date
                    posted_at = post_data.get('postedAt', '')
                    if posted_at:
                        # Parse ISO format datetime
                        pub_date = datetime.fromisoformat(posted_at.replace('Z', '+00:00'))
                        # Convert to local time for consistency
                        pub_date = pub_date.astimezone().replace(tzinfo=None)
                    else:
                        pub_date = datetime.now()

                    # Extract content from HTML
                    content_html = ''
                    contents = post_data.get('contents')
                    if contents and isinstance(contents, dict):
                        content_html = contents.get('html', '')

                    # Strip HTML tags from content
                    content_text = re.sub(r'<[^>]+>', '', content_html)
                    content_text = ' '.join(content_text.split())
                    # Truncate for storage
                    if len(content_text) > 2000:
                        content_text = content_text[:2000] + '...'

                    # Extract author
                    user = post_data.get('user', {})
                    if user:
                        author = user.get('displayName', user.get('username', 'Unknown'))
                    else:
                        author = 'Unknown'

                    post = CollectedItem(
                        id=self.generate_id(url, title),
                        title=title,
                        content=content_text,
                        url=url,
                        author=author,
                        published=pub_date.isoformat(),
                        source='LessWrong',
                        source_type='research_blog',
                        tags=[],
                        metadata={
                            'lesswrong_id': post_id,
                            'slug': slug,
                            'base_score': post_data.get('baseScore', 0),
                            'vote_count': post_data.get('voteCount', 0)
                        },
                        keywords=self.extract_keywords(f"{title} {content_text}")
                    )

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing LessWrong post: {e}")

            logger.info(f"Collected {len(posts)} posts from LessWrong (GraphQL)")

        except requests.exceptions.RequestException as e:
            logger.error(f"LessWrong GraphQL request failed: {e}")
        except Exception as e:
            logger.error(f"Error fetching LessWrong GraphQL: {e}")

        return posts

    def _execute_lesswrong_graphql(self, query: str, variables: dict) -> dict:
        """Prefer the cookie-aware client, but keep direct GraphQL as a fallback."""
        if LessWrongClient is not None:
            try:
                logger.info("Using LessWrongClient cookie bypass for LessWrong GraphQL")
                return LessWrongClient().graphql(query, variables)
            except Exception as e:
                logger.warning(
                    "LessWrongClient failed (%s); falling back to direct GraphQL request",
                    e,
                )
        else:
            logger.warning("LessWrongClient unavailable; falling back to direct GraphQL request")

        response = requests.post(
            'https://www.lesswrong.com/graphql',
            json={'query': query, 'variables': variables},
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Origin': 'https://www.lesswrong.com',
                'Referer': 'https://www.lesswrong.com/',
                'User-Agent': os.environ.get('NEWS_USER_AGENT') or LESSWRONG_USER_AGENT,
            },
            timeout=45
        )
        response.raise_for_status()
        return response.json()

    def _parse_feed_date(self, date_struct) -> Optional[datetime]:
        """Parse date from feedparser date structure.

        feedparser returns time.struct_time in UTC. We convert to local time
        for comparison with our local-time coverage window.

        Returns None when the date is missing or unparseable, so the caller can
        log and skip the entry explicitly. (A fallback to datetime.now() would
        land on the report day, outside the coverage window — the day before —
        and silently drop the entry with no signal.)
        """
        if not date_struct:
            return None

        try:
            # feedparser returns time.struct_time in UTC
            if hasattr(date_struct, 'tm_year'):
                from datetime import timezone
                # Create UTC datetime
                utc_dt = datetime(*date_struct[:6], tzinfo=timezone.utc)
                # Convert to local time (naive datetime for comparison)
                local_dt = utc_dt.astimezone().replace(tzinfo=None)
                return local_dt
        except Exception as e:
            logger.warning(f"Failed to parse feed date: {e}")

        return None

    def _fetch_category_rss(self, category: str) -> List[CollectedItem]:
        """Fetch papers from arXiv RSS feed (primary method - no rate limits)."""
        papers = []
        url = f"{self.RSS_BASE}/{category}"

        try:
            logger.info(f"Fetching RSS feed for {category}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"RSS request failed for {category}: {e}")
            raise  # Let gather() handle fallback

        try:
            feed = feedparser.parse(response.content)
            category_name = self.CATEGORIES.get(category, category)

            for entry in feed.entries:
                try:
                    # Filter: only new papers and cross-listings, not replacements
                    announce_type = getattr(entry, 'arxiv_announce_type', 'new')
                    if announce_type not in ['new', 'cross']:
                        continue

                    # Extract arXiv ID from guid or id
                    entry_id = entry.get('id', entry.get('guid', ''))
                    arxiv_id = self._parse_arxiv_id(entry_id)

                    # Parse publication date (RFC 2822 format from RSS)
                    pub_date = self._parse_rss_date(entry.get('published', ''))

                    # Extract authors from dc:creator
                    authors = []
                    dc_creator = entry.get('dc_creator', entry.get('author', ''))
                    if dc_creator:
                        # dc:creator can be comma-separated or a single author
                        if isinstance(dc_creator, list):
                            authors = dc_creator
                        else:
                            authors = [a.strip() for a in dc_creator.split(',')]

                    # Extract abstract from description
                    abstract = entry.get('description', entry.get('summary', ''))
                    # Clean up HTML and normalize whitespace
                    abstract = abstract.replace('<p>', '').replace('</p>', ' ')
                    abstract = abstract.replace('\n', ' ').strip()

                    # Get primary category from tags
                    primary_category = category
                    if hasattr(entry, 'tags') and entry.tags:
                        primary_category = entry.tags[0].get('term', category)

                    # Build arXiv URL
                    arxiv_url = f"http://arxiv.org/abs/{arxiv_id}"

                    paper = CollectedItem(
                        id=self.generate_id(arxiv_id),
                        title=entry.get('title', 'No Title').replace('\n', ' ').strip(),
                        content=abstract,
                        url=arxiv_url,
                        author=', '.join(authors),
                        published=pub_date.isoformat(),
                        source=f'arXiv ({category_name})',
                        source_type='arxiv',
                        tags=[primary_category],
                        metadata={
                            'arxiv_id': arxiv_id,
                            'pdf_url': f"http://arxiv.org/pdf/{arxiv_id}.pdf",
                            'category': primary_category,
                            'category_name': category_name,
                            'authors': authors,
                            'announce_type': announce_type
                        },
                        keywords=self.extract_keywords(f"{entry.get('title', '')} {abstract}")
                    )

                    papers.append(paper)

                except Exception as e:
                    logger.error(f"Error processing RSS entry from {category}: {e}")

            logger.info(f"Collected {len(papers)} papers from {category} via RSS")

        except Exception as e:
            logger.error(f"Error parsing RSS feed for {category}: {e}")
            raise  # Let gather() handle fallback

        return papers

    def _fetch_category_api(self, category: str, max_retries: int = 3) -> List[CollectedItem]:
        """Fetch papers from arXiv API (fallback method - has rate limits)."""
        papers = []

        # Format dates for arXiv API (YYYYMMDDHHMM)
        start_str = self.start_time.strftime('%Y%m%d%H%M')
        end_str = self.end_time.strftime('%Y%m%d%H%M')

        search_query = f"cat:{category} AND submittedDate:[{start_str} TO {end_str}]"

        params = {
            'search_query': search_query,
            'start': 0,
            'max_results': 500,
            'sortBy': 'submittedDate',
            'sortOrder': 'descending'
        }

        # Retry with exponential backoff
        for attempt in range(max_retries):
            try:
                logger.info(f"Fetching arXiv category: {category}" + (f" (attempt {attempt + 1})" if attempt > 0 else ""))
                response = requests.get(self.API_BASE, params=params, timeout=60)
                response.raise_for_status()
                break  # Success, exit retry loop
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    # Wait before retry: 5s, 15s, 45s (exponential backoff)
                    wait_time = 5 * (3 ** attempt)
                    logger.warning(f"arXiv request failed for {category}: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Error fetching arXiv category {category} after {max_retries} attempts: {e}")
                    return papers
        else:
            # All retries exhausted
            return papers

        try:
            feed = feedparser.parse(response.content)
            category_name = self.CATEGORIES.get(category, category)

            for entry in feed.entries:
                try:
                    # Parse publication date
                    pub_date = self._parse_date(entry.get('published', ''))

                    # Extract arXiv ID from entry ID
                    entry_id = entry.get('id', '')
                    arxiv_id = self._parse_arxiv_id(entry_id)

                    # Extract authors
                    authors = []
                    if hasattr(entry, 'authors'):
                        authors = [a.get('name', '') for a in entry.authors]
                    elif hasattr(entry, 'author'):
                        authors = [entry.author]

                    # Extract abstract
                    abstract = entry.get('summary', '').replace('\n', ' ').strip()

                    # Get primary category
                    primary_category = category
                    if hasattr(entry, 'arxiv_primary_category'):
                        primary_category = entry.arxiv_primary_category.get('term', category)

                    paper = CollectedItem(
                        id=self.generate_id(arxiv_id),
                        title=entry.get('title', 'No Title').replace('\n', ' ').strip(),
                        content=abstract,
                        url=entry_id,
                        author=', '.join(authors),
                        published=pub_date.isoformat(),
                        source=f'arXiv ({category_name})',
                        source_type='arxiv',
                        tags=[primary_category],
                        metadata={
                            'arxiv_id': arxiv_id,
                            'pdf_url': entry_id.replace('/abs/', '/pdf/') + '.pdf',
                            'category': primary_category,
                            'category_name': category_name,
                            'authors': authors
                        },
                        keywords=self.extract_keywords(f"{entry.get('title', '')} {abstract}")
                    )

                    papers.append(paper)

                except Exception as e:
                    logger.error(f"Error processing paper from {category}: {e}")

            logger.info(f"Collected {len(papers)} papers from {category}")

        except Exception as e:
            logger.error(f"Error parsing arXiv response for {category}: {e}")

        return papers

    def _parse_arxiv_id(self, link: str) -> str:
        """Extract arXiv ID from link or OAI identifier.

        Handles both formats:
        - API/URL format: http://arxiv.org/abs/2601.02514v1 -> 2601.02514
        - RSS OAI format: oai:arXiv.org:2601.02514v1 -> 2601.02514
        """
        try:
            if '/abs/' in link:
                # API format: http://arxiv.org/abs/2601.02514v1
                arxiv_id = link.split('/abs/')[-1]
            elif 'arXiv.org:' in link:
                # RSS OAI format: oai:arXiv.org:2601.02514v1
                arxiv_id = link.split('arXiv.org:')[-1]
            else:
                arxiv_id = link
            # Remove version suffix (v1, v2, etc.)
            if 'v' in arxiv_id and arxiv_id.split('v')[-1].isdigit():
                arxiv_id = arxiv_id.rsplit('v', 1)[0]
            return arxiv_id
        except:
            return link

    def _parse_date(self, date_str: str) -> datetime:
        """Parse date string from arXiv API."""
        try:
            if date_str:
                clean_date = date_str.replace('Z', '').replace('T', ' ')
                if '.' in clean_date:
                    return datetime.strptime(clean_date.split('.')[0], '%Y-%m-%d %H:%M:%S')
                return datetime.strptime(clean_date, '%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")
        return datetime.now()

    def _parse_rss_date(self, date_str: str) -> datetime:
        """Parse RFC 2822 date string from RSS feed."""
        try:
            if date_str:
                return parsedate_to_datetime(date_str)
        except Exception as e:
            logger.warning(f"Failed to parse RSS date '{date_str}': {e}")
        return datetime.now()
