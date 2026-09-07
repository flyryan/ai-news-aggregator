#!/usr/bin/env python3
"""Stage a checkpoint-based analysis repair without recollecting or publishing.

Default is an offline plan. --execute makes paid LLM calls and writes a separate
web directory for review. Existing publication, hero and original replay remain
untouched. Repeating the command reuses validated map-batch checkpoints.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import date
import json
import logging
import re
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def strip_internal_links(text):
    """New synthesis may copy links from old category context; re-enrich it fully."""
    return re.sub(r'\[([^\]]+)\]\(/\?date=[^)]*\)', r'\1', text)


def prepare_plan(args):
    original = read_json(args.result)
    gathering = read_json(args.gathering)
    report_date = original['date']
    date.fromisoformat(report_date)
    categories = args.categories.split(',')
    if any(c not in ('research', 'social', 'reddit') for c in categories):
        raise ValueError('Supported repair categories: research,social,reddit')
    counts = {}
    for category in categories:
        sources = gathering['categories'][category]
        ids = [s['id'] for s in sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f'{category}: duplicate source IDs')
        published = original['category_reports'][category]['all_items']
        if {p['id'] for p in published} != set(ids):
            raise ValueError(f'{category}: gathering and report source sets differ')
        by_id = {s['id']: s for s in sources}
        for item in published:
            for field in ('title', 'content', 'url'):
                if item[field] != by_id[item['id']][field]:
                    raise ValueError(f'{category}: original source {field} differs for {item["id"]}')
        counts[category] = {
            'sources': len(sources),
            'unanalyzed_placeholders': sum(p.get('reasoning') == 'Not analyzed (batch processing)' for p in published),
        }
    output, publication = args.output.resolve(), args.web_dir.resolve()
    if output.is_relative_to(publication) or publication.is_relative_to(output):
        raise ValueError('Repair output must be separate from the published web directory')
    print(json.dumps({
        'date': report_date, 'categories': counts, 'output': str(args.output),
        'regenerate': ['selected category analyses and rankings', 'cross-category topics',
                       'executive summary', 'internal links', 'feeds', 'search corpus'],
        'preserve': ['collected sources', 'other category reports', 'hero image', 'original run replay'],
        'execute': args.execute,
    }, indent=2))
    return original, gathering, categories


async def execute(args, original, gathering, categories):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from agents.base import CategoryReport, CollectedItem, AnalysisIntegrityError
    from agents.config import load_config
    from agents.config.prompts import PromptAccessor, load_prompts
    from agents.cost_tracker import get_tracker, reset_tracker
    from agents.orchestrator import MainOrchestrator
    from agents.link_enricher import LinkEnricher
    from agents.replay_recorder import get_recorder
    from generators.json_generator import JSONGenerator
    from generators.feed_generator import FeedGenerator
    from generators.search_indexer import SearchIndexer
    from validate_report import validate

    config = load_config(str(args.config_dir))
    reset_tracker(model=config.llm.model)
    prompts = PromptAccessor(load_prompts(str(args.config_dir)))
    report_date = original['date']
    work = args.output.parent / (args.output.name + '-work')
    # Refuse to blend outputs from different original reports or repair selections.
    import hashlib
    signature = hashlib.sha256(json.dumps([original, gathering, categories], sort_keys=True).encode()).hexdigest()
    manifest = work / 'input.json'
    if manifest.exists() and read_json(manifest).get('sha256') != signature:
        raise ValueError('Repair inputs changed; choose a new output directory')
    if not manifest.exists() and args.output.exists():
        raise ValueError('Output already exists without a matching repair manifest')
    write_json(manifest, {'sha256': signature, 'date': report_date, 'categories': categories})
    # Refresh the staging baseline on each attempt; validated analysis units live
    # outside it. Nothing copies back to the publication in this script.
    shutil.copytree(args.web_dir / 'data', args.output / 'data', dirs_exist_ok=True)
    orchestrator = MainOrchestrator(
        config_dir=str(args.config_dir), data_dir=str(work), web_dir=str(args.output),
        target_date=report_date, provider_config=config, prompt_accessor=prompts,
    )
    # Use the saved ecosystem context; do not fetch new discoveries or rewrite
    # curated release files during a historical repair.
    manager = orchestrator.ecosystem_manager
    manager.report_date = date.fromisoformat(report_date)
    manager.releases = manager._load_releases()
    cached = manager._load_cache()
    manager.context = manager._merge_cache_with_curated(cached) if manager._is_valid_context(cached) else manager._curated_to_context()
    orchestrator.grounding_context = manager._build_system_prompt()

    class CompleteClient:
        """Reject clipped outputs even when an upstream phase catches errors."""
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.terminal_error = None
        def __getattr__(self, key):
            return getattr(self.wrapped, key)
        async def call_with_thinking(self, **kwargs):
            try:
                response = await self.wrapped.call_with_thinking(**kwargs)
            except Exception as exc:
                from agents.llm_client import _transient_retry_reason
                if _transient_retry_reason(exc) is None:
                    self.terminal_error = AnalysisIntegrityError(
                        f'Repair request requires intervention: {type(exc).__name__}'
                    )
                raise
            if response.stop_reason == 'max_tokens':
                from agents.base import TruncatedJSONError
                raise TruncatedJSONError('Repair output was truncated')
            return response

    orchestrator.async_client = CompleteClient(orchestrator.async_client)

    async def require_result(label, operation, acceptable):
        attempt = 0
        while True:
            attempt += 1
            value = await operation()
            if orchestrator.async_client.terminal_error:
                raise orchestrator.async_client.terminal_error
            if acceptable(value):
                return value
            if args.max_attempts and attempt >= args.max_attempts:
                raise AnalysisIntegrityError(f'{label}: repair did not produce a complete result')
            delay = min(60, 5 * 2 ** min(attempt - 1, 6))
            logging.warning('%s incomplete; retrying in %ss', label, delay)
            await asyncio.sleep(delay)

    reports = {c: CategoryReport.from_dict(r) for c, r in original['category_reports'].items()}
    try:
        for category in categories:
            analyzer = orchestrator.analyzers[category]
            analyzer.async_client = orchestrator.async_client
            analyzer.grounding_context = orchestrator.grounding_context
            sources = [CollectedItem.from_dict(s) for s in gathering['categories'][category]]
            reports[category] = await require_result(
                category, lambda: analyzer.analyze(sources),
                lambda r: not r.degradations and len(r.all_items) == len(sources),
            )
            write_json(work / f'{category}-repaired.json', reports[category].to_dict())
        topics, _ = await require_result(
            'topics', lambda: orchestrator._detect_cross_category_topics(reports),
            lambda result: bool(result[0]) and not result[1].startswith('Error:'),
        )
        executive, _ = await require_result(
            'executive summary', lambda: orchestrator._generate_executive_summary(reports, topics),
            lambda result: len(result[0]) >= 400 and not result[1].startswith('Error:'),
        )
        enricher = LinkEnricher(orchestrator.async_client, report_date, prompts)
        executive = strip_internal_links(executive)
        for category in categories:
            reports[category].category_summary = strip_internal_links(reports[category].category_summary)
        for topic in topics:
            topic.description = strip_internal_links(topic.description)
        # Passing copies makes retries idempotent: enrichment mutates topics.
        executive, enriched_categories, topics = await require_result(
            'link enrichment', lambda: enricher.enrich_all(executive, deepcopy(reports), deepcopy(topics), only_unlinked=True),
            lambda result: not enricher.degradations,
        )
        for category, text in enriched_categories.items():
            if category in categories:
                reports[category].category_summary = text
        repaired = deepcopy(original)
        repaired['category_reports'] = {c: r.to_dict() for c, r in reports.items()}
        from dataclasses import asdict
        repaired['top_topics'] = [asdict(topic) for topic in topics]
        repaired['executive_summary'] = executive
        repaired['total_items_analyzed'] = sum(len(r.all_items) for r in reports.values())
        write_json(work / 'orchestrator_result_repaired.json', repaired)
        old_summary = read_json(args.web_dir / 'data' / report_date / 'summary.json')
        generator = JSONGenerator(str(args.output), **{k: old_summary.get(k) for k in
            ('llm_model', 'llm_model_display', 'image_model', 'image_model_display')})
        generator.generate_from_orchestrator_result(repaired)
        # Preserve other category JSON byte for byte, including their timestamps.
        for category in set(reports) - set(categories):
            shutil.copy2(args.web_dir / 'data' / report_date / f'{category}.json',
                         args.output / 'data' / report_date / f'{category}.json')
        FeedGenerator(str(args.output), rolling_window_days=7,
                      base_url=config.get_pipeline_config().base_url).generate_feeds()
        # Rebuilding all feeds also changes their generation timestamps. Retain
        # unrelated feeds byte for byte so only affected subscriptions change.
        for feed in (args.web_dir / 'data' / 'feeds').glob('*.xml'):
            affected = feed.name in ('main.xml', 'summaries.xml', 'summaries-executive.xml')
            affected = affected or any(
                feed.name in (f'{category}.xml', f'summaries-{category}.xml')
                or feed.name.startswith(f'{category}-') for category in categories
            )
            if not affected:
                shutil.copy2(feed, args.output / 'data' / 'feeds' / feed.name)
        SearchIndexer(str(args.output), rolling_window_days=30).update_index()
        verdict = validate(read_json(args.output / 'data' / report_date / 'summary.json'), report_date)
        write_json(work / 'validation.json', verdict)
        if not verdict['valid']:
            raise AnalysisIntegrityError('; '.join(verdict['failures']))
        print(f'Repair staged for review: {args.output}. No publication was changed.')
    finally:
        write_json(work / 'repair-cost.json', get_tracker().get_json_report())
        write_json(work / 'repair-replay.json', get_recorder().snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--gathering', type=Path, required=True)
    parser.add_argument('--categories', default='research')
    parser.add_argument('--web-dir', type=Path, default=ROOT / 'web')
    parser.add_argument('--config-dir', type=Path, default=ROOT / 'config')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Run paid reanalysis into the staging output')
    parser.add_argument('--max-attempts', type=int, default=0, help='0 retries incomplete repair phases until successful')
    args = parser.parse_args()
    if args.max_attempts < 0:
        parser.error('--max-attempts must be nonnegative')
    original, gathering, categories = prepare_plan(args)
    if args.execute:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        asyncio.run(execute(args, original, gathering, categories))


if __name__ == '__main__':
    main()
