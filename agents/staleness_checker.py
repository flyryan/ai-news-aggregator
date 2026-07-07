"""
Staleness Checker — Date and Old-Anchor Enforcement

Cross-references analyzed items against model_releases.yaml to detect
articles that report old model releases as new. Deterministic — no LLM
calls required for release checks.

Applied before reduce-phase ranking/summaries, then run again after continuity
as a defensive backstop.
"""

import ipaddress
import logging
import json
import os
import re
import socket
from html import unescape
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .base import CategoryReport, AnalyzedItem
from .llm_client import ThinkingLevel

logger = logging.getLogger(__name__)

# How many days after GA a model release is still considered "fresh"
FRESHNESS_WINDOW_DAYS = 3

# Score caps for freshness enforcement
STALE_RELEASE_SCORE_CAP = 40.0
STALE_FOLLOWUP_SCORE_CAP = 55.0
STALE_ANCHOR_SCORE_CAP = 55.0

# How many days after a primary-source post a secondary recap is still fresh
PRIMARY_FOLLOWUP_WINDOW_DAYS = 7

# How far back to search for prior coverage of a stale anchor fact.
OLD_ANCHOR_LOOKBACK_DAYS = 45
OLD_ANCHOR_MAX_LLM_CHECKS = int(os.getenv("OLD_ANCHOR_MAX_LLM_CHECKS", "4"))
OLD_ANCHOR_LLM_ENABLED = os.getenv("OLD_ANCHOR_LLM_ENABLED", "true").lower() not in {"0", "false", "no"}

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

OLD_ANCHOR_SIGNALS = (
    "after reportedly",
    "already",
    "earlier",
    "earlier this month",
    "following reports",
    "had already",
    "last month",
    "last week",
    "previously",
    "reportedly",
    "weeks after",
)

MATERIAL_NEW_DEVELOPMENT_SIGNALS = (
    "acquired",
    "announced",
    "approved",
    "banned",
    "blocked",
    "canceled",
    "cancelled",
    "filed",
    "fixed",
    "launched",
    "merged",
    "open-sourced",
    "ordered",
    "patched",
    "published",
    "raised",
    "released",
    "rolled out",
    "signed",
    "sued",
    "unveiled",
    "updated",
)

OLD_ANCHOR_STOPWORDS = {
    "about", "after", "also", "amid", "and", "are", "article", "been",
    "but", "can", "company", "could", "from", "has", "have", "into",
    "its", "more", "new", "news", "not", "now", "said", "says", "that",
    "the", "their", "them", "then", "there", "these", "this", "through",
    "today", "was", "were", "what", "when", "where", "which", "while",
    "with", "would", "you", "your",
}

OLD_ANCHOR_ENTITY_TERMS = {
    "anthropic", "claude", "google", "gemini", "microsoft", "openai", "uber"
}

OLD_ANCHOR_FACT_TERMS = {
    "budget", "burned", "burnt", "cost", "costs", "exhausted",
    "justify", "roi", "spend", "spending", "token", "tokens"
}

OLD_ANCHOR_CATEGORIES = {"news", "social", "reddit"}

# --- SSRF guard for outbound freshness fetches (finding #1248, CWE-918) --------
# StalenessChecker fetches fully untrusted URLs (RSS <link>, second-order
# <a href>, redirect targets). Route every outbound GET through _safe_get() so a
# malicious URL cannot reach loopback / RFC1918 / link-local (cloud-metadata)
# targets, and cannot use a non-http(s) scheme.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
MAX_SAFE_REDIRECTS = 5
# Cap the body we buffer from an untrusted fetch. Freshness only needs the
# article <head>/date metadata, so 5 MiB is far more than enough while
# preventing a malicious host from exhausting memory with a multi-GB body.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class SSRFBlockedError(requests.exceptions.RequestException):
    """Raised when an outbound fetch targets a disallowed scheme or private address.

    Subclasses requests.RequestException so existing ``except Exception`` handlers
    around the fetch sinks treat it as an ordinary (logged, non-fatal) fetch failure.
    """


