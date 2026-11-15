"""A source whose every fetch failed must report `failed` and block publication.

2026-11-15: the CI Mullvad SOCKS proxy was dead, so every proxied fetch hit a
ConnectTimeout. Each RSS feed, research blog and LessWrong call caught its own
error and returned an empty list, Twitter's "All 7 API requests failed" was
overwritten with `success`, and the report published green with news 0,
research 0, social 0 and only Reddit in it. The publish gate passed because the
executive summary, topics and items (all Reddit) existed.

These cases pin both halves: the gatherers now tell "could not fetch" apart from
"nothing happened", and `scripts/validate_report.py` refuses a source that
failed with nothing to show -- without failing a quiet day, a weekend arXiv
skip, or a source that only partly failed.

Run explicitly: venv/bin/python -m unittest tests.zero_source_gate_test -v
"""
import asyncio
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from agents.base import BaseGatherer
from agents.gatherers import news_gatherer as news_mod
from agents.gatherers import research_gatherer as research_mod
from agents.gatherers import social_gatherer as social_mod
from agents.gatherers.news_gatherer import NewsGatherer
from agents.gatherers.research_gatherer import ResearchGatherer
from agents.gatherers.social_gatherer import SocialGatherer
from agents.orchestrator import MainOrchestrator
from scripts.validate_report import validate

# 2026-11-15 was a Sunday: arXiv is legitimately skipped on this report date.
SUNDAY = '2026-11-15'
EMPTY_RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Quiet</title></channel></rss>'


def _status_for(gatherer, name, items):
    """Run the orchestrator's real status rule without building a whole pipeline."""
    fake = SimpleNamespace(gatherers={name: gatherer})
    return MainOrchestrator._status_for(fake, name, items)


def _ok_response(body=EMPTY_RSS):
    resp = Mock()
    resp.content = body
    resp.headers = {'content-type': 'application/rss+xml'}
    resp.raise_for_status.return_value = None
    return resp


def _write(path, lines):
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


