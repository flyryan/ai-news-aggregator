#!/usr/bin/env python3
"""Manual freshness-policy checks for stale release and old-anchor handling.

This is intentionally narrow and does not run the collection pipeline.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.base import AnalyzedItem, CollectedItem
from agents.link_enricher import LinkEnricher
from agents.staleness_checker import StalenessChecker


def item(item_id, title, summary, content="", score=80, source="Fixture"):
    return AnalyzedItem(
        item=CollectedItem(
            id=item_id,
            title=title,
            content=content or summary,
            url=f"https://example.com/{item_id}",
            author="",
            published="2026-05-26T12:00:00",
            source=source,
            source_type="fixture",
            metadata={},
        ),
        summary=summary,
        importance_score=score,
        reasoning="fixture",
        themes=[],
    )


def assert_freshness(analyzed_item, status):
    freshness = analyzed_item.item.metadata.get("freshness", {})
    assert freshness.get("status") == status, freshness
    assert freshness.get("exclude_from_top") is True, freshness
    assert freshness.get("exclude_from_summaries") is True, freshness


async def main():
    checker = StalenessChecker(
        config_dir=str(REPO_ROOT / "config"),
        target_date="2026-05-27",
        web_dir=str(REPO_ROOT / "web"),
    )

    gemini = item(
        "gemini",
        "Gemini Embedding 2: A Native Multimodal Embedding Model from Gemini",
        "Google introduces Gemini Embedding 2, a native multimodal embedding model.",
        score=85,
    )
    await checker.process_items("research", [gemini])
    assert_freshness(gemini, "stale_release")
    assert gemini.importance_score == 40.0

    uber = item(
        "uber",
        "Uber president says AI spending is getting harder to justify",
        "Uber exhausted its annual AI budget in four months and is struggling to connect Claude Code token spend to shipped features.",
        "After reportedly exhausting its annual AI budget just four months into 2026, Uber is questioning whether it is seeing returns.",
        score=75,
    )
    checker._historical_anchor_items = [{
        "id": "old-uber",
        "date": "2026-05-22",
        "category": "reddit",
        "title": "Tokens",
        "summary": "Discussion of token economics including Uber burning through AI budget in four months.",
        "source": "r/artificial",
        "url": "https://example.com/old-uber",
        "terms": checker._anchor_terms("Uber burning through AI budget in four months Claude Code token economics"),
    }]
    await checker.process_items("news", [uber])
    assert_freshness(uber, "stale_anchor")
    assert uber.importance_score == 55.0

    fresh = item(
        "fresh-followup",
        "Vendor announces binding AI cost controls after earlier budget blowout",
        "After earlier budget overruns, Vendor announced a new policy requiring per-team AI cost caps and quarterly ROI reporting.",
        score=75,
    )
    checker._historical_anchor_items = [{
        "id": "old-vendor",
        "date": "2026-05-10",
        "category": "news",
        "title": "Vendor AI budget overruns raise questions",
        "summary": "Vendor struggled with AI budget overruns and unclear ROI.",
        "source": "Fixture",
        "url": "https://example.com/old-vendor",
        "terms": checker._anchor_terms("Vendor AI budget overruns unclear ROI"),
    }]
    await checker.process_items("news", [fresh])
    assert fresh.item.metadata.get("freshness") is None, fresh.item.metadata

    enricher = LinkEnricher(None, "2026-05-27")
    visible = item("visible", "Visible fresh item", "Fresh item", score=80)
    report = SimpleNamespace(all_items=[uber, visible], top_items=[visible])
    link_items = enricher._build_item_list({"news": report})
    assert [entry["id"] for entry in link_items] == ["visible"], link_items

    print("Freshness policy checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
