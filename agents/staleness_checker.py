"""
Staleness Checker — Post-Analysis Date Enforcement

Cross-references analyzed items against model_releases.yaml to detect
articles that report old model releases as new. Deterministic — no LLM
calls required.

Hooked into the orchestrator after Phase 2.5 (continuity detection).
"""

import logging
import json
import os
import re
from html import unescape
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .base import CategoryReport, AnalyzedItem

logger = logging.getLogger(__name__)

# How many days after GA a model release is still considered "fresh"
FRESHNESS_WINDOW_DAYS = 3

# Score caps for freshness enforcement
STALE_RELEASE_SCORE_CAP = 40.0
STALE_FOLLOWUP_SCORE_CAP = 55.0

# How many days after a primary-source post a secondary recap is still fresh
PRIMARY_FOLLOWUP_WINDOW_DAYS = 7

# Minimum original score to bother checking (skip low-scoring items)
MIN_SCORE_THRESHOLD = 50.0

FRESHNESS_USER_AGENT = os.getenv(
    "NEWS_USER_AGENT",
    os.getenv("REDDIT_USER_AGENT", "AI-News-Aggregator/1.0")
)

PRIMARY_SOURCE_DOMAINS = (
    "ai.google.dev",
    "blog.google",
    "cloud.google.com",
    "developers.googleblog.com",
    "research.google",
    "deepmind.google",
    "openai.com",
    "anthropic.com",
    "microsoft.com",
    "github.blog",
    "aws.amazon.com",
    "blogs.nvidia.com",
    "ai.meta.com",
    "meta.com",
    "mistral.ai",
    "x.ai",
    "cohere.com",
    "huggingface.co",
    "stability.ai",
    "arxiv.org",
)

