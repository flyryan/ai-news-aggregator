"""
Link Follower - Smart extraction of articles from social media links.

Uses LLM to decide which links are worth following, then fetches and
extracts article content.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

from ..base import CollectedItem
from ..llm_client import AnthropicClient, ThinkingLevel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.prompts import PromptAccessor

logger = logging.getLogger(__name__)

# Domains to skip (social media, images, videos, etc.)
SKIP_DOMAINS = {
    'twitter.com', 'x.com', 't.co',
    'reddit.com', 'redd.it',
    'youtube.com', 'youtu.be',
    'instagram.com',
    'facebook.com', 'fb.me',
    'tiktok.com',
    'linkedin.com',
    'discord.com', 'discord.gg',
    'twitch.tv',
    'imgur.com', 'i.imgur.com',
    'gfycat.com',
    'giphy.com',
    'tenor.com',
    'bsky.app',
    'mastodon.social',
}

# File extensions to skip
SKIP_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.mp4', '.webm', '.mov', '.avi',
    '.mp3', '.wav', '.ogg',
    '.pdf', '.zip', '.tar', '.gz',
}


class LinkFollower:
    """
    Smart link follower that extracts articles from social media posts.

    Uses LLM to decide which links are relevant, then fetches article content.
    """

    def __init__(
        self,
        llm_client: Optional[AnthropicClient] = None,
        max_concurrent: int = 5,
        timeout: float = 30.0,
        prompt_accessor: Optional['PromptAccessor'] = None
    ):
        """
        Initialize link follower.

        Args:
            llm_client: Anthropic client for LLM decisions.
            max_concurrent: Max concurrent article fetches.
            timeout: HTTP request timeout.
            prompt_accessor: Optional PromptAccessor for config-based prompts.
        """
        self.llm_client = llm_client
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.prompt_accessor = prompt_accessor
        self.seen_urls: Set[str] = set()

    def extract_urls(self, text: str) -> List[str]:
        """
        Extract URLs from text content.

        Args:
            text: Text containing URLs.

        Returns:
            List of extracted URLs.
        """
        # URL pattern
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'

        urls = re.findall(url_pattern, text)

        # Clean and filter URLs
        cleaned_urls = []
        for url in urls:
            # Remove trailing punctuation
            url = url.rstrip('.,;:!?)\'"]')

            # Skip if already seen
            normalized = self._normalize_url(url)
            if normalized in self.seen_urls:
                continue

            # Skip excluded domains
            if self._should_skip_url(url):
                continue

            cleaned_urls.append(url)
            self.seen_urls.add(normalized)

        return cleaned_urls

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        try:
            parsed = urlparse(url)
            normalized = f"{parsed.netloc}{parsed.path}".lower().rstrip('/')
            return normalized
        except:
            return url.lower()

    def _should_skip_url(self, url: str) -> bool:
        """Check if URL should be skipped."""
        try:
            parsed = urlparse(url)

            # Check domain
            domain = parsed.netloc.lower()
            for skip_domain in SKIP_DOMAINS:
                if skip_domain in domain:
                    return True

            # Check extension
            path = parsed.path.lower()
            for ext in SKIP_EXTENSIONS:
                if path.endswith(ext):
                    return True

            return False

        except:
            return True

    async def should_follow_link(
        self,
        url: str,
        post_context: str
    ) -> bool:
        """
        Use LLM to decide if a link is worth following.

        Args:
            url: URL to evaluate.
            post_context: Context from the social post containing the link.

        Returns:
            True if link should be followed.
        """
        if not self.llm_client:
            # Without LLM, follow all non-excluded links
            return True

        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_gathering_prompt(
                'link_relevance',
                {'url': url, 'post_context': post_context[:500]}
            )
        else:
            # Fallback to inline prompt for backwards compatibility
            prompt = f"""Evaluate if this URL is likely to contain a relevant AI/ML news article or blog post.

URL: {url}
Post context: {post_context[:500]}

Consider:
- Is this likely to be a news article, blog post, or announcement about AI/ML?
- Is this from a reputable source (tech news, company blog, research org)?
- Avoid: social media, videos, images, shopping, personal blogs about unrelated topics

