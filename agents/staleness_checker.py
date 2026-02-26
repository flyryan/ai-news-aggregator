"""
Staleness Checker — Post-Analysis Date Enforcement

Cross-references analyzed items against model_releases.yaml to detect
articles that report old model releases as new. Deterministic — no LLM
calls required.

Hooked into the orchestrator after Phase 2.5 (continuity detection).
"""

import logging
import re
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import CategoryReport, AnalyzedItem

logger = logging.getLogger(__name__)

# How many days after GA a model release is still considered "fresh"
FRESHNESS_WINDOW_DAYS = 3

# Score cap for articles whose primary subject is a stale release
STALE_SCORE_CAP = 40.0

# Minimum original score to bother checking (skip low-scoring items)
MIN_SCORE_THRESHOLD = 50.0


class StalenessChecker:
    """
    Checks analyzed items against known model release dates.

    If an article's primary subject is a model release whose GA date
    is older than FRESHNESS_WINDOW_DAYS relative to the coverage date,
    cap its importance score and annotate its summary.
    """

    def __init__(self, config_dir: str, target_date: str):
        """
        Args:
            config_dir: Path to config/ directory containing model_releases.yaml.
            target_date: Report date (YYYY-MM-DD). Coverage date = target_date - 1.
        """
        self.config_dir = Path(config_dir)
        self.target_date = target_date
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        self.coverage_date = (target_dt - timedelta(days=1)).date()
        self.cutoff_date = self.coverage_date - timedelta(days=FRESHNESS_WINDOW_DAYS)
        self.releases = self._load_releases()

    def _load_releases(self) -> Dict[str, Tuple[str, str]]:
        """
        Load model_releases.yaml into a lookup: normalised name -> (ga_date, provider).

        Returns dict like {"sonnet 4.6": ("2026-02-17", "anthropic"), ...}
        """
        releases_path = self.config_dir / "model_releases.yaml"
        if not releases_path.exists():
            logger.warning(f"model_releases.yaml not found at {releases_path}")
            return {}

        try:
            with open(releases_path, "r") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load model_releases.yaml: {e}")
            return {}

        lookup: Dict[str, Tuple[str, str]] = {}
        for provider, models in data.items():
            if not isinstance(models, dict):
                continue
            for model_name, dates in models.items():
                if not isinstance(dates, dict):
                    continue
                ga = dates.get("ga_date", "unknown")
                if ga == "unknown":
                    continue
                # Build multiple normalised forms for matching
                for variant in self._name_variants(model_name):
                    lookup[variant] = (ga, provider)

        logger.info(f"Staleness checker: loaded {len(lookup)} model name variants")
        return lookup

    def _name_variants(self, name: str) -> List[str]:
        """
        Generate normalised variants of a model name for fuzzy title matching.

        e.g. "Claude-Sonnet-4.6" -> ["claude sonnet 4.6", "sonnet 4.6", ...]
        """
        base = name.lower().strip()
        variants = set()

        # With spaces instead of dashes
        spaced = base.replace("-", " ").replace("_", " ")
        variants.add(spaced)

        # Short form: drop provider prefix (e.g. "Claude-" from "Claude-Sonnet-4.6")
        parts = base.split("-")
        if len(parts) >= 3:
            # e.g. ["claude", "sonnet", "4.6"] -> "sonnet 4.6"
            short = " ".join(parts[1:])
            variants.add(short)

        # Also try "model version" pattern: "sonnet 4.6", "opus 4.6", "gpt 5.2"
        version_match = re.search(r"(\d+\.?\d*)\s*$", spaced)
        if version_match:
            version = version_match.group(1)
            prefix = spaced[:version_match.start()].strip()
            # Last word + version: "sonnet 4.6"
            last_word = prefix.split()[-1] if prefix.split() else ""
            if last_word:
                variants.add(f"{last_word} {version}")

        return list(variants)

    def _find_stale_release_in_text(self, text: str) -> Optional[Tuple[str, str, str]]:
        """
        Check if text references a stale model release.

        Returns (model_name_matched, ga_date, provider) or None.
        """
        text_lower = text.lower()

        for variant, (ga_date, provider) in self.releases.items():
            if variant not in text_lower:
                continue

            # Check if the GA date is before our cutoff
            try:
                ga_dt = datetime.strptime(ga_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            if ga_dt <= self.cutoff_date:
                return (variant, ga_date, provider)

        return None

    def _is_primarily_about_release(self, item: AnalyzedItem, model_variant: str) -> bool:
        """
        Heuristic: is this article *primarily* about the model release?

        Checks title prominence — if the model name is in the title, it's a
        strong signal. Also checks if "release" / "launches" / "announces"
        language is present.
        """
        title_lower = item.item.title.lower()

        # Model name in the title is a strong signal
        if model_variant not in title_lower:
            return False

        # Check for release-oriented language in title or summary
        release_signals = [
            "release", "released", "launched", "launches", "announces",
            "rolls out", "introduces", "debuts", "ships", "now available",
            "just released", "new model", "model release",
        ]
        combined = (title_lower + " " + item.summary.lower())
        has_release_language = any(sig in combined for sig in release_signals)

        # If model is in title AND release language present -> primarily about release
        return has_release_language

    def process(
        self, category_reports: Dict[str, CategoryReport]
    ) -> Dict[str, CategoryReport]:
        """
        Check all category reports for stale model release coverage.

        Caps importance scores and annotates summaries for stale items.

        Args:
            category_reports: Dict of category -> CategoryReport.

        Returns:
            Updated category_reports with stale items demoted.
        """
        total_demoted = 0

        for category, report in category_reports.items():
            category_demoted = 0

            for item in report.all_items:
                if item.importance_score < MIN_SCORE_THRESHOLD:
                    continue

                # Check title + summary for stale release references
                text = f"{item.item.title} {item.summary}"
                match = self._find_stale_release_in_text(text)
                if not match:
                    continue

                model_variant, ga_date, provider = match

                if not self._is_primarily_about_release(item, model_variant):
                    continue

                # Demote: cap score and annotate
                old_score = item.importance_score
                item.importance_score = min(item.importance_score, STALE_SCORE_CAP)

                days_old = (self.coverage_date - datetime.strptime(ga_date, "%Y-%m-%d").date()).days
                logger.info(
                    f"STALE RELEASE: [{category}] \"{item.item.title}\" "
                    f"— model GA was {ga_date} ({days_old}d ago), "
                    f"score {old_score:.0f} -> {item.importance_score:.0f}"
                )

                # Annotate reasoning
                item.reasoning = (
                    f"[Staleness check: primary subject released {days_old} days ago "
                    f"(GA: {ga_date}), score capped] {item.reasoning}"
                )

                category_demoted += 1
                total_demoted += 1

            # Re-sort top_items after score changes and backfill if needed
            if category_demoted > 0:
                all_sorted = sorted(
                    report.all_items,
                    key=lambda x: x.importance_score,
                    reverse=True,
                )
                report.top_items = [
                    item
                    for item in all_sorted[:15]
                    if not (item.continuation and item.continuation.should_demote)
                ][:10]

        if total_demoted > 0:
            logger.info(
                f"Staleness checker: demoted {total_demoted} item(s) "
                f"with stale release coverage"
            )
        else:
            logger.info("Staleness checker: no stale release coverage detected")

        return category_reports
