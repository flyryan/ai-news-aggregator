"""Twitter search recovers individual pages and reports the final batch outcome."""
import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from agents.gatherers import social_gatherer as social
from agents.gatherers.social_gatherer import SocialGatherer


def response(status, payload=None, retry_after=None):
    resp = requests.Response()
    resp.status_code = status
    resp.url = 'https://api.twitterapi.io/search?cursor=private-marker'
    resp._content = json.dumps(payload or {'tweets': []}).encode()
    if retry_after:
        resp.headers['Retry-After'] = retry_after
    return resp


def page(tweet_id, cursor=None):
    tweet = {'id': tweet_id, 'text': tweet_id, 'createdAt': 'Fri, 20 Nov 2026 09:00:00 GMT'}
    return response(200, {'tweets': [tweet], 'has_next_page': bool(cursor), 'next_cursor': cursor})


class TwitterRecoveryTests(unittest.TestCase):
    def test_search_retry_and_balance_bypass_pipeline_proxy(self):
        proxy = 'socks5h://127.0.0.1:19876'
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'HTTPS_PROXY': proxy, 'ALL_PROXY': proxy, 'NO_PROXY': ''}, clear=True):
            gatherer = SocialGatherer(config_dir=tmp, data_dir=tmp, target_date='2026-11-21')
            gatherer.twitter_users = ['someone']
            with patch.object(social, 'TWITTERAPI_IO_KEY', 'test'), patch.object(social.time, 'sleep'), \
                    patch.object(requests.adapters.HTTPAdapter, 'send', side_effect=[
                        response(503), page('a'), response(200, {'recharge_credits': 25})
                    ]) as send:
                items = gatherer._collect_twitter()
            self.assertEqual([i.content for i in items], ['a'])
            self.assertEqual([c.args[0].path_url.split('?')[0] for c in send.call_args_list], [
                '/twitter/tweet/advanced_search', '/twitter/tweet/advanced_search', '/oapi/my/info'])
            self.assertEqual([c.kwargs['proxies'] for c in send.call_args_list], [{}, {}, {}])

    def pull(self, responses, queries=('first',)):
        with TemporaryDirectory() as tmp:
            gatherer = SocialGatherer(config_dir=tmp, data_dir=tmp, target_date='2026-11-21')
            gatherer.twitter_users = ['someone']
            with patch.object(social, 'TWITTERAPI_IO_KEY', 'test'), \
                    patch.object(social, 'build_twitter_queries', return_value=queries), \
                    patch.object(social, '_twitter_get', side_effect=responses) as get, \
                    patch.object(social.time, 'sleep') as sleep, \
                    patch.object(gatherer, '_fetch_twitter_balance', return_value=None):
                items = gatherer._collect_twitter()
        return gatherer, items, get, sleep

    def test_slow_pages_can_finish_on_the_first_attempt(self):
        def slow_response(url, *, params, headers, timeout, attempt):
            connect_timeout, read_timeout = timeout
            self.assertEqual(connect_timeout, 10)
            if read_timeout < 55:
                raise requests.ReadTimeout()
            return page('b') if 'cursor' in params else page('a', 'next')

        g, items, get, _ = self.pull(slow_response)
        self.assertEqual([i.content for i in items], ['a', 'b'])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(g.collection_status['twitter']['recovered_pages'], 0)

    def test_retry_same_cursor_recovers_without_duplication_or_partial_status(self):
        g, items, get, sleep = self.pull([page('a', 'next'), requests.ReadTimeout(),
                                          response(503, retry_after='7'), page('b')])
        self.assertEqual([i.content for i in items], ['a', 'b'])
        self.assertEqual([c.kwargs['params'].get('cursor') for c in get.call_args_list],
                         [None, 'next', 'next', 'next'])
        sleep.assert_any_call(7)
        self.assertEqual(g._twitter_calls, 4)
        self.assertEqual(g._twitter_tweets_billed, 2)
        self.assertEqual(g.collection_status['twitter']['status'], 'success')
        self.assertEqual(g.collection_status['twitter']['recovered_pages'], 1)
        self.assertEqual(g.source_steps['social_twitter'][0]['status'], 'success')

    def test_exhausted_page_keeps_earlier_items_and_continues_next_batch(self):
        g, items, get, _ = self.pull([page('a', 'next'), *[requests.ReadTimeout()] * 3,
                                      page('b')], queries=('first', 'second'))
        self.assertEqual([i.content for i in items], ['a', 'b'])
        self.assertEqual(get.call_count, 5)
        row = g.collection_status['twitter']
        self.assertEqual(row['status'], 'partial')
        self.assertIn('1/2', row['error'])
        self.assertIn('read timeout', row['error'].lower())
        self.assertEqual([s['status'] for s in g.source_steps['social_twitter']], ['partial', 'success'])

    def test_terminal_http_error_and_long_retry_after_do_not_repeat(self):
        for status, delay in [(400, None), (401, None), (429, '3600')]:
            with self.subTest(status=status):
                g, _, get, _ = self.pull([response(status, retry_after=delay)])
                self.assertEqual(get.call_count, 1)
                row = g.collection_status['twitter']
                self.assertEqual(row['status'], 'failed')
                self.assertIn(f'HTTP {status}', row['error'])
                self.assertNotIn('private-marker', row['error'])

    def test_all_batches_interrupted_after_saving_posts_is_partial(self):
        g, items, _, _ = self.pull([page('a', 'next'), response(400)])
        self.assertEqual(len(items), 1)
        self.assertEqual(g.collection_status['twitter']['status'], 'partial')
