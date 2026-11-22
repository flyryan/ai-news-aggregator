"""Last-resort account timelines for Twitter collection, billed per returned item."""
from datetime import datetime, timezone
from email.utils import format_datetime

import requests

from scripts.source_egress import TRANSIENT_STATUS, get_with_retry


class SocialDataError(Exception):
    """An account could not be covered. Its message is safe for the public report."""


class SocialDataTimelines:
    """One run's request budget and usage. The gatherer visits unresolved accounts serially."""

    def __init__(self, key, max_calls=40):
        self.key = key
        self.max_calls = max_calls
        self.calls = 0
        self.profiles = 0
        self.tweets = 0
        self.stop_reason = None

    def _reserve_attempt(self):
        if self.stop_reason:
            raise SocialDataError(self.stop_reason)
        if self.calls >= self.max_calls:
            raise SocialDataError("request limit reached")
        self.calls += 1

    def _get(self, path, params=None):
        try:
            response = get_with_retry(
                f"https://api.socialdata.tools/twitter/user/{path}",
                headers={'Authorization': f'Bearer {self.key}', 'Accept': 'application/json'},
                params=params or {}, timeout=(10, 60), retry_status=TRANSIENT_STATUS,
                before_attempt=self._reserve_attempt, allow_redirects=False,
            )
            if response.status_code != 200:
                raise SocialDataError(f"HTTP {response.status_code}")
            data = response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code
            reason = f"HTTP {status}"
            if status in (401, 402, 403, 429):
                self.stop_reason = reason
            raise SocialDataError(reason) from exc
        except requests.RequestException as exc:
            raise SocialDataError(type(exc).__name__) from exc
        if not isinstance(data, dict) or data.get('status') == 'error':
            raise SocialDataError("invalid provider response")
        return data

    def posts(self, handle, start, end):
        """Yield normalized authored posts, raising after any partial results on failure."""
        profile = self._get(handle)
        self.profiles += 1
        user_id = str(profile.get('id_str') or profile.get('id') or '')
        if not user_id.isdecimal() or (profile.get('screen_name') or '').lower() != handle.lower():
            raise SocialDataError("profile identity mismatch")
        if profile.get('protected'):
            raise SocialDataError("protected profile")
        pinned = set(profile.get('pinned_tweet_ids_str') or [])
        params = {}
        cursors = set()
        for _ in range(20):
            data = self._get(f'{user_id}/tweets-and-replies', params)
            tweets = data.get('tweets')
            if not isinstance(tweets, list):
                raise SocialDataError("invalid timeline response")
            self.tweets += len(tweets)
            dates = []
            for tweet in tweets:
                author = tweet.get('user') or {}
                username = author.get('screen_name')
                if not username:
                    raise SocialDataError("missing post author")
                if username.lower() != handle.lower() or tweet.get('retweeted_status'):
                    continue
                tweet_id = tweet.get('id_str')
                published = datetime.fromisoformat(tweet['tweet_created_at'].replace('Z', '+00:00'))
                if not tweet_id or published.tzinfo is None:
                    raise SocialDataError("missing post ID or time zone")
                published = published.astimezone(timezone.utc)
                # The primary collector compares provider timestamps as naive UTC.
                timestamp = published.replace(tzinfo=None)
                if tweet_id not in pinned and not tweet.get('is_pinned'):
                    dates.append(timestamp)
                if start <= timestamp <= end:
                    content = tweet.get('full_text') or tweet.get('text')
                    if not isinstance(content, str):
                        raise SocialDataError("missing post text")
                    yield {
                        'id': tweet_id, 'createdAt': format_datetime(published), 'text': content,
                        'author': {'userName': username, 'name': author.get('name') or username},
                        'likeCount': tweet.get('favorite_count') or 0,
                        'retweetCount': tweet.get('retweet_count') or 0,
                        'replyCount': tweet.get('reply_count') or 0,
                        'quoteCount': tweet.get('quote_count') or 0,
                        'viewCount': tweet.get('views_count') or 0,
                    }
            if dates and max(dates) < start:
                return
            cursor = data.get('next_cursor')
            if not cursor:
                return
            if not isinstance(cursor, str) or cursor in cursors:
                raise SocialDataError("invalid or repeated timeline cursor")
            cursors.add(cursor)
            params = {'cursor': cursor}
        raise SocialDataError("account page limit reached")
