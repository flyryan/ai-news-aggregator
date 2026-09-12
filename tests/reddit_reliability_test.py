"""Regression cases for empty Reddit publication, credit exhaustion and warnings.

Run explicitly: venv/bin/python -m unittest tests.reddit_reliability_test -v
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from agents.gatherers.reddit_gatherer import RedditGatherer, FatalScrapeError
from scripts.validate_report import validate
from scripts.reddit_credit_health import reserve
from scripts.pipeline_alert import post_alert


class RedditReliabilityTests(unittest.TestCase):
    def test_credit_exhaustion_prevents_any_listing_requests(self):
        with TemporaryDirectory() as temp:
            g = RedditGatherer(config_dir=temp, data_dir=temp, target_date='2026-09-12')
            with patch.object(g, '_fetch_credit_balance', return_value=-5), patch.object(g, '_fetch_subreddit') as fetch:
                with self.assertRaises(FatalScrapeError):
                    g._gather_sync()
            fetch.assert_not_called()
            self.assertIn('exhausted', g.get_degradation())

    def test_retries_count_against_credit_budget_and_report_degradation(self):
        with TemporaryDirectory() as temp:
            g = RedditGatherer(config_dir=temp, data_dir=temp, target_date='2026-09-12')
            g.credit_budget = 2
            session = Mock()
            session.get.return_value.status_code = 503
            with patch('agents.gatherers.reddit_gatherer.time.sleep'):
                self.assertIsNone(g._api_get(session, '/v1/reddit/subreddit', {}))
            self.assertEqual(session.get.call_count, 2)
            self.assertTrue(g._stop_calls)

    def test_empty_reddit_cannot_publish_even_with_success_status(self):
        summary = {'date': '2026-09-12', 'executive_summary': 'A' * 500,
                   'top_topics': [{'name': 'AI'}], 'total_items_analyzed': 100,
                   'categories': {'reddit': {'count': 0}},
                   'collection_status': {'sources': [{'name': 'reddit', 'status': 'success', 'count': 0}]}}
        result = validate(summary, '2026-09-12')
        self.assertFalse(result['valid'])
        self.assertTrue(any('Reddit' in f for f in result['failures']))

    def test_reserve_uses_complete_daily_consumption_only(self):
        rows = [dict(date='2026-09-09', consumed=250, complete=True),
                dict(date='2026-09-10', consumed=85, complete=False)]
        self.assertEqual(reserve(rows), (750, 250))
        self.assertEqual(reserve([]), (1800, 600))

    def test_alert_bypasses_collection_proxy_and_rejects_failed_delivery(self):
        with patch.dict('os.environ', {'PIPELINE_ALERT_TOKEN': 'test-only'}), \
             patch('scripts.pipeline_alert.urllib.request.ProxyHandler') as proxy, \
             patch('scripts.pipeline_alert.urllib.request.build_opener') as opener:
            reply = opener.return_value.open.return_value.__enter__.return_value
            reply.status = 403
            reply.read.return_value = b'{"ok": false}'
            self.assertFalse(post_alert({'status': 'degraded'}))
            proxy.assert_called_once_with({})


if __name__ == '__main__':
    unittest.main()