class NewsZeroSourceTests(unittest.TestCase):
    FEEDS = ['https://a.example/feed', 'https://b.example/feed', 'https://c.example/feed']

    def _gatherer(self, temp):
        _write(os.path.join(temp, 'rss_feeds.txt'), self.FEEDS)
        return NewsGatherer(config_dir=temp, data_dir=temp, target_date=SUNDAY)

    def test_every_feed_failing_marks_news_failed(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            with patch.object(g.feed_session, 'get',
                              side_effect=requests.exceptions.ConnectTimeout('SOCKS timeout')):
                items = asyncio.run(g._collect_rss())
            self.assertEqual(items, [])
            self.assertTrue(all(s['status'] == 'failed' for s in g.source_steps['news']))
            status = _status_for(g, 'news', items)
            self.assertEqual(status['status'], 'failed')
            self.assertIn('all 3 RSS feed fetches failed', status['error'])

    def test_quiet_feeds_stay_success(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            with patch.object(g.feed_session, 'get', return_value=_ok_response()):
                items = asyncio.run(g._collect_rss())
            self.assertEqual(items, [])
            status = _status_for(g, 'news', items)
            self.assertEqual(status, {'status': 'success', 'count': 0, 'error': None})

    def test_one_dead_feed_among_healthy_ones_is_not_a_failure(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)

            def get(url, timeout=None):
                if url.startswith('https://a.'):
                    raise requests.exceptions.ConnectTimeout('one feed down')
                return _ok_response()

            with patch.object(g.feed_session, 'get', side_effect=get):
                items = asyncio.run(g._collect_rss())
            statuses = sorted(s['status'] for s in g.source_steps['news'])
            self.assertEqual(statuses, ['failed', 'success', 'success'])
            self.assertEqual(_status_for(g, 'news', items)['status'], 'success')

    def test_fetch_feed_default_still_swallows_errors(self):
        """Other callers of `_fetch_feed` keep the old never-raise contract."""
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            with patch.object(g.feed_session, 'get', side_effect=requests.exceptions.ConnectionError('x')):
                self.assertEqual(g._fetch_feed(self.FEEDS[0]), [])
                with self.assertRaises(requests.exceptions.ConnectionError):
                    g._fetch_feed(self.FEEDS[0], raise_errors=True)


class ResearchZeroSourceTests(unittest.TestCase):
    FEEDS = ['https://www.lesswrong.com/feed.xml', 'https://blog-a.example/rss', 'https://blog-b.example/rss']

    def _gatherer(self, temp):
        _write(os.path.join(temp, 'research_feeds.txt'), self.FEEDS)
        return ResearchGatherer(config_dir=temp, data_dir=temp, target_date=SUNDAY)

    def test_weekend_with_every_blog_failing_is_failed(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            timeout = requests.exceptions.ConnectTimeout('SOCKS timeout')
            with patch.object(research_mod.requests, 'get', side_effect=timeout), \
                    patch.object(g, '_execute_lesswrong_graphql', side_effect=timeout):
                items = asyncio.run(g.gather())
            self.assertEqual(items, [])
            self.assertNotIn('research_arxiv', g.source_steps)  # weekend skip records nothing
            self.assertTrue(all(s['status'] == 'failed' for s in g.source_steps['research_blogs']))
            status = _status_for(g, 'research', items)
            self.assertEqual(status['status'], 'failed')
            self.assertIn('all 3 research blog feed fetches failed', status['error'])

    def test_lesswrong_graphql_error_payload_is_a_failed_step(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            with patch.object(research_mod.requests, 'get', return_value=_ok_response()), \
                    patch.object(g, '_execute_lesswrong_graphql', return_value={'errors': [{'message': 'nope'}]}):
                asyncio.run(g.gather())
            by_name = {s['name']: s['status'] for s in g.source_steps['research_blogs']}
            self.assertEqual(by_name['LessWrong'], 'failed')
            # The other blogs answered, so research is a quiet day, not an outage.
            self.assertEqual(_status_for(g, 'research', [])['status'], 'success')

    def test_weekend_with_quiet_blogs_stays_success(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            with patch.object(research_mod.requests, 'get', return_value=_ok_response()), \
                    patch.object(g, '_execute_lesswrong_graphql',
                                 return_value={'data': {'posts': {'results': []}}}):
                items = asyncio.run(g.gather())
            self.assertEqual(items, [])
            self.assertEqual(_status_for(g, 'research', items),
                             {'status': 'success', 'count': 0, 'error': None})


class StepRuleEdgeTests(unittest.TestCase):
    class _G(BaseGatherer):
        @property
        def category(self):
            return 'x'

        async def gather(self):
            return []

    def _g(self, temp):
        return self._G(config_dir=temp, data_dir=temp, target_date=SUNDAY)

    def test_no_steps_means_no_inference(self):
        with TemporaryDirectory() as temp:
            self.assertEqual(self._g(temp).step_failure_reasons(), [])

    def test_capped_steps_are_not_called_total_failure(self):
        with TemporaryDirectory() as temp:
            g = self._g(temp)
            with self.assertRaises(RuntimeError):
                with g.time_step('src', 'one'):
                    raise RuntimeError('down')
            self.assertEqual(g.step_failure_reasons(), ['the only src fetch failed'])
            g._source_steps_dropped['src'] = 5  # unrecorded steps may have succeeded
            self.assertEqual(g.step_failure_reasons(), [])

    def test_partial_step_is_not_failed(self):
        with TemporaryDirectory() as temp:
            g = self._g(temp)
            with g.time_step('src', 'one') as step:
                step.status = 'partial'
            self.assertEqual(g.step_failure_reasons(), [])


class SocialZeroSourceTests(unittest.TestCase):
    def _gatherer(self, temp, twitter=True, bluesky=True, mastodon=True):
        _write(os.path.join(temp, 'twitter_accounts.txt'), ['someone'] if twitter else ['# none'])
        _write(os.path.join(temp, 'bluesky_accounts.txt'), ['a.bsky.social'] if bluesky else ['# none'])
        _write(os.path.join(temp, 'mastodon_accounts.txt'), ['a@m.social'] if mastodon else ['# none'])
        return SocialGatherer(config_dir=temp, data_dir=temp, target_date=SUNDAY)

    def _run(self, g, twitter_key='key', bluesky_exc=None, mastodon_exc=None, twitter_exc=None):
        def bluesky(handle):
            if bluesky_exc:
                raise bluesky_exc
            return []

        def mastodon(account):
            if mastodon_exc:
                raise mastodon_exc
            return []

        session = Mock()
        session.get.side_effect = twitter_exc
        if twitter_exc is None:
            session.get.return_value.json.return_value = {'tweets': []}
            session.get.side_effect = None
        with patch.object(social_mod, 'TWITTERAPI_IO_KEY', twitter_key), \
                patch.object(social_mod.requests, 'get', session.get), \
                patch.object(social_mod.time, 'sleep'), \
                patch.object(g, '_fetch_twitter_balance', return_value=None), \
                patch.object(g, '_fetch_bluesky_user', side_effect=bluesky), \
                patch.object(g, '_fetch_mastodon_user', side_effect=mastodon), \
                patch.object(g, 'save_to_file'):
            return asyncio.run(g.gather())

    def test_twitter_all_chunks_failed_is_not_overwritten_with_success(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            self._run(g, twitter_exc=requests.exceptions.ConnectTimeout('SOCKS'))
            row = g.collection_status['twitter']
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['error'], 'All 1 API requests failed')
            self.assertEqual(row['count'], 0)

    def test_twitter_clean_zero_tweets_is_success(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            items = self._run(g)
            self.assertEqual(g.collection_status['twitter']['status'], 'success')
            self.assertEqual(_status_for(g, 'social', items),
                             {'status': 'success', 'count': 0, 'error': None})

    def test_every_platform_failed_is_failed(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            boom = requests.exceptions.ConnectTimeout('SOCKS')
            items = self._run(g, twitter_exc=boom, bluesky_exc=boom, mastodon_exc=boom)
            status = _status_for(g, 'social', items)
            self.assertEqual(status['status'], 'failed')
            for platform in ('twitter', 'bluesky', 'mastodon'):
                self.assertIn(f'{platform} failed', status['error'])

    def test_one_dead_platform_beside_quiet_healthy_ones_is_partial(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            items = self._run(g, bluesky_exc=requests.exceptions.ConnectTimeout('SOCKS'))
            self.assertEqual(items, [])
            status = _status_for(g, 'social', items)
            self.assertEqual(status['status'], 'partial')
            self.assertIn('bluesky failed', status['error'])

    def test_twitter_without_key_stays_skipped_and_others_decide(self):
        with TemporaryDirectory() as temp:
            g = self._gatherer(temp)
            boom = requests.exceptions.ConnectTimeout('SOCKS')
            items = self._run(g, twitter_key='', bluesky_exc=boom, mastodon_exc=boom)
            self.assertEqual(g.collection_status['twitter']['status'], 'skipped')
            # Every ENABLED platform failed; a skipped one does not rescue the source.
            self.assertEqual(_status_for(g, 'social', items)['status'], 'failed')


def _report(sources):
    return {
        'date': SUNDAY, 'executive_summary': 'A' * 500,
        'top_topics': [{'name': 'AI'}], 'total_items_analyzed': 557,
        'categories': {'reddit': {'count': 557, 'category_summary': 'A' * 400}},
        'collection_status': {'sources': [{'name': 'reddit', 'status': 'success', 'count': 557}] + sources},
        'hero_image_url': '/data/x/hero.webp',
    }


class ValidatorZeroSourceTests(unittest.TestCase):
    def test_2026_11_15_with_corrected_statuses_is_rejected(self):
        """The real day, as the fixed gatherers now report it."""
        result = validate(_report([
            {'name': 'news', 'status': 'failed', 'count': 0, 'error': 'all 26 RSS feed fetches failed'},
            {'name': 'research', 'status': 'failed', 'count': 0,
             'error': 'all 19 research blog feed fetches failed'},
            {'name': 'social', 'status': 'failed', 'count': 0,
             'error': 'twitter failed: All 7 API requests failed; bluesky failed: All 40 handles failed'},
        ]), SUNDAY)
        self.assertFalse(result['valid'])
        joined = ' | '.join(result['failures'])
        for name in ('news', 'research', 'social'):
            self.assertIn(f'{name} collection failed with 0 items', joined)
        self.assertIn('all 26 RSS feed fetches failed', joined)

    def test_single_failed_source_is_enough(self):
        result = validate(_report([
            {'name': 'news', 'status': 'success', 'count': 120},
            {'name': 'research', 'status': 'success', 'count': 0},
            {'name': 'social', 'status': 'failed', 'count': 0, 'error': 'x'},
        ]), SUNDAY)
        self.assertFalse(result['valid'])
        self.assertEqual(result['failures'], ['social collection failed with 0 items: x'])

    def test_quiet_and_weekend_sources_pass(self):
        result = validate(_report([
            {'name': 'news', 'status': 'success', 'count': 120},
            {'name': 'research', 'status': 'success', 'count': 0},  # weekend, blogs quiet
            {'name': 'social', 'status': 'success', 'count': 0},
        ]), SUNDAY)
        self.assertTrue(result['valid'], result['failures'])

    def test_partial_is_a_warning_not_a_failure(self):
        result = validate(_report([
            {'name': 'news', 'status': 'success', 'count': 120},
            {'name': 'research', 'status': 'partial', 'count': 300, 'error': 'all 19 research blog feed fetches failed'},
            {'name': 'social', 'status': 'partial', 'count': 0, 'error': 'bluesky failed: All 40 handles failed'},
        ]), SUNDAY)
        self.assertTrue(result['valid'], result['failures'])
        self.assertTrue(any('research collection partial' in w for w in result['warnings']))
        self.assertTrue(any('social collection partial' in w for w in result['warnings']))


if __name__ == '__main__':
    unittest.main()
