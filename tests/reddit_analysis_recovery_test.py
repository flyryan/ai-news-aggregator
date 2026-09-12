"""Reddit regression cases for the September 12 stream/identity incident."""
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.analyzers.reddit_analyzer import RedditAnalyzer
from agents.base import AnalysisRecoveryExhausted, CollectedItem
from agents.llm_client import OpenRouterStreamError
from tests import openai_chat_transport_test as transport_fixtures
from tests.openrouter_stream_error_retry_test import _retry_client


class RedditRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = SimpleNamespace(model='mock', call_with_thinking=AsyncMock())
        self.analyzer = RedditAnalyzer(async_client=self.client, data_dir=self.temp.name,
                                       target_date='2026-09-12')
        self.items = [CollectedItem(id=f'{i:012x}', title=title, content=title,
                     url=f'https://reddit.com/r/LocalLLaMA/comments/{i}',
                     author='tester', published='2026-09-11', source='r/LocalLLaMA',
                     source_type='reddit') for i, title in enumerate([
                         'This is why we need open-source harnesses + local models',
                         "old z640,2x p100's, no idea", 'Q&amp;A: local models'])]

    def rows(self):
        return [dict(id=i.id, source_title=i.title, summary=i.title,
                     importance_score=60, reasoning='Technical community discussion.',
                     themes=[]) for i in self.items]

    def response(self, rows):
        return SimpleNamespace(content=json.dumps({'items': rows}), thinking='', stop_reason='end_turn')

    async def test_title_typo_recovers_only_the_one_unresolved_post(self):
        rows = self.rows()
        rows[0]['source_title'] = 'Expanded title that was not supplied'
        rows[2]['source_title'] = 'Q&A: local models'
        self.client.call_with_thinking.side_effect = [self.response(rows), self.response(self.rows()[:1])]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual({r['id'] for r in result.item_analyses}, {i.id for i in self.items})
        self.assertEqual(self.client.call_with_thinking.await_count, 2)
        self.client.call_with_thinking.reset_mock()
        await self.analyzer._analyze_batch(self.items, 0, 1)
        self.client.call_with_thinking.assert_not_awaited()

    async def test_foreign_id_and_swapped_title_are_never_accepted(self):
        rows = self.rows()
        rows[0]['source_title'] = self.items[1].title
        rows[1]['id'] += 'x'
        self.client.call_with_thinking.side_effect = [self.response(rows),
            self.response(self.rows()[:2])]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual(sorted(result.item_analyses, key=lambda r: r['id']), self.rows())
        self.assertEqual(self.client.call_with_thinking.await_count, 2)

    async def test_duplicate_id_discards_both_claims_but_keeps_other_posts(self):
        rows = self.rows()
        rows[1]['id'] = rows[0]['id']
        self.client.call_with_thinking.side_effect = [self.response(rows), self.response(self.rows()[:2])]
        result = await self.analyzer._analyze_batch(self.items, 0, 1)
        self.assertEqual(sorted(result.item_analyses, key=lambda r: r['id']), self.rows())
        self.assertEqual(self.client.call_with_thinking.await_count, 2)

    async def test_transport_exhaustion_does_not_split_or_restart_retry_budget(self):
        self.client.call_with_thinking.side_effect = OpenRouterStreamError('upstream failed', 502)
        with patch.object(self.analyzer, '_handle_truncated_batch', new_callable=AsyncMock) as split:
            with self.assertRaises(AnalysisRecoveryExhausted):
                await self.analyzer._analyze_batch(self.items, 0, 1)
            split.assert_not_awaited()
        self.assertEqual(self.client.call_with_thinking.await_count, 1)

    async def test_error_finish_preserves_usage_and_retries_same_reddit_request(self):
        client = transport_fixtures.EndToEndSSEAssemblyTest()._client()
        client._mark_provider_alive = lambda: None
        progress = {}
        client._http_client = transport_fixtures._FakeHttpClient(['data: ' + json.dumps({
            'choices': [{'delta': {'content': '{"items": ['}, 'finish_reason': 'error'}],
            'usage': {'prompt_tokens': 123, 'completion_tokens': 45}}), 'data: [DONE]'])
        with self.assertRaises(OpenRouterStreamError) as caught:
            await client._stream_message_openai_chat(progress=progress, messages=[])
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(progress['input_tokens'], 123)
        self.assertEqual(progress['output_tokens'], 45)
        self.assertGreater(progress['text_chars'], 0)
        retry = _retry_client([caught.exception, self.response(self.rows())])
        result = await retry._create_message_with_retries(request_context={'caller': 'reddit_analyzer.batch_0'})
        self.assertEqual(retry.calls, 2)
        self.assertEqual(len(json.loads(result.content)['items']), 3)

    async def test_early_eof_is_transport_failure_even_with_parseable_json(self):
        client = transport_fixtures.EndToEndSSEAssemblyTest()._client()
        client._http_client = transport_fixtures._FakeHttpClient(['data: ' + json.dumps({
            'choices': [{'delta': {'content': '{"items": []}'}}]}), 'data: [DONE]'])
        with self.assertRaises(OpenRouterStreamError):
            await client._stream_message_openai_chat(messages=[])

    async def test_other_active_calls_cannot_reset_stream_error_budget(self):
        client = _retry_client([OpenRouterStreamError('stream failed', 502)] * 10)
        client._provider_alive_at = time.monotonic()
        with self.assertRaises(OpenRouterStreamError):
            await client._create_message_with_retries(request_context={'caller': 'reddit_analyzer.batch_0'})
        self.assertEqual(client.calls, 3)
