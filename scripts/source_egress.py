"""Bounded HTTP GETs for sources with an explicitly selected primary route."""
import logging
import os
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)
PUBLIC_RETRY_STATUS = (403, *TRANSIENT_STATUS)


def routed_get(url, *, attempt=1, session=None, primary_proxy=None, **kwargs):
    """Send exactly one request. Callers with a retry/credit budget own the loop.

    A supplied session must ignore environment proxies. Per-request proxies
    avoid mutating the shared session when concurrent feeds use different routes.
    """
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    proxy = primary_proxy
    if attempt > 1:
        exclusions = {h.strip().lower().rstrip('.') for h in
                      os.getenv('PIPELINE_NO_FALLBACK_HOSTS', '').split(',')}
        fallback = os.getenv('PIPELINE_PROXY_URL', '').strip()
        route = 'primary proxy' if primary_proxy else 'direct'
        if fallback and '*' not in exclusions and host not in exclusions:
            proxy = fallback
            route = 'pipeline proxy'
        logger.info("Retrying %s on %s (attempt %d)", host, route, attempt)

    if proxy:
        kwargs['proxies'] = {'http': proxy, 'https': proxy}
    if session is not None:
        return session.get(url, **kwargs)

    import requests
    with requests.Session() as client:
        client.trust_env = False
        return client.get(url, **kwargs)


def get_with_retry(url, *, read=None, retry_status=PUBLIC_RETRY_STATUS, **kwargs):
    """Two attempts for sources without their own retry loop, including feeds."""
    import requests
    from urllib3.exceptions import InvalidHeader
    from urllib3.util.retry import Retry

    for attempt in (1, 2):
        try:
            response = routed_get(url, attempt=attempt, **kwargs)
            response.raise_for_status()
            return read(response) if read else response
        except requests.HTTPError as exc:
            if attempt == 2 or exc.response.status_code not in retry_status:
                raise
            reason = f'HTTP {exc.response.status_code}'
            try:
                delay = Retry().get_retry_after(exc.response) or 1
            except InvalidHeader:
                delay = 1
            if delay > 60:
                raise
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                InvalidFeed) as exc:
            if attempt == 2:
                raise
            reason = type(exc).__name__
            delay = 1
        logger.warning("Primary request to %s failed (%s), retrying in %ss", urlsplit(url).hostname, reason, delay)
        time.sleep(delay)


class InvalidFeed(ValueError):
    pass


def read_feed(response):
    """Treat HTML challenges as failed fetches and accept a valid, quiet feed."""
    import feedparser
    feed = feedparser.parse(response.content, response_headers={
        'content-type': response.headers.get('content-type', '')
    })
    if not feed.get('version') and not feed.entries:
        raise InvalidFeed('response contained no recognizable RSS or Atom feed')
    return feed
