"""Source association, persistent recovery and publication-stop regression cases.

Run explicitly: venv/bin/python -m unittest tests.analysis_identity_test -v
No network or paid LLM calls: responses are injected through AsyncMock.
"""
import asyncio
from copy import deepcopy
import json
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agents.base import AnalysisIntegrityError, BatchResult, CollectedItem
from agents.analyzers.research_analyzer import ResearchAnalyzer
from scripts.repair_analysis import strip_internal_links


def source(item_id, title):
    return CollectedItem(id=item_id, title=title, content=f'Abstract for {title}',
                         url=f'https://example.com/{item_id}', author='Author',
                         published='2026-09-07', source='arXiv', source_type='arxiv')


def analysis(item):
    return {'id': item.id, 'source_title': item.title, 'summary': f'{item.title} finding.',
            'importance_score': 80, 'reasoning': 'A substantive research result.', 'themes': ['Research']}


def response(rows, stop_reason='end_turn'):
    return SimpleNamespace(content=json.dumps({'items': rows}), thinking='', stop_reason=stop_reason)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = SimpleNamespace(model='mock', call_with_thinking=AsyncMock())
        self.analyzer = ResearchAnalyzer(async_client=self.client, data_dir=self.temp.name,
                                         target_date='2026-09-07')
        self.items = [source('7cadf1af16b3', 'MaxKernel: Agentic Kernel Generation for TPUs'),
                      source('b32b8cb0daf0', 'When Financial Fine-tuning Fails')]

    def test_order_is_irrelevant_but_identity_is_exact(self):
        rows = [analysis(item) for item in reversed(self.items)]
        clean = self.analyzer._validate_batch_identity({'items': rows}, self.items)
        self.assertEqual(clean['items'], rows)

    def test_new_synthesis_gets_full_enrichment_even_if_it_copied_a_link(self):
        text = '**Harbor** [unifies benchmarks](/?date=2026-09-07&category=research#item-123). [Source](https://example.com).'
        self.assertEqual(strip_internal_links(text), '**Harbor** unifies benchmarks. [Source](https://example.com).')

    def test_rejects_missing_foreign_duplicate_and_swapped_ids(self):
        valid = [analysis(item) for item in self.items]
        bad_cases = [valid[:1], valid + [valid[0]], [valid[0], valid[0]]]
        foreign = deepcopy(valid)
        foreign[1]['id'] = 'otherbatch01'
        bad_cases.append(foreign)
        swapped = deepcopy(valid)
        swapped[0]['source_title'], swapped[1]['source_title'] = swapped[1]['source_title'], swapped[0]['source_title']
        bad_cases.append(swapped)
        bad_cases.extend([{'items': 'wrong shape'}, []])
        for rows in bad_cases:
            with self.subTest(rows=rows), self.assertRaises(AnalysisIntegrityError):
                self.analyzer._validate_batch_identity({'items': rows}, self.items)

    def test_feed_title_padding_does_not_force_a_retry(self):
        items = deepcopy(self.items)
        items[0].title += ' '
        self.analyzer._validate_batch_identity({'items': [analysis(i) for i in self.items]}, items)

    async def test_invalid_batch_splits_and_keeps_both_sources(self):
        wrong = [analysis(self.items[0]), analysis(self.items[0])]
        self.client.call_with_thinking.side_effect = [response(wrong)] + [response([analysis(i)]) for i in self.items]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual([r['id'] for r in result.item_analyses], [i.id for i in self.items])
        self.assertFalse(result.failed)
        self.assertEqual(self.client.call_with_thinking.await_count, 3)

    async def test_single_source_retries_empty_and_truncated_results(self):
        self.client.call_with_thinking.side_effect = [response([]), response([], 'max_tokens'), response([analysis(self.items[0])])]
        with patch('agents.base.asyncio.sleep', new_callable=AsyncMock) as sleep:
            result = await self.analyzer._analyze_batch(self.items[:1], 0, 1)
        self.assertEqual(result.item_analyses[0]['id'], self.items[0].id)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [5, 10])

    async def test_explicit_attempt_limit_raises_instead_of_dropping(self):
        self.client.call_with_thinking.return_value = response([])
        with patch.dict(os.environ, {'ANALYZER_RESULT_MAX_ATTEMPTS': '2'}), patch('agents.base.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(AnalysisIntegrityError):
                await self.analyzer._analyze_batch(self.items[:1], 0, 1)
        self.assertEqual(self.client.call_with_thinking.await_count, 2)

    def test_encoding_and_typography_are_not_identity_changes(self):
        item = source('abc', 'An AI’s Q&amp;A:  5 models')
        row = analysis(item)
        row['source_title'] = "An AI's Q&A: 5 models"
        self.analyzer._validate_batch_identity({'items': [row]}, [item])
        row['source_title'] = "An AI's Q&A: 6 models"
        with self.assertRaises(AnalysisIntegrityError):
            self.analyzer._validate_batch_identity({'items': [row]}, [item])

    async def test_recovery_only_requests_the_unresolved_source(self):
        wrong = [analysis(i) for i in self.items]
        wrong[1]['source_title'] = 'rewritten title'
        self.client.call_with_thinking.side_effect = [response(wrong), response([wrong[1]])]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual(len(result.item_analyses), 2)
        self.assertEqual(self.client.call_with_thinking.await_count, 2)
        request = self.client.call_with_thinking.await_args_list[1].kwargs['messages'][0]['content']
        self.assertNotIn(self.items[0].id, request)
        self.assertIn(self.items[1].id, request)
        self.assertEqual(result.item_analyses[1]['source_title'], self.items[1].title)

    async def test_default_retry_limit_and_partial_cache_survive_failure(self):
        valid = analysis(self.items[0])
        self.client.call_with_thinking.side_effect = [response([valid])] + [response([])] * 3
        with patch('agents.base.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(AnalysisIntegrityError):
                await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual(self.client.call_with_thinking.await_count, 4)
        self.client.call_with_thinking.reset_mock()
        self.client.call_with_thinking.side_effect = [response([analysis(self.items[1])])]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual(len(result.item_analyses), 2)
        self.assertEqual(self.client.call_with_thinking.await_count, 1)

    async def test_cancellation_is_never_retried(self):
        self.client.call_with_thinking.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.analyzer._analyze_batch(self.items[:1], 0, 1)
        self.assertEqual(self.client.call_with_thinking.await_count, 1)

    async def test_completed_batch_survives_restart_and_source_change_invalidates_it(self):
        self.client.call_with_thinking.return_value = response([analysis(i) for i in self.items])
        await self.analyzer._map_phase(self.items)
        restarted = ResearchAnalyzer(async_client=self.client, data_dir=self.temp.name, target_date='2026-09-07')
        cached, _ = await restarted._map_phase(self.items)
        self.assertEqual(self.client.call_with_thinking.await_count, 1)
        self.assertEqual(len(cached[0].item_analyses), 2)
        updated = deepcopy(self.items)
        updated[0].content += ' New evidence.'
        await restarted._map_phase(updated)
        self.assertEqual(self.client.call_with_thinking.await_count, 2)

    def test_merge_never_overwrites_another_batch_or_fills_missing_analysis(self):
        row = analysis(self.items[0])
        batches = [BatchResult(batch_index=0, item_analyses=[row], batch_themes=[], cross_signals=[])]
        with self.assertRaises(AnalysisIntegrityError):
            self.analyzer._merge_batch_results(batches, self.items)
        with self.assertRaises(AnalysisIntegrityError):
            self.analyzer._merge_batch_results(batches * 2, self.items[:1])


if __name__ == '__main__':
    unittest.main()