def _ip_is_blocked(ip_str: str) -> bool:
    """True when ip_str is not a publicly routable address.

    Rejects loopback / RFC1918 private / link-local (cloud-metadata) /
    reserved / multicast / unspecified ranges, and additionally requires the
    address to be globally routable (``is_global``). The ``is_global`` check
    closes carrier-grade NAT (``100.64.0.0/10``) and other shared/special
    ranges that are not flagged as private on older Python versions.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address -> refuse
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) so the v4 rules apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


class StalenessChecker:
    """
    Checks analyzed items against known model release dates.

    If an article's primary subject is a model release whose GA date
    is older than FRESHNESS_WINDOW_DAYS relative to the coverage date,
    cap its importance score and annotate its summary.
    """

    def __init__(self, config_dir: str, target_date: str, web_dir: str = "./web"):
        """
        Args:
            config_dir: Path to config/ directory containing model_releases.yaml.
            target_date: Report date (YYYY-MM-DD). Coverage date = target_date - 1.
            web_dir: Path to generated web output, used for old-anchor history.
        """
        self.config_dir = Path(config_dir)
        self.web_dir = Path(web_dir)
        self.target_date = target_date
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        self.target_day = target_dt.date()
        self.coverage_date = (target_dt - timedelta(days=1)).date()
        self.cutoff_date = self.coverage_date - timedelta(days=FRESHNESS_WINDOW_DAYS)
        self.releases = self._load_releases()
        self._article_page_cache: Dict[str, Optional[str]] = {}
        self._primary_date_cache: Dict[str, Optional[date]] = {}
        self._historical_anchor_items: Optional[List[Dict[str, Any]]] = None
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

    def _has_old_anchor_signal(self, item: AnalyzedItem) -> bool:
        combined = f"{item.item.title} {item.summary} {item.item.content}".lower()
        return any(signal in combined for signal in OLD_ANCHOR_SIGNALS)

    def _has_material_new_development_signal(self, item: AnalyzedItem) -> bool:
        combined = f"{item.item.title} {item.summary}".lower()
        return any(signal in combined for signal in MATERIAL_NEW_DEVELOPMENT_SIGNALS)

    def _anchor_terms(self, text: str) -> Set[str]:
        terms = set()
        for token in re.findall(r"[a-z0-9][a-z0-9.-]*", text.lower()):
            normalized = token.strip(".-")
            if len(normalized) < 3:
                continue
            if normalized in OLD_ANCHOR_STOPWORDS:
                continue
            terms.add(normalized)
        return terms

    def _anchor_term_sequence(self, text: str) -> List[str]:
        terms: List[str] = []
        for token in re.findall(r"[a-z0-9][a-z0-9.-]*", text.lower()):
            normalized = token.strip(".-")
            if len(normalized) < 3:
                continue
            if normalized in OLD_ANCHOR_STOPWORDS:
                continue
            terms.append(normalized)
        return terms

    def _anchor_signature(self, item: AnalyzedItem) -> Set[str]:
        return self._anchor_terms(
            f"{item.item.title} {item.summary} {item.item.content[:1200]}"
        )

    def _parse_item_date(self, value: object) -> Optional[date]:
        parsed = self._parse_date_value(value)
        return parsed

    def _history_window_start(self) -> date:
        return self.target_day - timedelta(days=OLD_ANCHOR_LOOKBACK_DAYS)

    def _load_history_from_search_documents(self) -> List[Dict[str, Any]]:
        path = self.web_dir / "data" / "search-documents.json"
        if not path.exists():
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:
            logger.debug(f"Freshness check could not load search history {path}: {exc}")
            return []

        records = raw.values() if isinstance(raw, dict) else raw
        items: List[Dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            item_date = self._parse_item_date(record.get("date"))
            if not item_date:
                continue
            if item_date >= self.target_day or item_date < self._history_window_start():
                continue
            title = record.get("title", "")
            summary = record.get("summary", "")
            if not title and not summary:
                continue
            items.append({
                "id": record.get("id", ""),
                "date": item_date.isoformat(),
                "category": record.get("category", ""),
                "title": title,
                "summary": summary,
                "source": record.get("source", ""),
                "url": record.get("url", ""),
                "terms": self._anchor_terms(f"{title} {summary}"),
            })
        return items

    def _load_history_from_category_files(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        data_dir = self.web_dir / "data"
        if not data_dir.exists():
            return items

        current = self.target_day - timedelta(days=1)
        oldest = self._history_window_start()
        while current >= oldest:
            date_dir = data_dir / current.isoformat()
            for category in ("news", "research", "social", "reddit"):
                path = date_dir / f"{category}.json"
                if not path.exists():
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as exc:
                    logger.debug(f"Freshness check could not load history file {path}: {exc}")
                    continue
                for record in data.get("items", []):
                    if not isinstance(record, dict):
                        continue
                    title = record.get("title", "")
                    summary = record.get("summary", "")
                    if not title and not summary:
                        continue
                    items.append({
                        "id": record.get("id", ""),
                        "date": current.isoformat(),
                        "category": category,
                        "title": title,
                        "summary": summary,
                        "source": record.get("source", ""),
                        "url": record.get("url", ""),
                        "terms": self._anchor_terms(f"{title} {summary}"),
                    })
            current -= timedelta(days=1)
        return items

    def _load_historical_anchor_items(self) -> List[Dict[str, Any]]:
        if self._historical_anchor_items is not None:
            return self._historical_anchor_items

        items = self._load_history_from_search_documents()
        if not items:
            items = self._load_history_from_category_files()

        seen = set()
        deduped = []
        for item in items:
            key = item.get("id") or (item.get("date"), item.get("title"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        self._historical_anchor_items = deduped
        logger.info(f"Old-anchor freshness: loaded {len(deduped)} historical item(s)")
        return deduped

    def _find_old_anchor_matches(self, item: AnalyzedItem) -> List[Dict[str, Any]]:
        today_terms = self._anchor_signature(item)
        if len(today_terms) < 4:
            return []

        matches = []
        for historical in self._load_historical_anchor_items():
            if historical.get("id") == item.item.id or historical.get("url") == item.item.url:
                continue

            history_terms = historical.get("terms") or set()
            if not history_terms:
                continue

            overlap = today_terms & history_terms
            if len(overlap) < 4:
                continue

            overlap_ratio = len(overlap) / max(1, min(len(today_terms), len(history_terms)))
            strong_entity_overlap = any(term in overlap for term in OLD_ANCHOR_ENTITY_TERMS)
            strong_anchor_overlap = any(term in overlap for term in OLD_ANCHOR_FACT_TERMS)
            if overlap_ratio < 0.30 and not (strong_entity_overlap and strong_anchor_overlap):
                continue

            matches.append({
                "id": historical.get("id", ""),
                "date": historical.get("date", ""),
                "category": historical.get("category", ""),
                "title": historical.get("title", ""),
                "source": historical.get("source", ""),
                "url": historical.get("url", ""),
                "overlap_terms": sorted(overlap)[:20],
                "overlap_ratio": round(overlap_ratio, 3),
            })

        matches.sort(key=lambda match: (match["overlap_ratio"], match["date"]), reverse=True)
        return matches[:5]

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

    def _hostname_is_safe(self, hostname: str) -> bool:
        """Resolve hostname and require every resolved address to be public/routable."""
        if not hostname:
            return False
        try:
            infos = socket.getaddrinfo(hostname, None)
        except (socket.gaierror, socket.herror, UnicodeError, OSError):
            return False
        if not infos:
            return False
        for info in infos:
            sockaddr = info[4]
            if not sockaddr or _ip_is_blocked(sockaddr[0]):
                return False
        return True

    def _safe_get(self, url: str, timeout: int = 12, max_redirects: int = MAX_SAFE_REDIRECTS):
        """SSRF-guarded GET: http(s) only, no private targets, redirects re-validated.

        Redirects are followed manually (allow_redirects=False) so every hop's scheme
        and resolved IP are validated before the request is issued — a 302 from an
        allowlisted domain cannot bounce the fetch into an internal host. Raises
        SSRFBlockedError when a hop is disallowed.
        """
        current = url
        for _ in range(max_redirects + 1):
            parsed = urlparse(current)
            scheme = (parsed.scheme or "").lower()
            if scheme not in ALLOWED_URL_SCHEMES:
                raise SSRFBlockedError(f"blocked non-http(s) URL scheme: {scheme!r}")
            host = (parsed.hostname or "").lower()
            if not self._hostname_is_safe(host):
                raise SSRFBlockedError(
                    f"blocked outbound request to disallowed host: {host!r}"
                )
            response = self._session.get(
                current, timeout=timeout, allow_redirects=False, stream=True
            )
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    return response
                # Release the intermediate hop's connection back to the pool
                # before following the redirect (nothing in the body is needed).
                response.close()
                current = urljoin(current, location)
                continue
            # Final hop: buffer the body under a hard size cap before returning,
            # so callers keep using response.text/.content unchanged.
            self._buffer_capped_body(response)
            return response
        raise SSRFBlockedError(f"too many redirects while fetching {url!r}")

    def _buffer_capped_body(self, response) -> None:
        """Read an untrusted response body into memory under MAX_RESPONSE_BYTES.

        Streams the body and refuses (SSRFBlockedError, treated upstream as an
        ordinary non-fatal fetch failure) once it exceeds the cap, so a hostile
        or misconfigured host cannot exhaust memory. The buffered bytes are
        stored back on the response so ``.text``/``.content`` work as before.
        """
        declared = response.headers.get("Content-Length")
        if declared and declared.strip().isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            response.close()
            raise SSRFBlockedError(
                f"response body exceeds {MAX_RESPONSE_BYTES}-byte cap "
                f"(declared Content-Length {declared})"
            )
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                response.close()
                raise SSRFBlockedError(
                    f"response body exceeds {MAX_RESPONSE_BYTES}-byte cap"
                )
        response._content = bytes(body)
        response._content_consumed = True
        response.close()

    def _find_primary_source_url(self, item: AnalyzedItem) -> Optional[str]:
        """Fetch a secondary article and look for an official primary-source link."""
        item_url = item.item.url
        item_host = self._hostname(item_url)
        if not item_url or self._is_primary_domain(item_host):
            return None

        if item_url not in self._article_page_cache:
            try:
                response = self._safe_get(item_url, timeout=12)
                response.raise_for_status()
                if self._is_html_response(response):
                    self._article_page_cache[item_url] = response.text
                else:
                    logger.debug(f"Freshness: skipping non-HTML article page {item_url}")
                    self._article_page_cache[item_url] = None
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
            response = self._safe_get(primary_url, timeout=12)
            response.raise_for_status()
        except Exception as exc:
            logger.debug(f"Freshness check could not fetch primary page {primary_url}: {exc}")
            self._primary_date_cache[primary_url] = None
            return None

        # Only parse HTML/text. A primary URL pointing at a binary/compressed resource
        # (PDF, image, undecoded gzip) would otherwise feed garbage bytes into the date
        # parser and raise (e.g. int() on binary), disabling the freshness policy.
        if not self._is_html_response(response):
            logger.debug(f"Freshness: skipping non-HTML primary page {primary_url}")
            self._primary_date_cache[primary_url] = None
            return None

        try:
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
        except Exception as exc:
            logger.debug(f"Freshness: failed to parse primary page {primary_url}: {exc}")
            parsed = None
        self._primary_date_cache[primary_url] = parsed
        return parsed

    @staticmethod
    def _is_html_response(response) -> bool:
        """True when a response looks like HTML/XML/text (vs. binary like PDF/image)."""
        ctype = (response.headers.get("Content-Type") or "").lower()
        if not ctype:
            return True  # no content-type header: fall back to parsing (caught defensively)
        return any(token in ctype for token in ("html", "xml", "text"))

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
        matched_prior_items: Optional[List[Dict[str, Any]]] = None,
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
            "exclude_from_summaries": True,
        }
        if primary_url:
            freshness["primary_url"] = primary_url
        if primary_published:
            freshness["primary_published"] = primary_published.isoformat()
        if age_days is not None:
            freshness["age_days"] = age_days
        if matched_prior_items:
            freshness["matched_prior_items"] = [
                {
                    key: match.get(key)
                    for key in ("id", "date", "category", "title", "source", "url")
                    if match.get(key)
                }
                for match in matched_prior_items[:3]
            ]
        metadata["freshness"] = freshness

        if not item.reasoning.startswith("[Freshness check:"):
            item.reasoning = f"[Freshness check: {reason}] {item.reasoning}"

        return item.importance_score < old_score or status not in ("fresh", "")

    def _is_excluded_from_top(self, item: AnalyzedItem) -> bool:
        metadata = item.item.metadata if isinstance(item.item.metadata, dict) else {}
        freshness = metadata.get("freshness") if isinstance(metadata.get("freshness"), dict) else {}
        return bool(freshness.get("exclude_from_top"))

    def _is_excluded_from_summaries(self, item: AnalyzedItem) -> bool:
        metadata = item.item.metadata if isinstance(item.item.metadata, dict) else {}
        freshness = metadata.get("freshness") if isinstance(metadata.get("freshness"), dict) else {}
        return bool(freshness.get("exclude_from_summaries"))

    def _contains_title_phrase(
        self,
        segment_terms: List[str],
        item: AnalyzedItem,
        min_terms: int = 3,
    ) -> bool:
        title_terms = self._anchor_term_sequence(item.item.title)
        if len(title_terms) < min_terms or len(segment_terms) < min_terms:
            return False

        segment = " ".join(segment_terms)
        for i in range(0, len(title_terms) - min_terms + 1):
            phrase = " ".join(title_terms[i:i + min_terms])
            if phrase in segment:
                return True
        return False

    def _summary_segment_matches_excluded(
        self,
        segment: str,
        excluded_item: AnalyzedItem,
    ) -> bool:
        metadata = excluded_item.item.metadata if isinstance(excluded_item.item.metadata, dict) else {}
        freshness = metadata.get("freshness") if isinstance(metadata.get("freshness"), dict) else {}
        status = freshness.get("status", "")

        segment_terms_list = self._anchor_term_sequence(segment)
        segment_terms = set(segment_terms_list)
        if not segment_terms:
            return False

        item_terms = self._anchor_signature(excluded_item)
        overlap = segment_terms & item_terms
        has_entity_fact_overlap = (
            any(term in segment_terms for term in OLD_ANCHOR_ENTITY_TERMS)
            and any(term in segment_terms for term in OLD_ANCHOR_FACT_TERMS)
            and any(term in item_terms for term in OLD_ANCHOR_ENTITY_TERMS)
        )

        if status == "stale_anchor" and has_entity_fact_overlap:
            return True

        if status == "stale_release" and self._contains_title_phrase(
            segment_terms_list, excluded_item, min_terms=2
        ):
            return True

        if self._contains_title_phrase(segment_terms_list, excluded_item, min_terms=3):
            return True

        overlap_ratio = len(overlap) / max(1, min(len(segment_terms), len(item_terms)))
        return len(overlap) >= 5 and overlap_ratio >= 0.28

    def _sanitize_category_summary(self, category: str, report: CategoryReport) -> bool:
        """Remove summary sentences that refer to freshness-excluded items."""
        excluded_items = [
            item for item in report.all_items
            if self._is_excluded_from_summaries(item)
        ]
        if not excluded_items or not report.category_summary:
            return False

        changed = False
        sanitized_lines = []
        for line in report.category_summary.splitlines():
            if not line.strip():
                sanitized_lines.append(line)
                continue

            segments = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9_*`\[])", line)
            kept_segments = []
            for segment in segments:
                if any(
                    self._summary_segment_matches_excluded(segment, item)
                    for item in excluded_items
                ):
                    changed = True
                    continue
                kept_segments.append(segment)

            sanitized_line = " ".join(part.strip() for part in kept_segments if part.strip())
            if sanitized_line:
                sanitized_lines.append(sanitized_line)
            elif line.strip():
                changed = True

        if changed:
            report.category_summary = "\n".join(sanitized_lines).strip()
            logger.info(
                f"Freshness summary sanitizer: removed stale references from {category} summary"
            )
        return changed

    def _process_stale_releases(self, category: str, items: List[AnalyzedItem]) -> int:
        """Apply model-release freshness policy to analyzed items."""
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
                        reason if category == "news" else f"primary subject released {days_old} days ago (GA: {ga_date})",
                        STALE_RELEASE_SCORE_CAP,
                        age_days=days_old,
                    ):
                        logger.info(
                            f"STALE RELEASE: [{category}] \"{item.item.title}\" — {reason}, "
                            f"score capped at {item.importance_score:.0f}"
                        )
                        demoted += 1
                    continue

        return demoted

    def _process_news_primary_followups(self, items: List[AnalyzedItem]) -> int:
        """Detect secondary coverage whose primary-source post is old."""
        demoted = 0

        for item in items:
            if item.importance_score < MIN_SCORE_THRESHOLD:
                continue
            if self._is_excluded_from_top(item):
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

    def _old_anchor_candidate_state(
        self,
        item: AnalyzedItem,
        matches: List[Dict[str, Any]],
        category: str = "news",
    ) -> str:
        """Classify a matched history candidate as stale, ambiguous, or fresh."""
        if not matches:
            return "fresh"

        has_old_anchor_language = self._has_old_anchor_signal(item)
        has_material_signal = self._has_material_new_development_signal(item)
        best_ratio = matches[0].get("overlap_ratio", 0)
        best_terms = set(matches[0].get("overlap_terms", []))
        has_entity_fact_overlap = (
            any(term in best_terms for term in OLD_ANCHOR_ENTITY_TERMS)
            and any(term in best_terms for term in OLD_ANCHOR_FACT_TERMS)
        )

        # Social/community streams repeat phrases and memes constantly. For those
        # categories, only demote direct entity+fact duplicates instead of
        # treating old-anchor phrasing or high lexical overlap alone as stale.
        if category != "news":
            if has_entity_fact_overlap and not has_material_signal:
                return "stale"
            return "fresh"

        if has_old_anchor_language and not has_material_signal:
            return "stale"
        if has_entity_fact_overlap and not has_material_signal:
            return "stale"
        if best_ratio >= 0.50 and not has_material_signal:
            return "stale"
        if has_old_anchor_language or best_ratio >= 0.30:
            return "ambiguous"

        return "fresh"

    def _build_old_anchor_adjudication_prompt(
        self,
        item: AnalyzedItem,
        matches: List[Dict[str, Any]],
    ) -> str:
        history = [
            {
                "date": match.get("date"),
                "category": match.get("category"),
                "title": match.get("title"),
                "source": match.get("source"),
                "overlap_terms": match.get("overlap_terms", []),
            }
            for match in matches[:3]
        ]
        today = {
            "title": item.item.title,
            "source": item.item.source,
            "published": item.item.published,
            "summary": item.summary,
            "content_excerpt": item.item.content[:900],
        }
        return f"""You are a freshness editor for an AI news briefing.

