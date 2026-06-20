#!/usr/bin/env python3
"""
Surgical resume: regenerate ONLY the research category_summary for a given date.

The 2026-06-03 daily-pipeline run analyzed all research items and ranked the
top 10 successfully, but the reduce-phase ranking response contained a raw
control character that strict json.loads rejected, so category_summary fell
back to the hardcoded placeholder "Analysis complete. Top items selected by
score." This script re-runs ONLY that one failed step (faithfully reusing the
real ResearchAnalyzer ranking prompt + LLM config + now-fixed parser) and
writes the real summary back into research.json and summary.json. No
re-gathering, no re-analysis, no other categories touched.

Usage: python scripts/resume_research_summary.py YYYY-MM-DD
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date as _date
from agents.base import AnalyzedItem, CollectedItem, CategoryTheme
from agents.analyzers.research_analyzer import ResearchAnalyzer
from agents.config import load_config
from agents.config.prompts import PromptAccessor, load_prompts
from agents.llm_client import AsyncAnthropicClient
from agents.ecosystem_context import EcosystemContextManager
from agents.link_enricher import LinkEnricher
from generators.json_generator import JSONGenerator


def _build_grounding_context(config_dir: str, target_date: str) -> str:
    """Rebuild the same ecosystem grounding system prompt offline from the
    committed config/ecosystem_context.yaml cache + curated releases. This
    matches the system_chars the original pipeline run injected, without
    hitting OpenRouter."""
    try:
        m = EcosystemContextManager(config_dir=config_dir)
        y, mo, d = (int(x) for x in target_date.split('-'))
        m.report_date = _date(y, mo, d)
        m.releases = m._load_releases()
        cached = m._load_cache()
        if m._is_valid_context(cached):
            m.context = m._merge_cache_with_curated(cached)
        else:
            m.context = m._curated_to_context()
        return m._build_system_prompt()
    except Exception as exc:
        print(f"WARNING: could not rebuild grounding context ({exc}); proceeding without it")
        return None


def _to_list(v):
    """Published JSON sometimes stores list fields as their str() repr."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith('[') and s.endswith(']'):
            try:
                import ast
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
        return [s] if s else []
    return []