FOLLOWUP_SIGNALS = (
    "adds",
    "added",
    "available",
    "boost",
    "delivers",
    "faster",
    "gains",
    "gets",
    "introduces",
    "launched",
    "launches",
    "multi-token prediction",
    "mtp",
    "now supports",
    "open-sourced",
    "preview",
    "released",
    "rolls out",
    "ships",
    "speculative decoding",
    "supports",
    "unveiled",
)


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
        self._article_page_cache: Dict[str, Optional[str]] = {}
        self._primary_date_cache: Dict[str, Optional[date]] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": FRESHNESS_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

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

    def _has_followup_signal(self, item: AnalyzedItem) -> bool:
        combined = f"{item.item.title} {item.summary} {item.item.content}".lower()
        return any(signal in combined for signal in FOLLOWUP_SIGNALS)

    def _hostname(self, url: str) -> str:
        try:
            return (urlparse(url).hostname or "").lower()
        except Exception:
            return ""

    def _is_primary_domain(self, hostname: str) -> bool:
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in PRIMARY_SOURCE_DOMAINS
        )

    def _is_same_site(self, left: str, right: str) -> bool:
        left_parts = left.split(".")
        right_parts = right.split(".")
        if len(left_parts) < 2 or len(right_parts) < 2:
            return left == right
        return ".".join(left_parts[-2:]) == ".".join(right_parts[-2:])

    def _find_primary_source_url(self, item: AnalyzedItem) -> Optional[str]:
        """Fetch a secondary article and look for an official primary-source link."""
        item_url = item.item.url
        item_host = self._hostname(item_url)
        if not item_url or self._is_primary_domain(item_host):
            return None

        if item_url not in self._article_page_cache:
            try:
                response = self._session.get(item_url, timeout=12)
                response.raise_for_status()
                self._article_page_cache[item_url] = response.text
            except Exception as exc:
                logger.debug(f"Freshness check could not fetch article page {item_url}: {exc}")
                self._article_page_cache[item_url] = None

        html = self._article_page_cache.get(item_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("a", href=True):
            href = urljoin(item_url, link.get("href", ""))
            host = self._hostname(href)
            if not host or self._is_same_site(item_host, host):
                continue
            if self._is_primary_domain(host):
                return href

        return None

    def _parse_date_value(self, value: object):
        if not value:
            return None
        if isinstance(value, list):
            for nested in value:
                parsed = self._parse_date_value(nested)
                if parsed:
                    return parsed
            return None
        if not isinstance(value, str):
            return None
        try:
            return date_parser.parse(value, fuzzy=True).date()
        except Exception:
            return None

    def _json_find_date(self, data: object):
        if isinstance(data, dict):
            for key in ("datePublished", "dateCreated", "uploadDate", "dateModified"):
                parsed = self._parse_date_value(data.get(key))
                if parsed:
                    return parsed
            for value in data.values():
                parsed = self._json_find_date(value)
                if parsed:
                    return parsed
        elif isinstance(data, list):
            for value in data:
                parsed = self._json_find_date(value)
                if parsed:
                    return parsed
        return None

    def _extract_primary_published_date(self, primary_url: str):
        if primary_url in self._primary_date_cache:
            return self._primary_date_cache[primary_url]

        try:
            response = self._session.get(primary_url, timeout=12)
            response.raise_for_status()
        except Exception as exc:
            logger.debug(f"Freshness check could not fetch primary page {primary_url}: {exc}")
            self._primary_date_cache[primary_url] = None
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        meta_keys = (
            ("property", "article:published_time"),
            ("property", "og:article:published_time"),
            ("name", "article:published_time"),
            ("name", "date"),
            ("name", "datePublished"),
            ("itemprop", "datePublished"),
        )
        for attr, value in meta_keys:
            tag = soup.find("meta", attrs={attr: value})
            parsed = self._parse_date_value(tag.get("content") if tag else None)
            if parsed:
                self._primary_date_cache[primary_url] = parsed
                return parsed

        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                parsed = self._json_find_date(json.loads(raw))
                if parsed:
                    self._primary_date_cache[primary_url] = parsed
                    return parsed
            except Exception:
                continue

        for time_tag in soup.find_all("time"):
            parsed = self._parse_date_value(
                time_tag.get("datetime") or time_tag.get("content") or time_tag.get_text(" ", strip=True)
            )
            if parsed:
                self._primary_date_cache[primary_url] = parsed
                return parsed

        text = unescape(soup.get_text(" ", strip=True))
        match = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+"
            r"\d{1,2},\s+\d{4}\b",
            text[:3000],
            re.IGNORECASE,
        )
        parsed = self._parse_date_value(match.group(0) if match else None)
        self._primary_date_cache[primary_url] = parsed
        return parsed

    def _mark_freshness(
        self,
        item: AnalyzedItem,
        status: str,
        label: str,
        reason: str,
        score_cap: float,
        primary_url: Optional[str] = None,
        primary_published=None,
        age_days: Optional[int] = None,
    ) -> bool:
        old_score = float(item.importance_score or 0)
        item.importance_score = min(old_score, score_cap)

        metadata = item.item.metadata if isinstance(item.item.metadata, dict) else {}
        item.item.metadata = metadata
        freshness = {
            "status": status,
            "label": label,
            "reason": reason,
            "score_cap": score_cap,
            "exclude_from_top": True,
        }
        if primary_url:
            freshness["primary_url"] = primary_url
        if primary_published:
            freshness["primary_published"] = primary_published.isoformat()
        if age_days is not None:
            freshness["age_days"] = age_days
        metadata["freshness"] = freshness

        if not item.reasoning.startswith("[Freshness check:"):
            item.reasoning = f"[Freshness check: {reason}] {item.reasoning}"

        return item.importance_score < old_score or status not in ("fresh", "")

    def _is_excluded_from_top(self, item: AnalyzedItem) -> bool:
        metadata = item.item.metadata if isinstance(item.item.metadata, dict) else {}
        freshness = metadata.get("freshness") if isinstance(metadata.get("freshness"), dict) else {}
        return bool(freshness.get("exclude_from_top"))

    def process_news_items(self, items: List[AnalyzedItem]) -> int:
        """Apply freshness policy to analyzed news items before final ranking."""
        demoted = 0

        for item in items:
            if item.importance_score < MIN_SCORE_THRESHOLD:
                continue
            if self._is_excluded_from_top(item):
                continue

            text = f"{item.item.title} {item.summary}"
            match = self._find_stale_release_in_text(text)
            if match:
                model_variant, ga_date, _provider = match
                if self._is_primarily_about_release(item, model_variant):
                    ga_dt = datetime.strptime(ga_date, "%Y-%m-%d").date()
                    days_old = (self.coverage_date - ga_dt).days
                    reason = f"model GA was {ga_date} ({days_old}d before coverage)"
                    if self._mark_freshness(
                        item,
                        "stale_release",
                        "Stale release",
                        reason,
                        STALE_RELEASE_SCORE_CAP,
                        age_days=days_old,
                    ):
                        logger.info(
                            f"STALE RELEASE: [news] \"{item.item.title}\" — {reason}, "
                            f"score capped at {item.importance_score:.0f}"
                        )
                        demoted += 1
                    continue

            if not self._has_followup_signal(item):
                continue

            primary_url = self._find_primary_source_url(item)
            if not primary_url:
                continue

            primary_date = self._extract_primary_published_date(primary_url)
            if not primary_date:
                continue

            age_days = (self.coverage_date - primary_date).days
            if age_days <= PRIMARY_FOLLOWUP_WINDOW_DAYS:
                continue

            reason = (
                f"secondary coverage of primary source from {primary_date.isoformat()} "
                f"({age_days}d before coverage)"
            )
            if self._mark_freshness(
                item,
                "stale_followup",
                "Follow-up",
                reason,
                STALE_FOLLOWUP_SCORE_CAP,
                primary_url=primary_url,
                primary_published=primary_date,
                age_days=age_days,
            ):
                logger.info(
                    f"STALE FOLLOW-UP: [news] \"{item.item.title}\" — {reason}, "
                    f"score capped at {item.importance_score:.0f}"
                )
                demoted += 1

        return demoted

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

            if category == "news":
                category_demoted = self.process_news_items(report.all_items)
                total_demoted += category_demoted
                if category_demoted > 0:
                    all_sorted = sorted(
                        report.all_items,
                        key=lambda x: x.importance_score,
                        reverse=True,
                    )
                    report.top_items = [
                        item
                        for item in all_sorted
                        if not (item.continuation and item.continuation.should_demote)
                        and not self._is_excluded_from_top(item)
                    ][:10]
                continue

            for item in report.all_items:
                if item.importance_score < MIN_SCORE_THRESHOLD:
                    continue
                if self._is_excluded_from_top(item):
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
                item.importance_score = min(item.importance_score, STALE_RELEASE_SCORE_CAP)

                days_old = (self.coverage_date - datetime.strptime(ga_date, "%Y-%m-%d").date()).days
                logger.info(
                    f"STALE RELEASE: [{category}] \"{item.item.title}\" "
                    f"— model GA was {ga_date} ({days_old}d ago), "
                    f"score {old_score:.0f} -> {item.importance_score:.0f}"
                )

                # Annotate reasoning
                self._mark_freshness(
                    item,
                    "stale_release",
                    "Stale release",
                    f"primary subject released {days_old} days ago (GA: {ga_date})",
                    STALE_RELEASE_SCORE_CAP,
                    age_days=days_old,
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
                    for item in all_sorted
                    if not (item.continuation and item.continuation.should_demote)
                    and not self._is_excluded_from_top(item)
                ][:10]

        if total_demoted > 0:
            logger.info(
                f"Staleness checker: demoted {total_demoted} item(s) "
                f"with stale release coverage"
            )
        else:
            logger.info("Staleness checker: no stale release coverage detected")

        return category_reports
