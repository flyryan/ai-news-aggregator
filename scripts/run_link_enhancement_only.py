#!/usr/bin/env python3
"""
Run only the link-enrichment post-processing stage for an existing report date.

This intentionally reuses existing checkpoints/artifacts and does NOT run
collection, analysis, topic detection, executive-summary generation,
ecosystem enrichment, hero generation, feed generation, search indexing, git
commit, or deploy/push steps.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

# Project root on sys.path when invoked as scripts/run_link_enhancement_only.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from agents.base import CategoryReport  # noqa: E402
from agents.config import load_config  # noqa: E402
from agents.config.prompts import PromptAccessor, load_prompts  # noqa: E402
from agents.link_enricher import LinkEnricher  # noqa: E402
from agents.llm_client import AsyncAnthropicClient  # noqa: E402
from agents.orchestrator import TopTopic  # noqa: E402
from generators.json_generator import JSONGenerator  # noqa: E402

logger = logging.getLogger("link_enhancement_only")

INTERNAL_LINK_RE_TEMPLATE = r"\]\(/\?date={date}&category=[^)]+#item-[^)]+\)"


def parse_date(value: str) -> str:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def backup_file(path: Path, backup_dir: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        relative_path = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        relative_path = Path(path.name)
    target = backup_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def restore_top_topics(topics_data: list[dict[str, Any]]) -> list[TopTopic]:
    return [
        TopTopic(
            name=t.get("name", ""),
            description=t.get("description", ""),
            description_html=t.get("description_html", ""),
            category_breakdown=t.get("category_breakdown", {}),
            representative_items=t.get("representative_items", []),
            importance=t.get("importance", 50),
        )
        for t in topics_data
    ]


def count_internal_links(summary: Dict[str, Any], date: str) -> Dict[str, int]:
    pattern = re.compile(INTERNAL_LINK_RE_TEMPLATE.format(date=re.escape(date)))
    counts: Dict[str, int] = {
        "executive_summary": len(pattern.findall(summary.get("executive_summary", ""))),
        "top_topics": 0,
        "category_summaries": 0,
    }
    for topic in summary.get("top_topics", []):
        counts["top_topics"] += len(pattern.findall(topic.get("description", "")))
    for report in summary.get("categories", {}).values():
        counts["category_summaries"] += len(pattern.findall(report.get("category_summary", "")))
    counts["total"] = sum(counts.values())
    return counts


def build_result(
    date: str,
    category_reports: dict[str, CategoryReport],
    executive_summary: str,
    top_topics: list[TopTopic],
    existing_result: dict[str, Any],
    existing_summary: dict[str, Any],
) -> dict[str, Any]:
    total_collected = existing_summary.get("total_items_collected")
    if total_collected is None:
        total_collected = sum(r.total_collected for r in category_reports.values())

    total_analyzed = existing_summary.get("total_items_analyzed")
    if total_analyzed is None:
        total_analyzed = sum(len(r.all_items) for r in category_reports.values())

    return {
        "date": date,
        "coverage_date": existing_summary.get("coverage_date", existing_result.get("coverage_date", "")),
        "coverage_start": existing_summary.get("coverage_start", existing_result.get("coverage_start", "")),
        "coverage_end": existing_summary.get("coverage_end", existing_result.get("coverage_end", "")),
        "executive_summary": executive_summary,
        "top_topics": [t.__dict__ for t in top_topics],
        "category_reports": {k: v.to_dict() for k, v in category_reports.items()},
        "total_items_collected": total_collected,
        "total_items_analyzed": total_analyzed,
        # Prefer the raw orchestrator collection_status. web summary.json stores
        # a display-formatted version that JSONGenerator cannot consume.
        "collection_status": existing_result.get("collection_status", existing_summary.get("collection_status", {})),
        "hero_image_url": existing_summary.get("hero_image_url", existing_result.get("hero_image_url")),
        "hero_image_prompt": existing_summary.get("hero_image_prompt", existing_result.get("hero_image_prompt")),
        "phase_status": existing_result.get("phase_status", []),
        "orchestrator_thinking": existing_result.get("orchestrator_thinking"),
        # Preserve generated_at so this does not masquerade as a full regenerated report.
        "generated_at": existing_summary.get("generated_at", existing_result.get("generated_at", datetime.now().isoformat())),
    }


async def run(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    config_dir = (project_root / args.config_dir).resolve()
    data_dir = (project_root / args.data_dir).resolve()
    web_dir = (project_root / args.web_dir).resolve()
    date = args.date

    summary_checkpoint_path = data_dir / "checkpoints" / date / "summary.json"
    analysis_checkpoint_path = data_dir / "checkpoints" / date / "analysis.json"
    web_summary_path = web_dir / "data" / date / "summary.json"
    result_path = data_dir / "processed" / f"orchestrator_result_{date}.json"

    for required in (summary_checkpoint_path, analysis_checkpoint_path, web_summary_path):
        if not required.exists():
            logger.error("Missing required artifact: %s", required)
            return 2

    existing_summary = read_json(web_summary_path)
    existing_result = read_json(result_path) if result_path.exists() else {}
    before_counts = count_internal_links(existing_summary, date)
    logger.info("Before internal-link counts: %s", before_counts)

    analysis_checkpoint = read_json(analysis_checkpoint_path)
    summary_checkpoint = read_json(summary_checkpoint_path)

    category_reports = {
        category: CategoryReport.from_dict(report)
        for category, report in analysis_checkpoint.get("category_reports", {}).items()
    }
    if not category_reports:
        logger.error("No category reports found in %s", analysis_checkpoint_path)
        return 2

    executive_summary = summary_checkpoint.get("executive_summary") or existing_summary.get("executive_summary", "")
    top_topics = restore_top_topics(
        summary_checkpoint.get("enriched_topics")
        or existing_summary.get("top_topics", [])
    )

    # Start from the original analysis checkpoint category summaries, not the
    # possibly failed/unenriched summary checkpoint, then overwrite with LLM results.
    provider_config = load_config(str(config_dir))
    prompt_config = load_prompts(str(config_dir))
    prompt_accessor = PromptAccessor(prompt_config)
    async_client = AsyncAnthropicClient.from_config(provider_config.llm)

    try:
        enricher = LinkEnricher(async_client, date, prompt_accessor=prompt_accessor)
        executive_summary, enriched_category_summaries, top_topics = await enricher.enrich_all(
            executive_summary, category_reports, top_topics
        )
    finally:
        await async_client.close()

    for category, enriched_summary in enriched_category_summaries.items():
        if category in category_reports:
            category_reports[category].category_summary = enriched_summary

    result = build_result(
        date,
        category_reports,
        executive_summary,
        top_topics,
        existing_result,
        existing_summary,
    )

    # Generate into temp dir first so we can verify before touching production artifacts.
    tmp_root = data_dir / "tmp" / f"link_enhancement_only_{date}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    tmp_web = tmp_root / "web"
    (tmp_web / "data").mkdir(parents=True, exist_ok=True)

    # Seed temp index so JSONGenerator can run normally. Production index.json is
    # intentionally left untouched; link enrichment does not change date/category counts.
    index_src = web_dir / "data" / "index.json"
    if index_src.exists():
        shutil.copy2(index_src, tmp_web / "data" / "index.json")

    JSONGenerator(str(tmp_web)).generate_from_orchestrator_result(result)
    new_summary = read_json(tmp_web / "data" / date / "summary.json")
    after_counts = count_internal_links(new_summary, date)
    logger.info("After internal-link counts: %s", after_counts)

    if after_counts["total"] <= before_counts["total"]:
        logger.error(
            "Link enhancement did not increase internal-link count (before=%s after=%s); leaving artifacts unchanged at %s",
            before_counts,
            after_counts,
            tmp_root,
        )
        return 3

    if args.dry_run:
        logger.info("Dry run complete; generated artifacts left at %s", tmp_root)
        return 0

    backup_dir = data_dir / "backups" / f"link_enhancement_only_{date}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    backed_up = [
        backup_file(web_summary_path, backup_dir),
        backup_file(summary_checkpoint_path, backup_dir),
    ]
    if result_path.exists():
        backed_up.append(backup_file(result_path, backup_dir))

    # Update only summary-bearing artifacts.
    shutil.copy2(tmp_web / "data" / date / "summary.json", web_summary_path)
    # Update category-page summary fields surgically. Do not rewrite items or
    # regenerate unrelated category content in the production artifacts.
    for category in category_reports:
        production_category_path = web_dir / "data" / date / f"{category}.json"
        generated_category_path = tmp_web / "data" / date / f"{category}.json"
        if production_category_path.exists() and generated_category_path.exists():
            backed_up.append(backup_file(production_category_path, backup_dir))
            production_category = read_json(production_category_path)
            generated_category = read_json(generated_category_path)
            production_category["category_summary"] = generated_category.get("category_summary", "")
            production_category["category_summary_html"] = generated_category.get("category_summary_html", "")
            write_json(production_category_path, production_category)
            logger.info("Updated category summary fields: %s", production_category_path)

    summary_checkpoint["executive_summary"] = executive_summary
    summary_checkpoint["enriched_category_summaries"] = {
        category: report.category_summary for category, report in category_reports.items()
    }
    summary_checkpoint["enriched_topics"] = [t.__dict__ for t in top_topics]
    write_json(summary_checkpoint_path, summary_checkpoint)
    write_json(result_path, result)

    logger.info("Updated: %s", web_summary_path)
    logger.info("Updated: %s", summary_checkpoint_path)
    logger.info("Updated: %s", result_path)
    logger.info("Backups: %s", ", ".join(str(p) for p in backed_up))
    logger.info("Generated temp artifacts: %s", tmp_root)
    logger.info("Internal-link counts: before=%s after=%s", before_counts, after_counts)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run only link enrichment for an existing AI News date")
    parser.add_argument("--date", "-d", required=True, type=parse_date, help="Report date YYYY-MM-DD")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help="Project root")
    parser.add_argument("--config-dir", default="config", help="Config dir relative to project root")
    parser.add_argument("--data-dir", default="data", help="Data dir relative to project root")
    parser.add_argument("--web-dir", default="web", help="Web dir relative to project root")
    parser.add_argument("--dry-run", action="store_true", help="Generate and verify temp artifacts without modifying outputs")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