Reply with just "YES" or "NO"."""

        try:
            response = self.llm_client.call(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0
            )

            answer = response.content.strip().upper()
            return answer.startswith('YES')

        except Exception as e:
            logger.error(f"Error in link evaluation: {e}")
            return True  # Default to following on error

    async def fetch_article(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch and extract article content from URL.

        Args:
            url: URL to fetch.

        Returns:
            Dict with article data or None if failed.
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }

            response = requests.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Extract title
            title = None
            if soup.title:
                title = soup.title.string
            if not title:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    title = og_title.get('content')
            if not title:
                h1 = soup.find('h1')
                if h1:
                    title = h1.get_text()

            # Extract publication date
            pub_date = None
            date_meta = (
                soup.find('meta', property='article:published_time') or
                soup.find('meta', attrs={'name': 'pubdate'}) or
                soup.find('meta', attrs={'name': 'date'}) or
                soup.find('time', attrs={'datetime': True})
            )
            if date_meta:
                date_str = date_meta.get('content') or date_meta.get('datetime')
                if date_str:
                    try:
                        pub_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        pass

            # Extract author
            author = None
            author_meta = (
                soup.find('meta', attrs={'name': 'author'}) or
                soup.find('meta', property='article:author')
            )
            if author_meta:
                author = author_meta.get('content')

            # Extract content
            content = self._extract_content(soup)

            # Extract source/site name
            source = None
            og_site = soup.find('meta', property='og:site_name')
            if og_site:
                source = og_site.get('content')
            if not source:
                source = urlparse(url).netloc

            return {
                'url': response.url,  # Final URL after redirects
                'title': title or 'No Title',
                'content': content,
                'author': author,
                'published': pub_date.isoformat() if pub_date else None,
                'source': source
            }

        except Exception as e:
            logger.error(f"Error fetching article {url}: {e}")
            return None

    def _extract_content(self, soup: BeautifulSoup) -> str:
        """Extract main article content from soup."""
        # Remove script, style, nav, footer elements
        for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            element.decompose()

        # Try to find article content
        content_candidates = [
            soup.find('article'),
            soup.find(class_=re.compile(r'article|content|post|entry', re.I)),
            soup.find('main'),
            soup.find(id=re.compile(r'article|content|post|entry', re.I)),
        ]

        for candidate in content_candidates:
            if candidate:
                text = candidate.get_text(separator=' ', strip=True)
                if len(text) > 200:
                    return text[:5000]  # Limit content length

        # Fallback to body text
        body = soup.find('body')
        if body:
            text = body.get_text(separator=' ', strip=True)
            return text[:5000]

        return ""

    async def process_social_posts(
        self,
        posts: List[CollectedItem],
        start_time: datetime,
        end_time: datetime
    ) -> List[CollectedItem]:
        """
        Process social posts to extract linked articles.

        Args:
            posts: List of social media posts.
            start_time: Start of date range.
            end_time: End of date range.

        Returns:
            List of CollectedItem for discovered articles.
        """
        articles = []
        url_to_post = {}  # Track which post each URL came from

        # Extract URLs from all posts
        for post in posts:
            urls = self.extract_urls(post.content)
            for url in urls:
                url_to_post[url] = post

        logger.info(f"Found {len(url_to_post)} unique URLs in {len(posts)} social posts")

        # Evaluate and fetch URLs
        urls_to_fetch = []

        for url, post in url_to_post.items():
            # Use LLM to decide if worth following
            should_follow = await self.should_follow_link(url, post.content)
            if should_follow:
                urls_to_fetch.append((url, post))

        logger.info(f"Will fetch {len(urls_to_fetch)} URLs after filtering")

        # Fetch articles in parallel
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            tasks = [
                loop.run_in_executor(
                    executor,
                    lambda u: asyncio.run(self.fetch_article(u)),
                    url
                )
                for url, _ in urls_to_fetch
            ]

            # Use run_in_executor with synchronous wrapper
            async def fetch_sync(url):
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                    }
                    response = requests.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Extract data (simplified)
                    title = soup.title.string if soup.title else 'No Title'
                    content = self._extract_content(soup)
                    source = urlparse(url).netloc

                    return {
                        'url': response.url,
                        'title': title,
                        'content': content,
                        'source': source
                    }
                except:
                    return None

            results = await asyncio.gather(*[fetch_sync(url) for url, _ in urls_to_fetch])

        # Process results
        for (url, post), article_data in zip(urls_to_fetch, results):
            if not article_data:
                continue

            # Validate date if available
            if article_data.get('published'):
                try:
                    pub_date = datetime.fromisoformat(article_data['published'])
                    if pub_date < start_time or pub_date > end_time:
                        continue
                except:
                    pass

            # Generate ID from URL (12 chars = ~280 trillion values)
            import hashlib
            url_hash = hashlib.sha256(article_data['url'].encode()).hexdigest()[:12]

            article = CollectedItem(
                id=url_hash,
                title=article_data.get('title', 'No Title'),
                content=article_data.get('content', ''),
                url=article_data['url'],
                author=article_data.get('author', ''),
                published=article_data.get('published', datetime.now().isoformat()),
                source=article_data.get('source', ''),
                source_type='linked_article',
                tags=[],
                metadata={
                    'discovered_via': {
                        'platform': post.source_type,
                        'post_url': post.url,
                        'post_author': post.author
                    }
                }
            )

            articles.append(article)

        logger.info(f"Extracted {len(articles)} articles from social links")
        return articles
