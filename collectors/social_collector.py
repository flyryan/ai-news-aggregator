#!/usr/bin/env python3
"""
Social Media Collector
Collects content from Twitter, Reddit, Bluesky, and Mastodon.
Twitter/Reddit use Manus Data API (when available).
Bluesky/Mastodon use their public APIs.
"""

import sys
import json
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import requests

# Add Manus API client path
sys.path.append('/opt/.manus/.sandbox-runtime')
try:
    from data_api import ApiClient
    MANUS_API_AVAILABLE = True
except ImportError:
    MANUS_API_AVAILABLE = False
    logging.warning("Manus Data API not available - Twitter/Reddit collection disabled")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TwitterAPI.io configuration
TWITTERAPI_IO_KEY = os.getenv('TWITTERAPI_IO_KEY', '')
TWITTERAPI_IO_BASE = "https://api.twitterapi.io"

# Reddit JSON endpoint (free, no API key needed)
REDDIT_USER_AGENT = "AI-News-Aggregator/1.0"


class SocialMediaCollector:
    """Collects content from social media platforms."""

    def __init__(self, lookback_hours: int = 24, target_date: str = None):
        """
        Initialize social media collector.

        Args:
            lookback_hours: Only collect content from the last N hours (used if target_date not set)
            target_date: Specific date to collect (format: YYYY-MM-DD). Collects 00:00Z-23:59Z for that date.
        """
        self.lookback_hours = lookback_hours
        self.target_date = target_date

        if target_date:
            # Parse target date and set start/end times
            date_obj = datetime.strptime(target_date, '%Y-%m-%d')
            self.start_time = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            self.end_time = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
            self.cutoff_time = self.start_time
            logger.info(f"Collecting content for {target_date} (00:00Z - 23:59Z)")
        else:
            self.cutoff_time = datetime.now() - timedelta(hours=lookback_hours)
            self.start_time = self.cutoff_time
            self.end_time = datetime.now()
        
        if MANUS_API_AVAILABLE:
            self.api_client = ApiClient()
        else:
            self.api_client = None
    
    def _generate_content_hash(self, platform: str, content_id: str) -> str:
        """Generate a unique hash for content deduplication."""
        content = f"{platform}:{content_id}".encode('utf-8')
        return hashlib.sha256(content).hexdigest()
    
    def _collect_twitter_user(self, username: str) -> List[Dict[str, Any]]:
        """Collect recent tweets from a Twitter user."""
        if not self.api_client:
            return []
        
        tweets = []
        
        try:
            # First, get user profile to get user ID
            logger.info(f"Fetching Twitter profile for @{username}")
            profile_response = self.api_client.call_api(
                'Twitter/get_user_profile_by_username',
                query={'username': username}
            )
            
            # Extract user ID from nested structure
            user_data = profile_response.get('result', {}).get('data', {}).get('user', {}).get('result', {})
            user_id = user_data.get('rest_id')
            
            if not user_id:
                logger.warning(f"Could not find user ID for @{username}")
                return []
            
            # Get user tweets
            logger.info(f"Fetching tweets for @{username} (ID: {user_id})")
            tweets_response = self.api_client.call_api(
                'Twitter/get_user_tweets',
                query={'user': str(user_id), 'count': '20'}
            )
            
            # Parse tweets from response
            if 'result' in tweets_response and 'timeline' in tweets_response['result']:
                timeline = tweets_response['result']['timeline']
                instructions = timeline.get('instructions', [])
                
                for instruction in instructions:
                    if instruction.get('type') == 'TimelineAddEntries':
                        entries = instruction.get('entries', [])
                        for entry in entries:
                            if entry.get('entryId', '').startswith('tweet-'):
                                content = entry.get('content', {})
                                if 'itemContent' in content:
                                    tweet_results = content['itemContent'].get('tweet_results', {})
                                    if 'result' in tweet_results:
                                        tweet_data = tweet_results['result']
                                        
                                        # Extract tweet information
                                        legacy = tweet_data.get('legacy', {})
                                        tweet_id = legacy.get('id_str', tweet_data.get('rest_id', ''))
                                        text = legacy.get('full_text', '')
                                        created_at = legacy.get('created_at', '')
                                        
                                        # Normalize tweet data
                                        tweet = {
                                            'id': self._generate_content_hash('twitter', tweet_id),
                                            'platform_id': tweet_id,
                                            'platform': 'twitter',
                                            'author': username,
                                            'content': text,
                                            'url': f"https://twitter.com/{username}/status/{tweet_id}",
                                            'published': created_at,
                                            'engagement': {
                                                'retweets': legacy.get('retweet_count', 0),
                                                'likes': legacy.get('favorite_count', 0),
                                                'replies': legacy.get('reply_count', 0),
                                                'quotes': legacy.get('quote_count', 0)
                                            },
                                            'source_type': 'twitter',
                                            'collected_at': datetime.now().isoformat()
                                        }
                                        
                                        tweets.append(tweet)
            
            logger.info(f"Collected {len(tweets)} tweets from @{username}")
            
        except Exception as e:
            logger.error(f"Error collecting tweets from @{username}: {e}")
        
        return tweets
    
    def _collect_reddit_subreddit(self, subreddit: str) -> List[Dict[str, Any]]:
        """Collect hot posts from a Reddit subreddit."""
        if not self.api_client:
            return []
        
        posts = []
        
        try:
            logger.info(f"Fetching posts from r/{subreddit}")
            response = self.api_client.call_api(
                'Reddit/AccessAPI',
                query={'subreddit': subreddit, 'limit': '50'}
            )
            
            post_list = response.get('posts', [])
            
            for post_wrapper in post_list:
                post_data = post_wrapper.get('data', {})
                
                # Normalize post data
                post = {
                    'id': self._generate_content_hash('reddit', post_data.get('id', '')),
                    'platform_id': post_data.get('id', ''),
                    'platform': 'reddit',
                    'subreddit': subreddit,
                    'title': post_data.get('title', ''),
                    'content': post_data.get('selftext', ''),
                    'author': post_data.get('author', ''),
                    'url': f"https://reddit.com{post_data.get('permalink', '')}",
                    'published': datetime.fromtimestamp(post_data.get('created_utc', 0)).isoformat(),
                    'engagement': {
                        'score': post_data.get('score', 0),
                        'upvote_ratio': post_data.get('upvote_ratio', 0),
                        'num_comments': post_data.get('num_comments', 0)
                    },
                    'source_type': 'reddit',
                    'collected_at': datetime.now().isoformat()
                }
                
                posts.append(post)
            
            logger.info(f"Collected {len(posts)} posts from r/{subreddit}")
            
        except Exception as e:
            logger.error(f"Error collecting posts from r/{subreddit}: {e}")
        
        return posts
    
    def collect_twitter(self, usernames: List[str]) -> List[Dict[str, Any]]:
        """Collect tweets from multiple Twitter users in parallel."""
        all_tweets = []
        
        logger.info(f"Collecting tweets from {len(usernames)} Twitter accounts")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_username = {
                executor.submit(self._collect_twitter_user, username): username 
                for username in usernames
            }
            
            for future in as_completed(future_to_username):
                username = future_to_username[future]
                try:
                    tweets = future.result()
                    all_tweets.extend(tweets)
                except Exception as e:
                    logger.error(f"Twitter user @{username} generated an exception: {e}")
        
        return all_tweets
    
    def collect_reddit(self, subreddits: List[str]) -> List[Dict[str, Any]]:
        """Collect posts from multiple Reddit subreddits in parallel."""
        all_posts = []

        logger.info(f"Collecting posts from {len(subreddits)} subreddits")

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_subreddit = {
                executor.submit(self._collect_reddit_subreddit, subreddit): subreddit
                for subreddit in subreddits
            }

            for future in as_completed(future_to_subreddit):
                subreddit = future_to_subreddit[future]
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                except Exception as e:
                    logger.error(f"Subreddit r/{subreddit} generated an exception: {e}")

        return all_posts

    # ========== BLUESKY COLLECTION ==========

    def _collect_bluesky_user(self, handle: str) -> List[Dict[str, Any]]:
        """
        Collect recent posts from a Bluesky user using the public API.

        Args:
            handle: Bluesky handle (e.g., 'user.bsky.social' or 'user.com')

        Returns:
            List of normalized post dictionaries
        """
        posts = []
        base_url = "https://public.api.bsky.app/xrpc"

        try:
            # Ensure handle has proper format
            if not handle.endswith('.bsky.social') and '.' not in handle:
                handle = f"{handle}.bsky.social"

            logger.info(f"Fetching Bluesky posts for @{handle}")

            # Get author feed
            response = requests.get(
                f"{base_url}/app.bsky.feed.getAuthorFeed",
                params={'actor': handle, 'limit': 30},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            feed = data.get('feed', [])

            for item in feed:
                try:
                    post_data = item.get('post', {})
                    record = post_data.get('record', {})
                    author = post_data.get('author', {})

                    # Parse created time
                    created_at = record.get('createdAt', '')
                    try:
                        pub_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        pub_date = datetime.now()

                    # Skip if outside date range
                    if pub_date < self.start_time or pub_date > self.end_time:
                        continue

                    # Extract post URI and create web URL
                    uri = post_data.get('uri', '')
                    # URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
                    rkey = uri.split('/')[-1] if uri else ''
                    author_handle = author.get('handle', handle)
                    web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}"

                    post = {
                        'id': self._generate_content_hash('bluesky', uri),
                        'platform_id': uri,
                        'platform': 'bluesky',
                        'author': author_handle,
                        'author_display_name': author.get('displayName', author_handle),
                        'content': record.get('text', ''),
                        'url': web_url,
                        'published': pub_date.isoformat(),
                        'engagement': {
                            'likes': post_data.get('likeCount', 0),
                            'reposts': post_data.get('repostCount', 0),
                            'replies': post_data.get('replyCount', 0),
                            'quotes': post_data.get('quoteCount', 0)
                        },
                        'source_type': 'bluesky',
                        'collected_at': datetime.now().isoformat()
                    }

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing Bluesky post from {handle}: {e}")
                    continue

            logger.info(f"Collected {len(posts)} posts from Bluesky @{handle}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Bluesky posts for @{handle}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error collecting Bluesky posts from @{handle}: {e}")

        return posts

    def collect_bluesky(self, handles: List[str]) -> List[Dict[str, Any]]:
        """Collect posts from multiple Bluesky users in parallel."""
        all_posts = []

        logger.info(f"Collecting posts from {len(handles)} Bluesky accounts")

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_handle = {
                executor.submit(self._collect_bluesky_user, handle): handle
                for handle in handles
            }

            for future in as_completed(future_to_handle):
                handle = future_to_handle[future]
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                except Exception as e:
                    logger.error(f"Bluesky user @{handle} generated an exception: {e}")

        return all_posts

    # ========== MASTODON COLLECTION ==========

    def _collect_mastodon_user(self, account_spec: str) -> List[Dict[str, Any]]:
        """
        Collect recent posts from a Mastodon user.

        Args:
            account_spec: Full account spec as 'username@instance.social'

        Returns:
            List of normalized post dictionaries
        """
        posts = []

        try:
            # Parse account spec
            if '@' not in account_spec:
                logger.warning(f"Invalid Mastodon account format: {account_spec}. Expected 'username@instance.social'")
                return []

            parts = account_spec.split('@')
            if len(parts) == 2:
                username, instance = parts
            elif len(parts) == 3 and parts[0] == '':
                # Handle @username@instance format
                username, instance = parts[1], parts[2]
            else:
                logger.warning(f"Invalid Mastodon account format: {account_spec}")
                return []

            logger.info(f"Fetching Mastodon posts for @{username}@{instance}")

            # Look up account ID
            lookup_url = f"https://{instance}/api/v1/accounts/lookup"
            response = requests.get(
                lookup_url,
                params={'acct': username},
                timeout=30
            )
            response.raise_for_status()
            account_data = response.json()
            account_id = account_data.get('id')

            if not account_id:
                logger.warning(f"Could not find Mastodon account: {account_spec}")
                return []

            # Get account statuses
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
                    # Parse created time
                    created_at = status.get('created_at', '')
                    try:
                        pub_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        pub_date = datetime.now()

                    # Skip if outside date range
                    if pub_date < self.start_time or pub_date > self.end_time:
                        continue

                    # Extract plain text content (strip HTML)
                    content_html = status.get('content', '')
                    # Simple HTML stripping - remove tags
                    import re
                    content_text = re.sub(r'<[^>]+>', '', content_html)
                    content_text = content_text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

                    account = status.get('account', {})

                    post = {
                        'id': self._generate_content_hash('mastodon', status.get('id', '')),
                        'platform_id': status.get('id', ''),
                        'platform': 'mastodon',
                        'instance': instance,
                        'author': f"{account.get('username', '')}@{instance}",
                        'author_display_name': account.get('display_name', account.get('username', '')),
                        'content': content_text,
                        'url': status.get('url', ''),
                        'published': pub_date.isoformat(),
                        'engagement': {
                            'favourites': status.get('favourites_count', 0),
                            'reblogs': status.get('reblogs_count', 0),
                            'replies': status.get('replies_count', 0)
                        },
                        'source_type': 'mastodon',
                        'collected_at': datetime.now().isoformat()
                    }

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing Mastodon status from {account_spec}: {e}")
                    continue

            logger.info(f"Collected {len(posts)} posts from Mastodon @{account_spec}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Mastodon posts for @{account_spec}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error collecting Mastodon posts from @{account_spec}: {e}")

        return posts

    def collect_mastodon(self, accounts: List[str]) -> List[Dict[str, Any]]:
        """Collect posts from multiple Mastodon accounts in parallel."""
        all_posts = []

        logger.info(f"Collecting posts from {len(accounts)} Mastodon accounts")

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_account = {
                executor.submit(self._collect_mastodon_user, account): account
                for account in accounts
            }

            for future in as_completed(future_to_account):
                account = future_to_account[future]
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                except Exception as e:
                    logger.error(f"Mastodon account @{account} generated an exception: {e}")

        return all_posts

    # ========== TWITTERAPI.IO COLLECTION ==========

    def _collect_twitter_user_twitterapi(self, username: str) -> List[Dict[str, Any]]:
        """
        Collect recent tweets from a Twitter user using TwitterAPI.io.

        Args:
            username: Twitter username (without @)

        Returns:
            List of normalized tweet dictionaries
        """
        tweets = []

        if not TWITTERAPI_IO_KEY:
            logger.warning("TwitterAPI.io key not configured")
            return []

        try:
            logger.info(f"Fetching tweets for @{username} via TwitterAPI.io")

            headers = {
                "X-API-Key": TWITTERAPI_IO_KEY,
                "Content-Type": "application/json"
            }

            # Get user's last tweets
            response = requests.get(
                f"{TWITTERAPI_IO_BASE}/twitter/user/last_tweets",
                params={"userName": username},
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            # Parse tweets from response - nested in data.tweets
            tweet_list = data.get('data', {}).get('tweets', [])

            for tweet_data in tweet_list:
                try:
                    tweet_id = tweet_data.get('id', '')
                    text = tweet_data.get('text', '')
                    created_at = tweet_data.get('createdAt', '')

                    # Parse Twitter's date format: "Thu Jan 01 18:49:10 +0000 2026"
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(created_at)
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except:
                        try:
                            # Fallback to strptime
                            pub_date = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                            if pub_date.tzinfo:
                                pub_date = pub_date.replace(tzinfo=None)
                        except:
                            pub_date = datetime.now()

                    # Skip if outside date range
                    if pub_date < self.start_time or pub_date > self.end_time:
                        continue

                    # Get author info
                    author_data = tweet_data.get('author', {})
                    author_username = author_data.get('userName', username)

                    tweet = {
                        'id': self._generate_content_hash('twitter', tweet_id),
                        'platform_id': tweet_id,
                        'platform': 'twitter',
                        'author': author_username,
                        'author_display_name': author_data.get('name', author_username),
                        'content': text,
                        'url': tweet_data.get('twitterUrl', f"https://twitter.com/{author_username}/status/{tweet_id}"),
                        'published': pub_date.isoformat(),
                        'engagement': {
                            'retweets': tweet_data.get('retweetCount', 0),
                            'likes': tweet_data.get('likeCount', 0),
                            'replies': tweet_data.get('replyCount', 0),
                            'quotes': tweet_data.get('quoteCount', 0),
                            'views': tweet_data.get('viewCount', 0)
                        },
                        'source_type': 'twitter',
                        'collected_at': datetime.now().isoformat()
                    }

                    tweets.append(tweet)

                except Exception as e:
                    logger.error(f"Error processing tweet from @{username}: {e}")
                    continue

            logger.info(f"Collected {len(tweets)} tweets from @{username}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching tweets for @{username}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error collecting tweets from @{username}: {e}")

        return tweets

    def collect_twitter_twitterapi(self, usernames: List[str]) -> List[Dict[str, Any]]:
        """Collect tweets from multiple Twitter users via TwitterAPI.io in parallel."""
        all_tweets = []

        if not TWITTERAPI_IO_KEY:
            logger.warning("TwitterAPI.io key not configured - skipping Twitter collection")
            return []

        logger.info(f"Collecting tweets from {len(usernames)} Twitter accounts via TwitterAPI.io")

        with ThreadPoolExecutor(max_workers=3) as executor:  # Lower concurrency to respect rate limits
            future_to_username = {
                executor.submit(self._collect_twitter_user_twitterapi, username): username
                for username in usernames
            }

            for future in as_completed(future_to_username):
                username = future_to_username[future]
                try:
                    tweets = future.result()
                    all_tweets.extend(tweets)
                    time.sleep(0.5)  # Small delay between requests
                except Exception as e:
                    logger.error(f"Twitter user @{username} generated an exception: {e}")

        return all_tweets

    def collect_twitter_search(self, usernames: List[str]) -> List[Dict[str, Any]]:
        """
        Collect tweets from multiple users using TwitterAPI.io Advanced Search.

        This is more efficient than individual user timeline requests as it uses
        a single API call with OR operators: (from:user1 OR from:user2 ...)

        Args:
            usernames: List of Twitter usernames (without @)

        Returns:
            List of normalized tweet dictionaries
        """
        all_tweets = []

        if not TWITTERAPI_IO_KEY:
            logger.warning("TwitterAPI.io key not configured - skipping Twitter collection")
            return []

        if not usernames:
            return []

        # Build the search query with OR operators
        # Format: (from:user1 OR from:user2 OR ...) since:YYYY-MM-DD
        from_clauses = [f"from:{u}" for u in usernames]

        # Split into chunks if too many users (Twitter may limit query length)
        max_users_per_query = 25  # Conservative limit
        user_chunks = [from_clauses[i:i + max_users_per_query]
                       for i in range(0, len(from_clauses), max_users_per_query)]

        # Format date for search
        if self.target_date:
            since_date = self.start_time.strftime('%Y-%m-%d')
            until_date = (self.end_time + timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            since_date = self.start_time.strftime('%Y-%m-%d')
            until_date = (self.end_time + timedelta(days=1)).strftime('%Y-%m-%d')

        headers = {
            "X-API-Key": TWITTERAPI_IO_KEY,
            "Content-Type": "application/json"
        }

        for chunk_idx, chunk in enumerate(user_chunks):
            query = f"({' OR '.join(chunk)}) since:{since_date} until:{until_date}"
            logger.info(f"Twitter search query (chunk {chunk_idx + 1}/{len(user_chunks)}): {query[:100]}...")

            cursor = ""
            page = 0
            max_pages = 10  # Limit pages to control API usage

            while page < max_pages:
                try:
                    params = {
                        "query": query,
                        "queryType": "Latest"
                    }
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

                    # Parse tweets from response
                    tweets_data = data.get('tweets', [])
                    if not tweets_data:
                        tweets_data = data.get('data', {}).get('tweets', [])

                    if not tweets_data:
                        logger.info(f"No more tweets found on page {page + 1}")
                        break

                    for tweet_data in tweets_data:
                        try:
                            tweet_id = tweet_data.get('id', '')
                            text = tweet_data.get('text', '')
                            created_at = tweet_data.get('createdAt', '')
                            author = tweet_data.get('author', {})
                            username = author.get('userName', author.get('username', 'unknown'))

                            # Parse Twitter's date format
                            try:
                                from email.utils import parsedate_to_datetime
                                pub_date = parsedate_to_datetime(created_at)
                                if pub_date.tzinfo:
                                    pub_date = pub_date.replace(tzinfo=None)
                            except:
                                try:
                                    pub_date = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
                                except:
                                    pub_date = datetime.now()

                            # Check if tweet is within our time window
                            if pub_date < self.start_time or pub_date > self.end_time:
                                continue

                            # Get engagement metrics
                            likes = tweet_data.get('likeCount', 0)
                            retweets = tweet_data.get('retweetCount', 0)
                            replies = tweet_data.get('replyCount', 0)

                            # Build tweet URL
                            tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"

                            tweet = {
                                'id': self._generate_content_hash('twitter', tweet_id),
                                'platform': 'twitter',
                                'source': f'@{username}',
                                'title': f"Tweet from @{username}",
                                'content': text,
                                'url': tweet_url,
                                'published_date': pub_date.isoformat(),
                                'author': username,
                                'engagement': {
                                    'likes': likes,
                                    'retweets': retweets,
                                    'replies': replies,
                                    'total': likes + retweets + replies
                                },
                                'raw_data': tweet_data
                            }

                            all_tweets.append(tweet)

                        except Exception as e:
                            logger.error(f"Error processing tweet in search: {e}")
                            continue

                    # Check for pagination
                    has_next = data.get('has_next_page', False)
                    next_cursor = data.get('next_cursor', '')

                    if not has_next or not next_cursor:
                        break

                    cursor = next_cursor
                    page += 1
                    time.sleep(0.3)  # Rate limit between pages

                except requests.exceptions.RequestException as e:
                    logger.error(f"Error in Twitter search request: {e}")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error in Twitter search: {e}")
                    break

            # Delay between chunks
            if chunk_idx < len(user_chunks) - 1:
                time.sleep(0.5)

        logger.info(f"Twitter search collected {len(all_tweets)} tweets from {len(usernames)} users")
        return all_tweets

    # ========== REDDIT JSON COLLECTION (FREE) ==========

    def _collect_reddit_subreddit_json(self, subreddit: str) -> List[Dict[str, Any]]:
        """
        Collect hot posts from a Reddit subreddit using the free JSON endpoint.

        Args:
            subreddit: Subreddit name (without r/)

        Returns:
            List of normalized post dictionaries
        """
        posts = []

        try:
            logger.info(f"Fetching posts from r/{subreddit} via Reddit JSON")

            headers = {
                "User-Agent": REDDIT_USER_AGENT
            }

            # Get hot posts from subreddit
            response = requests.get(
                f"https://www.reddit.com/r/{subreddit}/hot.json",
                params={"limit": 50},
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            # Parse posts from response
            post_list = data.get('data', {}).get('children', [])

            for post_wrapper in post_list:
                try:
                    post_data = post_wrapper.get('data', {})

                    # Skip stickied posts (usually announcements)
                    if post_data.get('stickied', False):
                        continue

                    # Parse created time
                    created_utc = post_data.get('created_utc', 0)
                    pub_date = datetime.fromtimestamp(created_utc)

                    # Skip if outside date range
                    if pub_date < self.start_time or pub_date > self.end_time:
                        continue

                    post_id = post_data.get('id', '')

                    post = {
                        'id': self._generate_content_hash('reddit', post_id),
                        'platform_id': post_id,
                        'platform': 'reddit',
                        'subreddit': subreddit,
                        'title': post_data.get('title', ''),
                        'content': post_data.get('selftext', ''),
                        'author': post_data.get('author', ''),
                        'url': f"https://reddit.com{post_data.get('permalink', '')}",
                        'external_url': post_data.get('url', ''),
                        'published': pub_date.isoformat(),
                        'engagement': {
                            'score': post_data.get('score', 0),
                            'upvote_ratio': post_data.get('upvote_ratio', 0),
                            'num_comments': post_data.get('num_comments', 0)
                        },
                        'is_self': post_data.get('is_self', False),
                        'link_flair_text': post_data.get('link_flair_text', ''),
                        'source_type': 'reddit',
                        'collected_at': datetime.now().isoformat()
                    }

                    posts.append(post)

                except Exception as e:
                    logger.error(f"Error processing Reddit post from r/{subreddit}: {e}")
                    continue

            logger.info(f"Collected {len(posts)} posts from r/{subreddit}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching posts from r/{subreddit}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error collecting posts from r/{subreddit}: {e}")

        return posts

    def collect_reddit_json(self, subreddits: List[str]) -> List[Dict[str, Any]]:
        """Collect posts from multiple Reddit subreddits via free JSON endpoint in parallel."""
        all_posts = []

        logger.info(f"Collecting posts from {len(subreddits)} subreddits via Reddit JSON")

        with ThreadPoolExecutor(max_workers=3) as executor:  # Lower concurrency to be respectful
            future_to_subreddit = {
                executor.submit(self._collect_reddit_subreddit_json, subreddit): subreddit
                for subreddit in subreddits
            }

            for future in as_completed(future_to_subreddit):
                subreddit = future_to_subreddit[future]
                try:
                    posts = future.result()
                    all_posts.extend(posts)
                    time.sleep(1)  # Respect Reddit's rate limits
                except Exception as e:
                    logger.error(f"Subreddit r/{subreddit} generated an exception: {e}")

        return all_posts

    def save_to_file(self, content: List[Dict[str, Any]], output_path: str, content_type: str):
        """Save collected content to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'collected_at': datetime.now().isoformat(),
                'content_type': content_type,
                'count': len(content),
                'items': content
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved {len(content)} {content_type} items to {output_path}")


def load_list_from_file(file_path: str) -> List[str]:
    """Load a list of items from a text file (one per line)."""
    if not os.path.exists(file_path):
        return []
    
    with open(file_path, 'r') as f:
        items = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    return items


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Collect social media content')
    parser.add_argument('--twitter-users', type=str, help='File with Twitter usernames (one per line)')
    parser.add_argument('--reddit-subs', type=str, help='File with Reddit subreddits (one per line)')
    parser.add_argument('--output-dir', type=str, required=True, help='Output directory for collected data')
    
    args = parser.parse_args()
    
    collector = SocialMediaCollector(lookback_hours=24)
    
    # Collect Twitter
    if args.twitter_users:
        usernames = load_list_from_file(args.twitter_users)
        if usernames:
            tweets = collector.collect_twitter(usernames)
            collector.save_to_file(tweets, f"{args.output_dir}/twitter.json", "twitter")
    
    # Collect Reddit
    if args.reddit_subs:
        subreddits = load_list_from_file(args.reddit_subs)
        if subreddits:
            posts = collector.collect_reddit(subreddits)
            collector.save_to_file(posts, f"{args.output_dir}/reddit.json", "reddit")
