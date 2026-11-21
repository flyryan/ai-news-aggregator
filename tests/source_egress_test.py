"""Inspect the final HTTP adapter routes through the production entry points."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

from admin_service import balances
from agents.gatherers import reddit_gatherer, social_gatherer
from agents.gatherers.arxiv_oai import ArxivOAIHarvester, OAI_BASE_URL
from agents.gatherers.news_gatherer import NewsGatherer
from agents.gatherers.research_gatherer import ResearchGatherer
from scripts import source_egress

PROXY = 'socks5h://mullvad.invalid:1080'
CONFIG = str(Path(__file__).resolve().parents[1] / 'config')
EMPTY_RSS = b'<rss version="2.0"><channel><title>Quiet</title></channel></rss>'


def response(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result.url = 'https://example.org/'
    result._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return result


class SourceEgressTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'HTTPS_PROXY': PROXY, 'ALL_PROXY': PROXY, 'PIPELINE_PROXY_URL': PROXY,
            'NO_PROXY': 'api.scrapecreators.com,api.twitterapi.io',
        }, clear=True))
        self.enterContext(patch.object(source_egress.time, 'sleep'))
        self.send = self.enterContext(patch.object(requests.adapters.HTTPAdapter, 'send'))
        temp = self.enterContext(TemporaryDirectory())
        self.args = dict(config_dir=CONFIG, data_dir=temp, target_date='2026-11-21')

    def routes(self):
        return [c.kwargs['proxies'].get('https') for c in self.send.call_args_list]

    def test_twitter_retries_same_page_then_starts_next_page_and_balance_direct(self):
        self.enterContext(patch.object(social_gatherer, 'TWITTERAPI_IO_KEY', 'test'))
        g = social_gatherer.SocialGatherer(**self.args)
        self.send.side_effect = [
            response(503), requests.ReadTimeout(), response(body={'tweets': []}),
            response(body={'tweets': []}), response(503), response(body={'recharge_credits': 20}),
        ]
        g._twitter_page({'cursor': 'same'}, {}, 1, 2)
        g._twitter_page({'cursor': 'next'}, {}, 1, 3)
        self.assertEqual(g._fetch_twitter_balance(), 20)
        self.assertEqual(self.routes(), [None, PROXY, PROXY, None, None, PROXY])
        calls = self.send.call_args_list[:4]
        self.assertEqual([c.kwargs['timeout'] for c in calls], [(10, 60)] * 4)
        self.assertEqual(len({c.args[0].url for c in calls[:3]}), 1)
        self.assertEqual(g._twitter_calls, 4)

    def test_exact_host_and_global_exclusions_keep_all_twitter_attempts_direct(self):
        g = social_gatherer.SocialGatherer(**self.args)
        for excluded, retry in [('other.test, API.TWITTERAPI.IO.', None), ('*', None),
                                ('twitterapi.io', PROXY)]:
            with self.subTest(excluded=excluded), patch.dict(os.environ, {'PIPELINE_NO_FALLBACK_HOSTS': excluded}):
                self.send.reset_mock()
                self.send.side_effect = [response(503), response(503), response(body={'tweets': []})]
                g._twitter_page({}, {}, 1, 1)
                self.assertEqual(self.routes(), [None, retry, retry])

    def test_scrapecreators_budget_and_explicit_primary(self):
        primary = 'http://vendor-proxy.invalid'
        self.enterContext(patch.object(reddit_gatherer, 'SCRAPECREATORS_PROXY_URL', primary))
        for excluded, retry in [('', PROXY), ('api.scrapecreators.com', primary), ('*', primary)]:
            with self.subTest(excluded=excluded), patch.dict(os.environ, {'PIPELINE_NO_FALLBACK_HOSTS': excluded}):
                self.send.reset_mock()
                self.send.side_effect = [response(503), response(503)]
                g = reddit_gatherer.RedditGatherer(**self.args)
                g.credit_budget = 2
                with g._make_session() as session:
                    self.assertIsNone(g._api_get(session, '/v1/reddit/subreddit', {}))
                self.assertEqual(self.routes(), [primary, retry])
                self.assertEqual(g._calls_made, 2)

    def test_direct_feeds_retry_invalid_html_then_accept_quiet_rss(self):
        news = NewsGatherer(**self.args)
        research = ResearchGatherer(**self.args)
        for specs, fetch, host in [
            (news.feed_specs, news._fetch_feed, 'www.artificialintelligence-news.com'),
            (research.research_feed_specs, research._fetch_research_feed, 'bair.berkeley.edu'),
        ]:
            feed = next(s for s in specs if urlsplit(s.url).hostname == host)
            self.send.reset_mock()
            self.send.side_effect = [response(200, b'<html>challenge</html>'), response(200, EMPTY_RSS)]
            self.assertEqual(fetch(feed, raise_errors=True), [])
            self.assertEqual(self.routes(), [None, PROXY])
            self.send.reset_mock()
            self.send.side_effect = [response(202, b''), response(503)]
            with self.assertRaises(requests.HTTPError):
                fetch(feed, raise_errors=True)
            self.assertEqual(self.routes(), [None, PROXY])

    def test_bluesky_and_only_tested_mastodon_instances_prefer_direct(self):
        g = social_gatherer.SocialGatherer(**self.args)
        self.send.side_effect = [response(503), response(body={'feed': []})]
        self.assertEqual(g._fetch_bluesky_user('a.bsky.social'), [])
        self.assertEqual(self.routes(), [None, PROXY])
        for host, primary in [('mastodon.social', None), ('dair-community.social', None),
                              ('new-instance.example', PROXY)]:
            self.send.reset_mock()
            self.send.side_effect = [response(body={'id': 42}), response(body=[])]
            self.assertEqual(g._fetch_mastodon_user(f'person@{host}'), [])
            self.assertEqual(self.routes(), [primary, primary])

    def test_arxiv_api_retries_switch_routes_and_rss_uses_default(self):
        g = ResearchGatherer(**self.args)
        self.send.side_effect = [response(503), response(200, EMPTY_RSS), response(200, EMPTY_RSS)]
        g._fetch_category_api('cs.AI')
        g._fetch_category_rss('cs.AI')
        self.assertEqual(self.routes(), [None, PROXY, PROXY])
        self.send.reset_mock()
        self.send.side_effect = [response(503), response(200, b'<OAI-PMH/>')]
        harvester = ArxivOAIHarvester(['cs.AI'], sleep=lambda _: None)
        harvester._request_with_retry(OAI_BASE_URL, 'cs')
        self.assertEqual(self.routes(), [None, PROXY])

    def test_admin_balances_recover_and_terminal_auth_does_one_attempt(self):
        for probe, key in [(balances._probe_twitter, 'recharge_credits'),
                           (balances._probe_scrapecreators, 'creditCount')]:
            self.send.reset_mock()
            self.send.side_effect = [response(503), response(body={key: 25}), response(401)]
            self.assertEqual(probe('test'), (25, ''))
            self.assertEqual(probe('test'), (None, 'probe returned HTTP 401'))
            self.assertEqual(self.routes(), [None, PROXY, None])
