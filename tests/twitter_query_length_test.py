"""Twitter search queries stay under TwitterAPI.io's 512-character limit.

Chunks used to be a fixed 25 accounts. The second chunk of
config/twitter_accounts.txt came to 523 characters, TwitterAPI.io answered
HTTP 400 ("query is too long ... The maximum length is 512 characters") on its
first page, and those 25 accounts were missing from every report. The other
six chunks sat 3-19 characters under the limit.
"""
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agents.gatherers import social_gatherer as social_mod
from agents.gatherers.social_gatherer import (
    TWITTER_QUERY_MAX_CHARS,
    SocialGatherer,
    build_twitter_queries,
)

ROOT = Path(__file__).resolve().parents[1]
SINCE, UNTIL = '2026-11-14', '2026-11-15'


def _configured_accounts():
    lines = (ROOT / 'config' / 'twitter_accounts.txt').read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith('#')]


def _handles_in(query):
    return re.findall(r'from:([A-Za-z0-9_]+)', query)


class BuildTwitterQueriesTests(unittest.TestCase):
    def test_configured_accounts_fit_the_limit_and_all_appear_once(self):
        accounts = _configured_accounts()
        queries = build_twitter_queries(accounts, SINCE, UNTIL)
        for query in queries:
            self.assertLessEqual(len(query), TWITTER_QUERY_MAX_CHARS, query)
        sent = [h for q in queries for h in _handles_in(q)]
        self.assertEqual(sorted(sent), sorted(a.lstrip('@') for a in accounts))

    def test_configured_accounts_are_valid_handles(self):
        bad = [a for a in _configured_accounts()
               if not re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', a)]
        self.assertEqual(bad, [], 'these can never match a from: clause')

    def test_the_chunk_that_was_rejected_now_splits(self):
        # The exact 25 accounts that produced the 523-character query.
        chunk2 = ['lexfridman', 'goodfellow_ian', 'percyliang', 'ilyasut', 'noamshazeer',
                  '_jasonwei', 'AlecRad', 'ch402', 'janleike', 'dpkingma', 'NeelNanda5',
                  'OriolVinyalsML', 'hugo_larochelle', 'ZoubinGhahrama1', 'sirbayes',
                  'shakir_za', 'TheGregYang', 'igorbabuschkin', 'GuillaumeLample',
                  'LacroixTimothee', 'nickfrosst', 'Nils_Reimers', 'lilianweng',
                  'DaniYogatama', 'YiTayML']
        old = f"({' OR '.join('from:' + u for u in chunk2)}) since:{SINCE} until:{UNTIL}"
        self.assertGreater(len(old), TWITTER_QUERY_MAX_CHARS)
        queries = build_twitter_queries(chunk2, SINCE, UNTIL)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(len(q) <= TWITTER_QUERY_MAX_CHARS for q in queries))
        self.assertEqual([h for q in queries for h in _handles_in(q)], chunk2)

    def test_a_query_exactly_at_the_limit_is_kept_whole(self):
        whole = f"(from:aaa OR from:bbb) since:{SINCE} until:{UNTIL}"
        self.assertEqual(
            build_twitter_queries(['aaa', 'bbb'], SINCE, UNTIL, max_chars=len(whole)), [whole])
        self.assertEqual(
            len(build_twitter_queries(['aaa', 'bbb'], SINCE, UNTIL, max_chars=len(whole) - 1)), 2)

    def test_invalid_handles_are_dropped_with_a_warning(self):
        with self.assertLogs(social_mod.logger, level='WARNING') as logs:
            queries = build_twitter_queries(['@good_one', 'SebastianRaschka', 'bad-dash'], SINCE, UNTIL)
        self.assertEqual(queries, [f"(from:good_one) since:{SINCE} until:{UNTIL}"])
        self.assertEqual(sum('Skipping invalid Twitter handle' in l for l in logs.output), 2)

    def test_no_accounts_no_queries(self):
        self.assertEqual(build_twitter_queries([], SINCE, UNTIL), [])


class TwitterSearchSendsBoundedQueriesTests(unittest.TestCase):
    def test_every_request_is_within_the_limit_and_covers_every_account(self):
        accounts = _configured_accounts()
        with TemporaryDirectory() as temp:
            for name, rows in (('twitter_accounts.txt', accounts),
                               ('bluesky_accounts.txt', ['# none']),
                               ('mastodon_accounts.txt', ['# none'])):
                with open(os.path.join(temp, name), 'w') as handle:
                    handle.write('\n'.join(rows) + '\n')
            g = SocialGatherer(config_dir=temp, data_dir=temp, target_date='2026-11-15')
            get = Mock()
            get.return_value.json.return_value = {'tweets': []}
            with patch.object(social_mod, 'TWITTERAPI_IO_KEY', 'key'), \
                    patch.object(social_mod.requests, 'get', get), \
                    patch.object(social_mod.time, 'sleep'):
                g._twitter_search(g.twitter_users)
        queries = [call.kwargs['params']['query'] for call in get.call_args_list]
        self.assertTrue(queries)
        for query in queries:
            self.assertLessEqual(len(query), TWITTER_QUERY_MAX_CHARS, query)
        self.assertEqual(sorted(h for q in queries for h in _handles_in(q)),
                         sorted(a.lstrip('@') for a in accounts))
        self.assertNotIn(g.collection_status['twitter']['status'], ('failed', 'partial'))


if __name__ == '__main__':
    unittest.main()
