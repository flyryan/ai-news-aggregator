"""
Social Gatherer - Collects posts from Twitter, Bluesky, and Mastodon.

Twitter uses TwitterAPI.io (paid).
Bluesky and Mastodon use their free public APIs.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..base import BaseGatherer, CollectedItem

logger = logging.getLogger(__name__)

# TwitterAPI.io configuration
TWITTERAPI_IO_KEY = os.getenv('TWITTERAPI_IO_KEY', '')
TWITTERAPI_IO_BASE = "https://api.twitterapi.io"


class SocialGatherer(BaseGatherer):
    """Gathers posts from Twitter, Bluesky, and Mastodon."""

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None
    ):
        super().__init__(config_dir, data_dir, lookback_hours, target_date)

        # Load configured accounts
        self.twitter_users = self.load_config_list('twitter_accounts.txt')
        self.bluesky_handles = self.load_config_list('bluesky_accounts.txt')
        self.mastodon_accounts = self.load_config_list('mastodon_accounts.txt')

        # Track collection status per platform
        self.collection_status: Dict[str, Dict[str, Any]] = {
            'twitter': {'status': 'pending', 'count': 0, 'error': None},
            'bluesky': {'status': 'pending', 'count': 0, 'error': None},
            'mastodon': {'status': 'pending', 'count': 0, 'error': None},
        }

        logger.info(f"Loaded {len(self.twitter_users)} Twitter, {len(self.bluesky_handles)} Bluesky, {len(self.mastodon_accounts)} Mastodon accounts")

    @property
    def category(self) -> str:
        return 'social'

    async def gather(self) -> List[CollectedItem]:
        """Gather posts from all social platforms."""
        logger.info("Starting social media collection")

        all_items = []
        loop = asyncio.get_event_loop()

        # Mark skipped platforms
        if not self.twitter_users or not TWITTERAPI_IO_KEY:
            self.collection_status['twitter']['status'] = 'skipped'
            self.collection_status['twitter']['error'] = 'No API key' if not TWITTERAPI_IO_KEY else 'No accounts configured'
        if not self.bluesky_handles:
            self.collection_status['bluesky']['status'] = 'skipped'
            self.collection_status['bluesky']['error'] = 'No accounts configured'
        if not self.mastodon_accounts:
            self.collection_status['mastodon']['status'] = 'skipped'
            self.collection_status['mastodon']['error'] = 'No accounts configured'

        # Collect from all platforms in parallel
        with ThreadPoolExecutor(max_workers=6) as executor:
            tasks = []

            # Twitter collection
            if self.twitter_users and TWITTERAPI_IO_KEY:
                tasks.append(loop.run_in_executor(executor, self._collect_twitter))

            # Bluesky collection
            if self.bluesky_handles:
                tasks.append(loop.run_in_executor(executor, self._collect_bluesky))

            # Mastodon collection
            if self.mastodon_accounts:
                tasks.append(loop.run_in_executor(executor, self._collect_mastodon))

            if tasks:
                results = await asyncio.gather(*tasks)
                for result in results:
                    all_items.extend(result)

        logger.info(f"Collected {len(all_items)} total social posts")

        # Log collection status summary
        self._log_collection_summary()

        # Save to file
        self.save_to_file(all_items, f'social_{self.target_date}.json')

        return all_items

    def _log_collection_summary(self):
        """Log a summary of collection status for all platforms."""
        logger.info("Social collection summary:")
        for platform, status in self.collection_status.items():
            if status['status'] == 'success':
                logger.info(f"  ✓ {platform.capitalize()}: {status['count']} posts")
            elif status['status'] == 'partial':
                logger.warning(f"  ⚠ {platform.capitalize()}: {status['count']} posts (partial - {status['error']})")
            elif status['status'] == 'failed':
                logger.error(f"  ✗ {platform.capitalize()}: FAILED - {status['error']}")
            elif status['status'] == 'skipped':
                logger.info(f"  - {platform.capitalize()}: skipped ({status['error']})")
            else:
                logger.warning(f"  ? {platform.capitalize()}: unknown status")

    def get_collection_status(self) -> Dict[str, Dict[str, Any]]:
        """Get collection status for all platforms."""
        return self.collection_status

    # ========== TWITTER COLLECTION ==========

    def _collect_twitter(self) -> List[CollectedItem]:
        """Collect tweets from configured users via TwitterAPI.io."""
        if not TWITTERAPI_IO_KEY:
            logger.warning("TwitterAPI.io key not configured - skipping Twitter")
            self.collection_status['twitter']['status'] = 'skipped'
            self.collection_status['twitter']['error'] = 'No API key'
            return []

        all_tweets = []

        try:
            # Use search endpoint for efficiency
            tweets = self._twitter_search(self.twitter_users)
            all_tweets.extend(tweets)

            if all_tweets:
                self.collection_status['twitter']['status'] = 'success'
                self.collection_status['twitter']['count'] = len(all_tweets)
            else:
                # Got 0 tweets - might be an API issue or just no recent tweets
                self.collection_status['twitter']['status'] = 'success'
                self.collection_status['twitter']['count'] = 0

            logger.info(f"Collected {len(all_tweets)} tweets from Twitter")
        except Exception as e:
            self.collection_status['twitter']['status'] = 'failed'
            self.collection_status['twitter']['error'] = str(e)
            logger.error(f"Twitter collection failed: {e}")

        return all_tweets

    def _twitter_search(self, usernames: List[str]) -> List[CollectedItem]:
        """Use Twitter advanced search to collect from multiple users."""
        all_tweets = []
        failed_chunks = 0

        if not usernames:
            return []

        # Build search query chunks (max ~25 users per query)
        max_users = 25
        chunks = [usernames[i:i + max_users] for i in range(0, len(usernames), max_users)]

        # Format dates for search
        since_date = self.start_time.strftime('%Y-%m-%d')
        until_date = (self.end_time + timedelta(days=1)).strftime('%Y-%m-%d')

        headers = {
            "X-API-Key": TWITTERAPI_IO_KEY,
            "Content-Type": "application/json"
        }

        for chunk_idx, chunk in enumerate(chunks):
            from_clauses = [f"from:{u}" for u in chunk]
            query = f"({' OR '.join(from_clauses)}) since:{since_date} until:{until_date}"
            logger.info(f"Twitter search query (chunk {chunk_idx + 1}/{len(chunks)})")
            chunk_error = None

            cursor = ""
            page = 0
            max_pages = 10

            while page < max_pages:
                try:
                    params = {"query": query, "queryType": "Latest"}
                    if cursor:
                        params["cursor"] = cursor

                    response = requests.get(
                        f"{TWITTERAPI_IO_BASE}/twitter/tweet/advanced_search",
                        params=params,
                        headers=headers,
                        timeout=30
                    )
                    response.raise_for_status()
                    data = response.json()

                    tweets_data = data.get('tweets', [])
                    if not tweets_data:
                        tweets_data = data.get('data', {}).get('tweets', [])

                    if not tweets_data:
                        break

                    for tweet_data in tweets_data:
                        try:
                            item = self._parse_twitter_tweet(tweet_data)
                            if item and self.is_in_date_range(datetime.fromisoformat(item.published)):
                                all_tweets.append(item)
                        except Exception as e:
                            logger.error(f"Error parsing tweet: {e}")

                    # Pagination
                    if not data.get('has_next_page', False) or not data.get('next_cursor', ''):
                        break

                    cursor = data['next_cursor']
                    page += 1
                    time.sleep(0.3)

                except Exception as e:
                    logger.error(f"Error in Twitter search: {e}")
                    chunk_error = str(e)
                    failed_chunks += 1
                    break

            if chunk_idx < len(chunks) - 1:
                time.sleep(0.5)

        # Track partial failures
        if failed_chunks > 0:
            if failed_chunks == len(chunks):
                self.collection_status['twitter']['status'] = 'failed'
                self.collection_status['twitter']['error'] = f"All {failed_chunks} API requests failed"
            else:
                self.collection_status['twitter']['status'] = 'partial'
                self.collection_status['twitter']['error'] = f"{failed_chunks}/{len(chunks)} API requests failed"

        return all_tweets

    def _parse_twitter_tweet(self, tweet_data: Dict[str, Any]) -> Optional[CollectedItem]:
        """Parse a tweet into CollectedItem."""
        tweet_id = tweet_data.get('id', '')
        text = tweet_data.get('text', '')
        created_at = tweet_data.get('createdAt', '')
        author = tweet_data.get('author', {})
        username = author.get('userName', author.get('username', 'unknown'))

        # Parse date
        try:
            pub_date = parsedate_to_datetime(created_at)
            if pub_date.tzinfo:
                pub_date = pub_date.replace(tzinfo=None)
        except:
            try:
                pub_date = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
            except:
                pub_date = datetime.now()

        return CollectedItem(
            id=self.generate_id('twitter', tweet_id),
            title=text[:100] + '...' if len(text) > 100 else text,
            content=text,
            url=f"https://twitter.com/{username}/status/{tweet_id}",
            author=f"@{username}",
            published=pub_date.isoformat(),
            source='Twitter',
            source_type='twitter',
            tags=[],
            metadata={
                'platform_id': tweet_id,
                'author_display_name': author.get('name', username),
                'engagement': {
                    'likes': tweet_data.get('likeCount', 0),
                    'retweets': tweet_data.get('retweetCount', 0),
                    'replies': tweet_data.get('replyCount', 0),
                    'quotes': tweet_data.get('quoteCount', 0),
                    'views': tweet_data.get('viewCount', 0)
                }
            },
            keywords=self.extract_keywords(text)
        )

    # ========== BLUESKY COLLECTION ==========

    def _collect_bluesky(self) -> List[CollectedItem]:
        """Collect posts from Bluesky handles."""
        all_posts = []
        failed_handles = 0

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_handle = {
                executor.submit(self._fetch_bluesky_user, handle): handle
                for handle in self.bluesky_handles
            }

            for future in as_completed(future_to_handle):
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                except Exception as e:
                    failed_handles += 1
                    logger.error(f"Bluesky collection error: {e}")

        # Track status
        if failed_handles == len(self.bluesky_handles):
            self.collection_status['bluesky']['status'] = 'failed'
            self.collection_status['bluesky']['error'] = f"All {failed_handles} handles failed"
        elif failed_handles > 0:
            self.collection_status['bluesky']['status'] = 'partial'
            self.collection_status['bluesky']['error'] = f"{failed_handles}/{len(self.bluesky_handles)} handles failed"
            self.collection_status['bluesky']['count'] = len(all_posts)
        else:
            self.collection_status['bluesky']['status'] = 'success'
            self.collection_status['bluesky']['count'] = len(all_posts)

        logger.info(f"Collected {len(all_posts)} posts from Bluesky")
        return all_posts

    def _fetch_bluesky_user(self, handle: str) -> List[CollectedItem]:
        """Fetch posts from a Bluesky user."""
        posts = []
        base_url = "https://public.api.bsky.app/xrpc"

        try:
            if not handle.endswith('.bsky.social') and '.' not in handle:
                handle = f"{handle}.bsky.social"

            logger.info(f"Fetching Bluesky posts for @{handle}")

            response = requests.get(
                f"{base_url}/app.bsky.feed.getAuthorFeed",
                params={'actor': handle, 'limit': 30},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            for item in data.get('feed', []):
                try:
                    post_data = item.get('post', {})
                    record = post_data.get('record', {})
                    author = post_data.get('author', {})

                    # Parse date
                    created_at = record.get('createdAt', '')
                    try:
                        pub_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        pub_date = datetime.now()

                    if not self.is_in_date_range(pub_date):
                        continue

                    uri = post_data.get('uri', '')
                    rkey = uri.split('/')[-1] if uri else ''
                    author_handle = author.get('handle', handle)
                    web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}"
                    text = record.get('text', '')

                    post = CollectedItem(
                        id=self.generate_id('bluesky', uri),
                        title=text[:100] + '...' if len(text) > 100 else text,
                        content=text,
                        url=web_url,
                        author=f"@{author_handle}",
                        published=pub_date.isoformat(),
                        source='Bluesky',
                        source_type='bluesky',
                        tags=[],
                        metadata={
                            'platform_id': uri,
                            'author_display_name': author.get('displayName', author_handle),
                            'engagement': {
                                'likes': post_data.get('likeCount', 0),
                                'reposts': post_data.get('repostCount', 0),
                                'replies': post_data.get('replyCount', 0),
                                'quotes': post_data.get('quoteCount', 0)
                            }
                        },
                        keywords=self.extract_keywords(text)
                    )

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing Bluesky post: {e}")

        except Exception as e:
            logger.error(f"Error fetching Bluesky @{handle}: {e}")

        return posts

    # ========== MASTODON COLLECTION ==========

    def _collect_mastodon(self) -> List[CollectedItem]:
        """Collect posts from Mastodon accounts."""
        all_posts = []
        failed_accounts = 0

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_account = {
                executor.submit(self._fetch_mastodon_user, account): account
                for account in self.mastodon_accounts
            }

            for future in as_completed(future_to_account):
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                except Exception as e:
                    failed_accounts += 1
                    logger.error(f"Mastodon collection error: {e}")

        # Track status
        if failed_accounts == len(self.mastodon_accounts):
            self.collection_status['mastodon']['status'] = 'failed'
            self.collection_status['mastodon']['error'] = f"All {failed_accounts} accounts failed"
        elif failed_accounts > 0:
            self.collection_status['mastodon']['status'] = 'partial'
            self.collection_status['mastodon']['error'] = f"{failed_accounts}/{len(self.mastodon_accounts)} accounts failed"
            self.collection_status['mastodon']['count'] = len(all_posts)
        else:
            self.collection_status['mastodon']['status'] = 'success'
            self.collection_status['mastodon']['count'] = len(all_posts)

        logger.info(f"Collected {len(all_posts)} posts from Mastodon")
        return all_posts

    def _fetch_mastodon_user(self, account_spec: str) -> List[CollectedItem]:
        """Fetch posts from a Mastodon user."""
        posts = []

        try:
            # Parse account spec (username@instance)
            if '@' not in account_spec:
                logger.warning(f"Invalid Mastodon format: {account_spec}")
                return []

            parts = account_spec.split('@')
            if len(parts) == 2:
                username, instance = parts
            elif len(parts) == 3 and parts[0] == '':
                username, instance = parts[1], parts[2]
            else:
                logger.warning(f"Invalid Mastodon format: {account_spec}")
                return []

            logger.info(f"Fetching Mastodon posts for @{username}@{instance}")

            # Look up account ID
            lookup_url = f"https://{instance}/api/v1/accounts/lookup"
            response = requests.get(lookup_url, params={'acct': username}, timeout=30)
            response.raise_for_status()
            account_data = response.json()
            account_id = account_data.get('id')

            if not account_id:
                return []

            # Get statuses
            statuses_url = f"https://{instance}/api/v1/accounts/{account_id}/statuses"
            response = requests.get(
                statuses_url,
                params={'limit': 40, 'exclude_replies': False, 'exclude_reblogs': True},
                timeout=30
            )
            response.raise_for_status()
            statuses = response.json()

            for status in statuses:
                try:
                    created_at = status.get('created_at', '')
                    try:
                        pub_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        pub_date = datetime.now()

                    if not self.is_in_date_range(pub_date):
                        continue

                    # Strip HTML from content
                    content_html = status.get('content', '')
                    content_text = re.sub(r'<[^>]+>', '', content_html)
                    content_text = content_text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

                    account = status.get('account', {})

                    post = CollectedItem(
                        id=self.generate_id('mastodon', status.get('id', '')),
                        title=content_text[:100] + '...' if len(content_text) > 100 else content_text,
                        content=content_text,
                        url=status.get('url', ''),
                        author=f"@{account.get('username', '')}@{instance}",
                        published=pub_date.isoformat(),
                        source=f"Mastodon ({instance})",
                        source_type='mastodon',
                        tags=[],
                        metadata={
                            'platform_id': status.get('id', ''),
                            'instance': instance,
                            'author_display_name': account.get('display_name', account.get('username', '')),
                            'engagement': {
                                'favourites': status.get('favourites_count', 0),
                                'reblogs': status.get('reblogs_count', 0),
                                'replies': status.get('replies_count', 0)
                            }
                        },
                        keywords=self.extract_keywords(content_text)
                    )

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing Mastodon status: {e}")

        except Exception as e:
            logger.error(f"Error fetching Mastodon @{account_spec}: {e}")

        return posts

    def get_urls_from_posts(self) -> List[Dict[str, Any]]:
        """
        Extract URLs from collected posts for link following.

        Returns list of dicts with url, post_url, platform, author.
        """
        # This would be called after gather() to get URLs for the link follower
        # Implementation depends on when this is called in the pipeline
        pass
