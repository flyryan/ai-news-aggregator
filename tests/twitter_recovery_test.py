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

    def pull(self, responses, queries=('first',), backup_key=''):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'SOCIALDATA_API_KEY': backup_key}):
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

    def test_exhausted_503_recovers_accounts_and_deduplicates_earlier_search_pages(self):
        def tweet(tweet_id, day=20, author='alice', **kwargs):
            return {'id': tweet_id, 'text': tweet_id, 'author': {'userName': author},
                    'createdAt': f'{day} Nov 2026 10:00:00 GMT', **kwargs}

        def fetch(path, *, params, **kwargs):
            if path.endswith('advanced_search'):
                return response(503) if params.get('cursor') else page('already', 'search-next')
            if path.endswith('/user/info'):
                return response(200, {'status': 'success', 'data': {'id': params['userName'] + '-id'}})
            self.assertEqual(path, '/twitter/user/tweet_timeline')
            self.assertEqual(params['includeReplies'], 'true')
            if params['userId'] == 'bob-id':
                rows = []
            elif 'cursor' not in params:
                rows = [tweet('pinned', 1, isPinned=True), tweet('already'), tweet('other', author='other'),
                        tweet('native-retweet', isRetweet=True)]
                return response(200, {'status': 'success', 'data': {'tweets': rows},
                                      'has_next_page': True, 'next_cursor': 'timeline-next'})
            else:
                self.assertEqual(params['cursor'], 'timeline-next')
                rows = [tweet('recovered'), tweet('too-old', 19)]
            return response(200, {'status': 'success', 'data': {'tweets': rows}, 'has_next_page': False})

        g, items, _, _ = self.pull(fetch, queries=('(from:alice OR from:bob) since:2026-11-20 until:2026-11-21',))
        self.assertCountEqual([i.content for i in items], ['already', 'recovered'])
        self.assertEqual(g.collection_status['twitter']['status'], 'success')
        self.assertEqual(g.source_steps['social_twitter'][0]['status'], 'success')
        self.assertEqual(g._twitter_calls, 9)
        self.assertEqual(g.collection_status['twitter']['recovered_batches'], 1)
        self.assertEqual(g._twitter_tweets_billed, 7)

    def test_incomplete_account_recovery_keeps_posts_and_reports_the_missing_account(self):
        def fetch(path, *, params, **kwargs):
            if path.endswith('advanced_search'):
                return response(503)
            if path.endswith('/user/info'):
                return response(200, {'status': 'success', 'data': {'id': '1'}})
            if params.get('cursor'):
                return response(200, {'status': 'error', 'msg': 'unavailable'})
            row = {'id': 'one', 'text': 'one', 'createdAt': '20 Nov 2026 10:00:00 GMT',
                   'author': {'userName': 'alice'}}
            return response(200, {'status': 'success', 'data': {'tweets': [row]},
                                  'has_next_page': True, 'next_cursor': 'next'})

        g, items, _, _ = self.pull(fetch, queries=('(from:alice) since:2026-11-20 until:2026-11-21',))
        self.assertEqual([i.content for i in items], ['one'])
        self.assertEqual(g.collection_status['twitter']['status'], 'partial')
        self.assertIn('alice', g.collection_status['twitter']['error'])

    def test_provider_confirmed_suspension_is_reported_separately_from_a_failed_lookup(self):
        def fetch(path, *, params, **kwargs):
            if path.endswith('advanced_search'):
                return response(503)
            if params.get('userName') == 'alice':
                return response(200, {'status': 'success', 'data': {'unavailable': True,
                                                                    'unavailableReason': 'Suspended'}})
            return response(200, {'status': 'error', 'msg': 'backend unavailable'})

        g, _, _, _ = self.pull(fetch, queries=('(from:alice OR from:bob) since:2026-11-20 until:2026-11-21',))
        row = g.collection_status['twitter']
        self.assertEqual(row.get('unavailable_accounts'), {'alice': 'suspended'})
        self.assertIn('bob', row['error'])
        self.assertNotEqual(row['status'], 'success')

    def test_search_page_cap_requires_verified_account_coverage(self):
        pages = [page(str(i), 'cursor-' + str(i)) for i in range(10)]
        g, items, _, _ = self.pull(pages + [response(401)], queries=('(from:alice) since:X until:Y',))
        self.assertEqual(len(items), 10)
        self.assertEqual(g.collection_status['twitter']['status'], 'partial')
        self.assertIn('alice', g.collection_status['twitter']['error'])

    def test_account_recovery_stops_at_the_shared_paid_request_limit(self):
        replies = [response(503)] * 3 + [response(200, {'status': 'success', 'data': {'id': '1'}})]
        with patch.object(social, 'TWITTER_TIMELINE_CALL_LIMIT', 1):
            g, items, _, _ = self.pull(replies, queries=('(from:alice) since:X until:Y',))
        self.assertEqual(items, [])
        self.assertEqual(g._twitter_calls, 4)
        self.assertEqual(g.collection_status['twitter']['status'], 'failed')

    def test_overlapping_search_pages_count_each_published_post_once(self):
        g, items, _, _ = self.pull([page('same', 'again'), page('same')])
        self.assertEqual([i.content for i in items], ['same'])
        self.assertEqual(g._twitter_tweets_billed, 2)

    def socialdata_pull(self, accounts, primary, backup):
        def fetch(path, *, params, **kwargs):
            if path.endswith('advanced_search'):
                return response(503)
            return primary(path, params)

        query = '(' + ' OR '.join('from:' + name for name in accounts) + ')'
        with patch('scripts.source_egress.routed_get', side_effect=backup) as requests_sent:
            g, items, _, _ = self.pull(fetch, queries=(query,), backup_key='backup-key')
        return g, items, requests_sent

    def test_socialdata_only_visits_unresolved_accounts_and_keeps_primary_posts(self):
        def primary(path, params):
            if 'userName' in params:
                name = params['userName']
                if name == 'gone':
                    return response(200, {'status': 'error', 'msg': 'user not found'})
                return response(200, {'status': 'success', 'data': {'id': name}})
            if params['userId'] == 'bob':
                return response(200, {'status': 'success', 'tweets': [], 'has_next_page': False})
            if params.get('cursor'):
                return response(200, {'status': 'error'})
            return response(200, {'status': 'success', 'tweets': [{
                'author': {'userName': 'alice'}, 'id': '101', 'text': 'primary copy',
                'createdAt': '20 Nov 2026 10:00:00 GMT',
            }], 'has_next_page': True, 'next_cursor': 'primary-next'})

        def backup(url, *, params, headers, timeout, attempt, allow_redirects):
            self.assertFalse(allow_redirects)
            self.assertEqual(headers['Authorization'], 'Bearer backup-key')
            if url.endswith('/user/alice'):
                return response(200, {'id_str': '11', 'screen_name': 'Alice'})
            self.assertTrue(url.endswith('/user/11/tweets-and-replies'), url)
            if params.get('cursor'):
                return response(200, {'tweets': [], 'next_cursor': None})
            tweet = {'id_str': '101', 'user': {'screen_name': 'Alice'}, 'full_text': 'duplicate',
                     'tweet_created_at': '2026-11-20T10:00:00Z'}
            rows = [tweet, dict(tweet, id_str='102', full_text='new reply', favorite_count=12),
                    dict(tweet, id_str='103', retweeted_status={'id_str': '1'}),
                    dict(tweet, id_str='104', user={'screen_name': 'other'}),
                    dict(tweet, id_str='105', tweet_created_at='2026-11-22T00:00:00Z')]
            return response(200, {'tweets': rows, 'next_cursor': 'more'})

        g, items, sent = self.socialdata_pull(['alice', 'bob', 'gone'], primary, backup)
        self.assertEqual(sent.call_count, 3)
        self.assertCountEqual([i.content for i in items], ['primary copy', 'new reply'])
        new = next(i for i in items if i.content == 'new reply')
        self.assertEqual(new.metadata['engagement']['likes'], 12)
        self.assertEqual(new.url, 'https://twitter.com/Alice/status/102')
        row = g.collection_status['twitter']
        self.assertEqual(row['socialdata_accounts'], {'alice': 'recovered'})
        self.assertEqual(row['status'], 'success')
        self.assertEqual(row['unavailable_accounts'], {'gone': 'not found'})
        from agents.cost_tracker import get_tracker
        usage = get_tracker().external_apis['SocialData (Twitter fallback)']
        self.assertEqual((usage['calls'], usage['items'], usage['est_cost_usd']), (3, 5, 0.0012))

    def test_socialdata_insufficient_funds_stops_further_requests_and_keeps_partial(self):
        g, _, sent = self.socialdata_pull(['alice', 'bob'], lambda *_: response(500),
                                          [response(402)])
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(g.collection_status['twitter']['status'], 'failed')
        self.assertIn('alice, bob', g.collection_status['twitter']['error'])

    def test_primary_completion_uses_no_socialdata(self):
        def healthy(path, params):
            if 'userName' in params:
                return response(200, {'status': 'success', 'data': {'id': '11'}})
            return response(200, {'status': 'success', 'tweets': [], 'has_next_page': False})
        g, _, sent = self.socialdata_pull(['alice'], healthy, [])
        sent.assert_not_called()
        self.assertEqual(g.collection_status['twitter']['status'], 'success')
        self.assertNotIn('socialdata_accounts', g.collection_status['twitter'])

    def test_socialdata_call_budget_includes_retries_and_keeps_an_unfinished_page(self):
        tweet = {'id_str': '1', 'user': {'screen_name': 'alice'}, 'full_text': 'retained',
                 'tweet_created_at': '2026-11-20T10:00:00+00:00'}
        backup = [response(503), response(200, {'id_str': '11', 'screen_name': 'alice'}),
                  response(200, {'tweets': [tweet], 'next_cursor': 'more'})]
        with patch.dict(os.environ, {'SOCIALDATA_MAX_CALLS': '3'}):
            g, items, sent = self.socialdata_pull(['alice'], lambda *_: response(500), backup)
        self.assertEqual([c.kwargs['attempt'] for c in sent.call_args_list], [1, 2, 1])
        self.assertEqual([i.content for i in items], ['retained'])
        self.assertEqual(g.collection_status['twitter']['status'], 'partial')
        self.assertEqual(g.collection_status['twitter']['socialdata_accounts'], {'alice': 'request limit reached'})

    def test_socialdata_repeated_cursor_stays_incomplete_and_deduplicates(self):
        tweet = {'id_str': '1', 'user': {'screen_name': 'alice'}, 'full_text': 'one',
                 'tweet_created_at': '2026-11-20T08:00:00Z'}
        backup = [response(200, {'id_str': '11', 'screen_name': 'alice'})] + [
            response(200, {'tweets': [tweet], 'next_cursor': 'same'})] * 2
        g, items, _ = self.socialdata_pull(['alice'], lambda *_: response(500), backup)
        self.assertEqual([i.content for i in items], ['one'])
        self.assertEqual(g.collection_status['twitter']['status'], 'partial')

    def test_exhausted_search_timeout_tries_primary_account_before_backup(self):
        with patch('scripts.source_egress.routed_get') as backup:
            g, _, calls, _ = self.pull([requests.ReadTimeout()] * 3 + [
                response(200, {'status': 'success', 'data': {'id': '11'}}),
                response(200, {'status': 'success', 'tweets': [], 'has_next_page': False})],
                queries=('from:alice',), backup_key='backup-key')
            backup.assert_not_called()
        self.assertEqual(calls.call_count, 5)
        self.assertEqual(g.collection_status['twitter']['status'], 'success')

    def test_successful_search_and_disabled_backup_never_spend(self):
        with patch('scripts.source_egress.routed_get') as backup:
            g, _, _, _ = self.pull([page('ok')], queries=('from:alice',), backup_key='key')
            self.assertEqual(g.collection_status['twitter']['status'], 'success')
            with patch.dict(os.environ, {'SOCIALDATA_MAX_CALLS': '0'}):
                failed, _, _, _ = self.pull([response(503)] * 6, queries=('from:alice',), backup_key='key')
            backup.assert_not_called()
        self.assertEqual(failed.collection_status['twitter']['status'], 'failed')