Decide whether today's article is primarily an old-anchor follow-up: a fresh article whose main briefing-worthy claim was already covered previously, with only color/commentary added.

TODAY:
{json.dumps(today, ensure_ascii=False, indent=2)}

PRIOR COVERAGE:
{json.dumps(history, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "stale_anchor": true,
  "reason": "one sentence",
  "new_material_development": "briefly name the concrete new event, or empty string"
}}

Mark stale_anchor=false when today's item has a concrete new action, release, filing, outage, policy change, measured result, or official announcement beyond commentary around the old fact."""

    async def _adjudicate_old_anchor(
        self,
        item: AnalyzedItem,
        matches: List[Dict[str, Any]],
        async_client,
    ) -> Tuple[bool, str]:
        if not async_client or not OLD_ANCHOR_LLM_ENABLED:
            return False, ""

        prompt = self._build_old_anchor_adjudication_prompt(item, matches)
        try:
            response = await async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                system="You make concise editorial freshness decisions. Return valid JSON only.",
                profile=ThinkingLevel.QUICK,
                caller="freshness.old_anchor"
            )
            content = response.content.strip()
            json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if json_match:
                content = json_match.group(1).strip()
            if not content.startswith("{"):
                start = content.find("{")
                if start != -1:
                    content = content[start:]
            result = json.loads(content)
            return bool(result.get("stale_anchor")), str(result.get("reason", ""))
        except Exception as exc:
            logger.debug(f"Old-anchor LLM adjudication failed for {item.item.id}: {exc}")
            return False, ""

    def _mark_old_anchor_item(
        self,
        category: str,
        item: AnalyzedItem,
        matches: List[Dict[str, Any]],
        reason: str = "",
    ) -> bool:
        """Annotate one old-anchor item and log the freshness decision."""
        if not matches:
            return False

        if not reason:
            prior = matches[0]
            reason = (
                f"main claim overlaps prior {prior.get('category', 'coverage')} coverage "
                f"from {prior.get('date', 'unknown date')}"
            )

        primary_match_date = self._parse_item_date(matches[0].get("date"))
        age_days = (self.coverage_date - primary_match_date).days if primary_match_date else None

        marked = self._mark_freshness(
            item,
            "stale_anchor",
            "Old anchor",
            reason,
            STALE_ANCHOR_SCORE_CAP,
            age_days=age_days,
            matched_prior_items=matches,
        )
        if marked:
            logger.info(
                f"STALE ANCHOR: [{category}] \"{item.item.title}\" — {reason}, "
                f"score capped at {item.importance_score:.0f}"
            )
        return marked

    async def _process_old_anchors(
        self,
        category: str,
        items: List[AnalyzedItem],
        async_client=None,
    ) -> int:
        """Detect items whose main claim is an already-covered anchor fact."""
        demoted = 0
        llm_checks = 0

        for item in items:
            if item.importance_score < MIN_SCORE_THRESHOLD:
                continue
            if self._is_excluded_from_top(item):
                continue

            matches = self._find_old_anchor_matches(item)
            state = self._old_anchor_candidate_state(item, matches, category)
            if state == "fresh":
                continue

            reason = ""
            should_mark = state == "stale"
            if state == "ambiguous" and llm_checks < OLD_ANCHOR_MAX_LLM_CHECKS:
                llm_checks += 1
                should_mark, reason = await self._adjudicate_old_anchor(item, matches, async_client)

            if not should_mark:
                continue

            if self._mark_old_anchor_item(category, item, matches, reason):
                demoted += 1

        return demoted

    def _process_old_anchors_sync(
        self,
        category: str,
        items: List[AnalyzedItem],
    ) -> int:
        """Synchronous old-anchor pass for resume/checkpoint repair paths."""
        demoted = 0

        for item in items:
            if item.importance_score < MIN_SCORE_THRESHOLD:
                continue
            if self._is_excluded_from_top(item):
                continue

            matches = self._find_old_anchor_matches(item)
            if self._old_anchor_candidate_state(item, matches, category) != "stale":
                continue

            if self._mark_old_anchor_item(category, item, matches):
                demoted += 1

        return demoted

    async def process_items(
        self,
        category: str,
        items: List[AnalyzedItem],
        async_client=None,
    ) -> int:
        """Apply all freshness policies before reduce writes summaries."""
        demoted = self._process_stale_releases(category, items)
        if category == "news":
            demoted += self._process_news_primary_followups(items)
        if category in OLD_ANCHOR_CATEGORIES:
            demoted += await self._process_old_anchors(category, items, async_client=async_client)
        if demoted:
            items.sort(key=lambda x: x.importance_score, reverse=True)
        return demoted

    def process_items_sync(self, category: str, items: List[AnalyzedItem]) -> int:
        """Synchronous fallback for post-analysis repair paths."""
        demoted = self._process_stale_releases(category, items)
        if category == "news":
            demoted += self._process_news_primary_followups(items)
        if category in OLD_ANCHOR_CATEGORIES:
            demoted += self._process_old_anchors_sync(category, items)
        if demoted:
            items.sort(key=lambda x: x.importance_score, reverse=True)
        return demoted

    def process_news_items(self, items: List[AnalyzedItem]) -> int:
        """Backward-compatible sync entry point for news freshness."""
        return self.process_items_sync("news", items)

    def process(
        self, category_reports: Dict[str, CategoryReport]
    ) -> Dict[str, CategoryReport]:
        """
        Check all category reports for stale model release and old-anchor coverage.

        Caps importance scores and annotates stale items.

        Args:
            category_reports: Dict of category -> CategoryReport.

        Returns:
            Updated category_reports with stale/old-anchor items demoted.
        """
        total_demoted = 0

        for category, report in category_reports.items():
            category_demoted = self.process_items_sync(category, report.all_items)
            total_demoted += category_demoted

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

            self._sanitize_category_summary(category, report)

        if total_demoted > 0:
            logger.info(
                f"Staleness checker: demoted {total_demoted} item(s) "
                f"with stale or old-anchor coverage"
            )
        else:
            logger.info("Staleness checker: no stale or old-anchor coverage detected")

        return category_reports