def _analyzed_from_published(rec: dict) -> AnalyzedItem:
    ci = CollectedItem(
        id=rec.get('id', ''),
        title=rec.get('title', ''),
        content=rec.get('content', ''),
        url=rec.get('url', ''),
        author=rec.get('author', ''),
        published=rec.get('published', ''),
        source=rec.get('source', ''),
        source_type=rec.get('source_type', ''),
        tags=_to_list(rec.get('tags', [])),
    )
    try:
        score = float(rec.get('importance_score', 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    return AnalyzedItem(
        item=ci,
        summary=rec.get('summary', '') or '',
        importance_score=score,
        reasoning=rec.get('reasoning', '') or '',
        themes=_to_list(rec.get('themes', [])),
    )


async def resume(target_date: str, web_dir: str = './web', config_dir: str = './config',
                 force: bool = False) -> bool:
    research_path = os.path.join(web_dir, 'data', target_date, 'research.json')
    summary_path = os.path.join(web_dir, 'data', target_date, 'summary.json')
    if not os.path.exists(research_path):
        print(f"ERROR: {research_path} not found")
        return False

    with open(research_path, encoding='utf-8') as f:
        research = json.load(f)

    items = [_analyzed_from_published(r) for r in research.get('items', [])]
    themes = [CategoryTheme.from_dict(t) for t in research.get('themes', [])]
    print(f"Loaded {len(items)} analyzed items, {len(themes)} themes for {target_date}")

    if not items:
        print("ERROR: no items to rank")
        return False

    provider_config = load_config(config_dir)
    async_client = AsyncAnthropicClient.from_config(provider_config.llm)
    prompt_config = load_prompts(config_dir)
    prompt_accessor = PromptAccessor(prompt_config)

    PLACEHOLDER = "Analysis complete. Top items selected by score."
    current_summary = (research.get('category_summary') or '').strip()

    if current_summary and current_summary != PLACEHOLDER and not force:
        # Summary is already real (e.g. we previously regenerated it). Skip the
        # ranking LLM call entirely and just (re)run link enrichment on it.
        # NOTE: a truncated summary is also "non-placeholder" -- pass --force to
        # regenerate one that was cut off mid-generation.
        print("Existing research summary is real (not placeholder); skipping regeneration, enriching only.")
        print("       (pass --force to regenerate anyway, e.g. when the summary was truncated)")
        new_summary = current_summary
    else:
        grounding_context = _build_grounding_context(config_dir, target_date)
        print(f"Grounding context: {len(grounding_context) if grounding_context else 0} chars")

        analyzer = ResearchAnalyzer(
            async_client=async_client,
            config_dir=config_dir,
            target_date=target_date,
            web_dir=web_dir,
            grounding_context=grounding_context,
            prompt_accessor=prompt_accessor,
        )

        # Reproduce the reduce-phase ranking exactly: top 50 eligible candidates
        # by current order (already score-sorted in published JSON), build the
        # same ranking context, call the same prompt, parse with fixed parser.
        eligible = [it for it in items if not analyzer._exclude_from_top(it)]
        top_candidates = eligible[:50]
        print(f"Ranking {len(top_candidates)} candidates...")

        ranking_context = analyzer._build_ranking_context(top_candidates, themes)
        ranking_prompt = analyzer._get_ranking_prompt(ranking_context)

        response = await async_client.call_with_thinking(
            messages=[{"role": "user", "content": ranking_prompt}],
            system=analyzer.grounding_context,
            profile=analyzer.thinking_budget,
            caller="research_analyzer.resume_reduce_rank",
            # Match the pipeline reduce path: full output ceiling at max effort
            # so the regeneration itself cannot re-truncate.
            full_output_budget=True,
        )
        if response.stop_reason == "max_tokens":
            print("WARNING: regeneration response truncated at max_tokens even after escalation")

        result = analyzer._parse_json_response(response.content)
        new_summary = (result.get('category_summary') or '').strip()
        if not new_summary or new_summary == PLACEHOLDER:
            print("ERROR: regeneration still produced empty/placeholder summary")
            print("Raw response (first 600 chars):", repr(response.content[:600]))
            return False

    print(f"\n=== NEW RESEARCH SUMMARY ({len(new_summary)} chars) ===\n{new_summary}\n")

    # Phase 4.5 (link enrichment), research-only: inject internal /?date=...#item-...
    # links the same way the pipeline does. Build a single-category report dict
    # from the published items so the enricher can map title->id. Category
    # summaries only link to items from their own category, so research-only is
    # faithful and leaves other categories untouched.
    import re as _re
    research_report = {
        'category': 'research',
        'category_summary': new_summary,
        # enricher prefers all_items (id/title/summary dicts work directly)
        'all_items': research.get('items', []),
        'top_items': research.get('items', [])[:10],
    }
    enricher = LinkEnricher(async_client, target_date, prompt_accessor=prompt_accessor)
    try:
        _exec, enriched_categories, _topics = await enricher.enrich_all(
            '', {'research': research_report}, []
        )
    finally:
        pass
    enriched_summary = (enriched_categories.get('research') or '').strip()
    link_count = len(_re.findall(r'\]\(/\?date=', enriched_summary)) if enriched_summary else 0
    if enriched_summary and link_count > 0:
        print(f"Link enrichment added {link_count} internal links")
        new_summary = enriched_summary
    else:
        print(f"WARNING: link enrichment produced {link_count} links; keeping unenriched summary")
    print(f"\n=== ENRICHED RESEARCH SUMMARY ===\n{new_summary}\n")

    jg = JSONGenerator(output_dir=web_dir)
    new_html = jg._markdown_to_html(new_summary)

    # Backup
    backup = research_path.replace('.json', f'.summary-backup-{datetime.now():%Y%m%d-%H%M%S}.json')
    with open(backup, 'w', encoding='utf-8') as f:
        json.dump({
            'old_category_summary': research.get('category_summary'),
            'old_category_summary_html': research.get('category_summary_html'),
            'backed_up_at': datetime.now().isoformat(),
        }, f, indent=2, ensure_ascii=False)
    print(f"Backed up old summary -> {backup}")

    # Patch research.json
    research['category_summary'] = new_summary
    research['category_summary_html'] = new_html
    with open(research_path, 'w', encoding='utf-8') as f:
        json.dump(research, f, indent=2, ensure_ascii=False)
    print(f"Updated {research_path}")

    # Patch summary.json research section
    if os.path.exists(summary_path):
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        cats = summary.get('categories', {})
        if 'research' in cats:
            cats['research']['category_summary'] = new_summary
            cats['research']['category_summary_html'] = new_html
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"Updated {summary_path} (research section)")
        else:
            print(f"WARNING: no 'research' section in {summary_path}")
    else:
        print(f"WARNING: {summary_path} not found")

    return True


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--force']
    force = '--force' in sys.argv[1:]
    if not args:
        print("Usage: python scripts/resume_research_summary.py YYYY-MM-DD [--force]")
        sys.exit(1)
    date = args[0]
    datetime.strptime(date, '%Y-%m-%d')
    ok = asyncio.run(resume(date, force=force))
    sys.exit(0 if ok else 1)
