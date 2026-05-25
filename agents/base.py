"""
Base Classes for Multi-Agent Architecture

This module provides base classes for gatherers and analyzers that collect
and analyze AI/ML news from various sources.
"""

import os
import json
import logging
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Tuple
import asyncio
from urllib.parse import urlparse
import re

from .llm_client import AnthropicClient, AsyncAnthropicClient, ThinkingLevel, LLMResponse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config.prompts import PromptAccessor

logger = logging.getLogger(__name__)


class TruncatedJSONError(Exception):
    """Raised when an LLM JSON response was cut off before completion.

    Distinct from a JSON response that is well-formed but empty, or from
    a response that contains no JSON at all. Callers (notably
    ``_analyze_batch``) react to this by splitting the input batch and
    retrying, since the root cause is output-token exhaustion.
    """


class MalformedJSONError(Exception):
    """Raised when an LLM JSON response is complete but malformed.

    Large batch prompts occasionally return JSON with a local syntax error
    (for example a missing comma between objects). Map batches should recover
    by splitting into smaller batches instead of dropping every item.
    """


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer from the environment."""
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
        return default
    if value < minimum:
        logger.warning(f"Ignoring {name}={value}; minimum is {minimum}, using {default}")
        return default
    return value


@dataclass
class CollectedItem:
    """Standardized item from any gatherer."""
    id: str
    title: str
    content: str
    url: str
    author: str
    published: str
    source: str
    source_type: str  # 'rss', 'arxiv', 'twitter', 'reddit', 'bluesky', 'mastodon', 'linked_article'
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CollectedItem':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StoryMatch:
    """A potential match between today's item and historical item."""
    today_item_id: str
    today_category: str
    historical_item_id: str
    historical_category: str
    historical_date: str
    historical_title: str
    confidence: float  # 0-1


@dataclass
class ContinuationInfo:
    """Information about a story continuation from a previous day."""
    original_item_id: str
    original_date: str
    original_category: str
    original_title: str
    continuation_type: str  # 'new_development' | 'mainstream_pickup' | 'community_reaction' | 'rehash' | 'follow_up'
    should_demote: bool     # True = don't headline on homepage
    reference_text: str     # "as first reported in Social yesterday"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'original_item_id': self.original_item_id,
            'original_date': self.original_date,
            'original_category': self.original_category,
            'original_title': self.original_title,
            'continuation_type': self.continuation_type,
            'should_demote': self.should_demote,
            'reference_text': self.reference_text
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ContinuationInfo':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AnalyzedItem:
    """Item with analysis added."""
    item: CollectedItem
    summary: str
    importance_score: float  # 0-100
    reasoning: str  # Brief explanation of the score
    themes: List[str]  # Detected themes
    thinking: Optional[str] = None  # Extended thinking content (if available)
    continuation: Optional['ContinuationInfo'] = None  # Story continuation info

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = self.item.to_dict()
        result.update({
            'summary': self.summary,
            'importance_score': self.importance_score,
            'reasoning': self.reasoning,
            'themes': self.themes,
            'thinking': self.thinking,
            'continuation': self.continuation.to_dict() if self.continuation else None
        })
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnalyzedItem':
        """Create from a flat dictionary (as produced by to_dict)."""
        # Extract analysis fields
        continuation_data = data.get('continuation')
        continuation = ContinuationInfo.from_dict(continuation_data) if continuation_data else None

        # Build CollectedItem from the remaining fields
        item = CollectedItem.from_dict(data)

        return cls(
            item=item,
            summary=data.get('summary', ''),
            importance_score=data.get('importance_score', 50),
            reasoning=data.get('reasoning', ''),
            themes=data.get('themes', []),
            thinking=data.get('thinking'),
            continuation=continuation
        )


@dataclass
class CategoryTheme:
    """A detected theme within a category."""
    name: str
    description: str
    item_count: int
    example_items: List[str]  # Item IDs
    importance: float  # 0-100

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CategoryTheme':
        """Create from dictionary."""
        return cls(
            name=data.get('name', ''),
            description=data.get('description', ''),
            item_count=data.get('item_count', 0),
            example_items=data.get('example_items', []),
            importance=data.get('importance', 50)
        )


@dataclass
class CategoryReport:
    """
    Report produced by each category analyzer.

    This is the standard output format that each analyzer produces
    and the orchestrator consumes.
    """
    category: str  # 'news', 'papers', 'social', 'reddit'
    top_items: List[AnalyzedItem]  # Top 10 with full analysis + thinking
    all_items: List[AnalyzedItem]  # All items for comprehensive page
    category_summary: str  # Executive summary for this category
    themes: List[CategoryTheme]  # Detected themes within category
    cross_signals: List[str]  # Hints for orchestrator (e.g., "OpenAI news trending")
    total_collected: int
    analysis_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    thinking: Optional[str] = None  # Extended thinking from analysis

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'category': self.category,
            'top_items': [item.to_dict() for item in self.top_items],
            'all_items': [item.to_dict() for item in self.all_items],
            'category_summary': self.category_summary,
            'themes': [asdict(theme) for theme in self.themes],
            'cross_signals': self.cross_signals,
            'total_collected': self.total_collected,
            'analysis_timestamp': self.analysis_timestamp,
            'thinking': self.thinking
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CategoryReport':
        """Create from dictionary."""
        top_items = [AnalyzedItem.from_dict(item) for item in data.get('top_items', [])]
        all_items = [AnalyzedItem.from_dict(item) for item in data.get('all_items', [])]
        themes = [CategoryTheme.from_dict(t) for t in data.get('themes', [])]

        return cls(
            category=data.get('category', ''),
            top_items=top_items,
            all_items=all_items,
            category_summary=data.get('category_summary', ''),
            themes=themes,
            cross_signals=data.get('cross_signals', []),
            total_collected=data.get('total_collected', 0),
            analysis_timestamp=data.get('analysis_timestamp', ''),
            thinking=data.get('thinking')
        )


@dataclass
class BatchResult:
    """Result from a single batch analysis in map-reduce processing."""
    batch_index: int
    item_analyses: List[Dict[str, Any]]  # Per-item analysis results
    batch_themes: List[Dict[str, Any]]   # Themes detected in this batch
    cross_signals: List[str]             # Cross-category signals
    thinking: Optional[str] = None       # Extended thinking content


class BaseGatherer(ABC):
    """
    Base class for all gatherers.

    Gatherers are responsible for collecting items from specific sources
    and normalizing them to the CollectedItem format.
    """

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        lookback_hours: int = 24,
        target_date: Optional[str] = None
    ):
        """
        Initialize gatherer.

        Args:
            config_dir: Directory containing configuration files.
            data_dir: Directory for storing collected data.
            lookback_hours: Hours to look back for items (if target_date not set).
            target_date: Specific date to collect (YYYY-MM-DD format).
        """
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.lookback_hours = lookback_hours
        self.target_date = target_date

        # Set up date range
        # target_date is the REPORT date, coverage is the day BEFORE
        if target_date:
            self.report_date = target_date
            report_date_obj = datetime.strptime(target_date, '%Y-%m-%d')
            # Coverage is the previous day
            coverage_date_obj = report_date_obj - timedelta(days=1)
            self.coverage_date = coverage_date_obj.strftime('%Y-%m-%d')
            self.start_time = coverage_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            self.end_time = coverage_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Default: report today, coverage yesterday
            now = datetime.now()
            self.report_date = now.strftime('%Y-%m-%d')
            coverage_date_obj = now - timedelta(days=1)
            self.coverage_date = coverage_date_obj.strftime('%Y-%m-%d')
            self.start_time = coverage_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            self.end_time = coverage_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)

        logger.info(f"{self.__class__.__name__} initialized: report={self.report_date}, coverage={self.coverage_date} ({self.start_time} to {self.end_time})")

    @property
    @abstractmethod
    def category(self) -> str:
        """Return the category this gatherer collects for."""
        pass

    @abstractmethod
    async def gather(self) -> List[CollectedItem]:
        """
        Gather items from the source.

        Returns:
            List of CollectedItem objects.
        """
        pass

    def generate_id(self, *components: str) -> str:
        """Generate a unique ID from components (12 chars = ~280 trillion values)."""
        content = ':'.join(str(c) for c in components).encode('utf-8')
        return hashlib.sha256(content).hexdigest()[:12]

    def normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return normalized.rstrip('/').lower()
        except:
            return url.lower()

    def is_in_date_range(self, dt: datetime) -> bool:
        """Check if datetime is within the collection date range."""
        return self.start_time <= dt <= self.end_time

    def extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract keywords from text."""
        if not text:
            return []

        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'can', 'this', 'that', 'these',
            'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which',
            'who', 'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both',
            'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
            'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'new'
        }

        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        word_freq = {}
        for word in words:
            if word not in stop_words:
                word_freq[word] = word_freq.get(word, 0) + 1

        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [word for word, freq in top_words]

    def load_config_list(self, filename: str) -> List[str]:
        """Load a list from a config file (one item per line)."""
        filepath = os.path.join(self.config_dir, filename)
        if not os.path.exists(filepath):
            logger.warning(f"Config file not found: {filepath}")
            return []

        with open(filepath, 'r') as f:
            items = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return items

    def save_to_file(self, items: List[CollectedItem], filename: str):
        """Save collected items to JSON file."""
        raw_dir = os.path.join(self.data_dir, 'raw')
        os.makedirs(raw_dir, exist_ok=True)
        filepath = os.path.join(raw_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'collected_at': datetime.now().isoformat(),
                'category': self.category,
                'count': len(items),
                'items': [item.to_dict() for item in items]
            }, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved {len(items)} items to {filepath}")


class BaseAnalyzer(ABC):
    """
    Base class for all analyzers.

    Analyzers are responsible for analyzing collected items using LLM
    and producing CategoryReport outputs.
    """

    def __init__(
        self,
        llm_client: Optional[AnthropicClient] = None,
        async_client: Optional[AsyncAnthropicClient] = None,
        data_dir: str = './data',
        grounding_context: Optional[str] = None,
        prompt_accessor: Optional['PromptAccessor'] = None
    ):
        """
        Initialize analyzer.

        Args:
            llm_client: Anthropic client for LLM calls (sync).
            async_client: Async Anthropic client for parallel LLM calls.
            data_dir: Directory containing collected data.
            grounding_context: System prompt with AI ecosystem context for grounding.
            prompt_accessor: Optional PromptAccessor for config-based prompts.
        """
        self.llm_client = llm_client
        self.async_client = async_client
        self.data_dir = data_dir
        self.grounding_context = grounding_context
        self.prompt_accessor = prompt_accessor

        if not llm_client and not async_client:
            logger.warning("No LLM client provided - analysis will be limited")

    @property
    @abstractmethod
    def category(self) -> str:
        """Return the category this analyzer handles."""
        pass

    @property
    def thinking_budget(self) -> int:
        """Default thinking budget for this analyzer (used in reduce phase)."""
        return ThinkingLevel.DEEP

    # Map-reduce batch processing constants
    BATCH_SIZE = _env_int("ANALYZER_BATCH_SIZE", 75)  # Items per batch for map phase
    MAX_CONCURRENT_BATCHES = _env_int("ANALYZER_MAX_CONCURRENT_BATCHES", 3)  # Per-category API calls

    # --- Map-Reduce Methods ---

    def _format_batch_sample(self, batch_items: List[CollectedItem], max_items: int = 2) -> str:
        samples = []
        for item in batch_items[:max_items]:
            title = re.sub(r'\s+', ' ', item.title or '').strip()
            if len(title) > 80:
                title = title[:77] + "..."
            samples.append(f"{item.id}:{title}")
        return " | ".join(samples)

    async def _analyze_batch(
        self,
        batch_items: List[CollectedItem],
        batch_index: int,
        total_batches: int,
        sub_label: str = "",
    ) -> BatchResult:
        """
        MAP phase: Analyze a single batch of items.

        Uses STANDARD thinking for quality per-item analysis.

        When the LLM response is truncated (``stop_reason == 'max_tokens'``
        or the JSON was cut off mid-token), the batch is split in half and
        each half is analyzed recursively; results are then merged. This
        recovers transparently from dense batches that overflow the
        response token budget instead of silently dropping them.

        Args:
            batch_items: Items to analyze.
            batch_index: Zero-based batch index (used in prompts and logs).
            total_batches: Total batch count for the map phase.
            sub_label: Suffix appended to the label during recursive splits
                (e.g. "a", "b", "ab"); purely cosmetic for logging.
        """
        items_context = self._build_items_context(batch_items, max_items=len(batch_items))
        prompt = self._get_batch_analysis_prompt(items_context, batch_index, total_batches)

        label = f"{batch_index + 1}{sub_label}/{total_batches}"
        caller_suffix = f"{batch_index}{sub_label}"
        logger.info(
            f"  {self.category} map {label}: sending {len(batch_items)} items "
            f"(prompt_chars={len(prompt)}, system_chars={len(self.grounding_context or '')})"
        )

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                system=self.grounding_context,  # Inject ecosystem grounding
                budget_tokens=ThinkingLevel.STANDARD,  # Quality batch processing
                caller=f"{self.category}_analyzer.batch_{caller_suffix}"
            )

            if response.stop_reason == "max_tokens":
                return await self._handle_truncated_batch(
                    batch_items, batch_index, total_batches, sub_label
                )

            try:
                result = self._parse_json_response(
                    response.content, expected_items=len(batch_items)
                )
            except (TruncatedJSONError, MalformedJSONError) as parse_error:
                return await self._handle_truncated_batch(
                    batch_items, batch_index, total_batches, sub_label, str(parse_error)
                )

            batch_themes = result.get('themes', result.get('category_themes', []))
            parsed_items = result.get('items', [])
            logger.info(f"  {self.category} map {label}: {len(parsed_items)} items, {len(batch_themes)} themes")

            return BatchResult(
                batch_index=batch_index,
                item_analyses=parsed_items,
                batch_themes=batch_themes,
                cross_signals=result.get('cross_signals', []),
                thinking=response.thinking
            )
        except Exception as e:
            logger.error(f"{self.category} batch {label} analysis failed: {type(e).__name__}: {e}")
            # Retry once with backoff for transient failures (network, 5xx, etc.)
            try:
                await asyncio.sleep(5)
                response = await self.async_client.call_with_thinking(
                    messages=[{"role": "user", "content": prompt}],
                    system=self.grounding_context,  # Inject ecosystem grounding
                    budget_tokens=ThinkingLevel.STANDARD,
                    caller=f"{self.category}_analyzer.batch_{caller_suffix}_retry"
                )
                if response.stop_reason == "max_tokens":
                    return await self._handle_truncated_batch(
                        batch_items, batch_index, total_batches, sub_label
                    )
                try:
                    result = self._parse_json_response(
                        response.content, expected_items=len(batch_items)
                    )
                except (TruncatedJSONError, MalformedJSONError) as parse_error:
                    return await self._handle_truncated_batch(
                        batch_items, batch_index, total_batches, sub_label, str(parse_error)
                    )
                batch_themes = result.get('themes', result.get('category_themes', []))
                parsed_items = result.get('items', [])
                logger.info(f"  {self.category} map {label}: {len(parsed_items)} items, {len(batch_themes)} themes (retry)")
                return BatchResult(
                    batch_index=batch_index,
                    item_analyses=parsed_items,
                    batch_themes=batch_themes,
                    cross_signals=result.get('cross_signals', []),
                    thinking=response.thinking
                )
            except Exception as retry_e:
                logger.error(
                    f"  {self.category} map {label}: FAILED after retry: "
                    f"{type(retry_e).__name__}: {retry_e}"
                )
                return BatchResult(
                    batch_index=batch_index,
                    item_analyses=[],
                    batch_themes=[],
                    cross_signals=[],
                    thinking=f"Error: {e}, Retry error: {retry_e}"
                )

    async def _handle_truncated_batch(
        self,
        batch_items: List[CollectedItem],
        batch_index: int,
        total_batches: int,
        sub_label: str,
        reason: str = "truncated",
    ) -> BatchResult:
        """Recover from an unusable LLM response by splitting the batch.

        Runs each half through ``_analyze_batch`` inside the current batch
        slot and merges the results. Keeping recovery sequential prevents a
        malformed response from briefly exceeding the per-category analyzer
        concurrency limit. When a single-item batch still fails there is
        nothing further to split, so the item is dropped with a loud ERROR
        (the same user-visible outcome as the old silent-drop path, but only
        after exhausting recovery attempts).
        """
        label = f"{batch_index + 1}{sub_label}/{total_batches}"
        if len(batch_items) <= 1:
            logger.error(
                f"  {self.category} map {label}: unrecoverable response "
                f"({reason}) with {len(batch_items)} item(s); cannot split further, dropping"
            )
            return BatchResult(
                batch_index=batch_index,
                item_analyses=[],
                batch_themes=[],
                cross_signals=[],
                thinking=f"Error: unusable response ({reason}) with {len(batch_items)} item(s); cannot split further"
            )

        mid = len(batch_items) // 2
        left_items, right_items = batch_items[:mid], batch_items[mid:]
        logger.warning(
            f"  {self.category} map {label}: unusable response ({reason}), splitting "
            f"{len(batch_items)} items into {len(left_items)}+{len(right_items)} sub-batches"
        )

        left_result = await self._analyze_batch(
            left_items, batch_index, total_batches, sub_label + "a"
        )
        right_result = await self._analyze_batch(
            right_items, batch_index, total_batches, sub_label + "b"
        )

        thinkings = [t for t in (left_result.thinking, right_result.thinking) if t]
        merged_thinking = "\n\n".join(thinkings) if thinkings else None

        return BatchResult(
            batch_index=batch_index,
            item_analyses=left_result.item_analyses + right_result.item_analyses,
            batch_themes=left_result.batch_themes + right_result.batch_themes,
            cross_signals=left_result.cross_signals + right_result.cross_signals,
            thinking=merged_thinking,
        )

    def _get_batch_analysis_prompt(
        self,
        items_context: str,
        batch_index: int,
        total_batches: int
    ) -> str:
        """
        Get the analysis prompt for batch processing.
        Subclasses must override to provide category-specific prompts.
        """
        raise NotImplementedError("Subclasses must implement _get_batch_analysis_prompt")

    async def _map_phase(
        self,
        items: List[CollectedItem]
    ) -> Tuple[List[BatchResult], List[CollectedItem]]:
        """
        MAP phase: Process all items in parallel batches.

        Returns:
            Tuple of (batch_results, items) for reduce phase
        """
        if not items:
            return [], items

        # Split into batches
        batches = [
            items[i:i + self.BATCH_SIZE]
            for i in range(0, len(items), self.BATCH_SIZE)
        ]
        total_batches = len(batches)

        logger.info(
            f"  {self.category} MAP: processing {len(items)} items in {total_batches} batches "
            f"(batch_size={self.BATCH_SIZE}, per_category_concurrency={self.MAX_CONCURRENT_BATCHES})"
        )
        for i, batch in enumerate(batches):
            logger.info(
                f"  {self.category} MAP queue {i + 1}/{total_batches}: "
                f"{len(batch)} items, sample={self._format_batch_sample(batch)}"
            )

        # Process batches with concurrency limit
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_BATCHES)

        async def process_with_semaphore(batch, index):
            async with semaphore:
                return await self._analyze_batch(batch, index, total_batches)

        # Run all batches concurrently (up to MAX_CONCURRENT_BATCHES at a time)
        tasks = [
            process_with_semaphore(batch, i)
            for i, batch in enumerate(batches)
        ]

        batch_results = await asyncio.gather(*tasks)

        # Log batch completion stats
        successful = sum(1 for r in batch_results if r.item_analyses)
        logger.info(f"  {self.category} MAP: complete, {successful}/{total_batches} batches successful")

        return list(batch_results), items

    def _merge_batch_results(
        self,
        batch_results: List[BatchResult],
        items: List[CollectedItem]
    ) -> Tuple[List[AnalyzedItem], List[CategoryTheme], List[str]]:
        """
        REDUCE phase helper: Merge results from all batches.
        """
        # Build lookup of all item analyses
        all_analyses = {}
        all_themes = {}
        all_signals = set()

        for batch in batch_results:
            # Merge item analyses
            for analysis in batch.item_analyses:
                item_id = analysis.get('id')
                if item_id:
                    all_analyses[item_id] = analysis

            # Aggregate themes (combine counts for same theme name)
            for theme in batch.batch_themes:
                theme_name = theme.get('name', '')
                if theme_name:
                    if theme_name in all_themes:
                        # Merge theme data
                        all_themes[theme_name]['item_count'] = all_themes[theme_name].get('item_count', 0) + theme.get('item_count', 0)
                        all_themes[theme_name]['importance'] = max(
                            all_themes[theme_name].get('importance', 50),
                            theme.get('importance', 50)
                        )
                    else:
                        all_themes[theme_name] = theme.copy()

            # Collect cross signals
            all_signals.update(batch.cross_signals)

        # Build AnalyzedItem list
        analyzed_items = []
        for item in items:
            if item.id in all_analyses:
                a = all_analyses[item.id]
                analyzed_items.append(AnalyzedItem(
                    item=item,
                    summary=a.get('summary', ''),
                    importance_score=a.get('importance_score', 50),
                    reasoning=a.get('reasoning', ''),
                    themes=a.get('themes', [])
                ))
            else:
                # Item wasn't analyzed (batch failure)
                analyzed_items.append(AnalyzedItem(
                    item=item,
                    summary=item.content[:200] + '...' if len(item.content) > 200 else item.content,
                    importance_score=30,  # Lower score for unanalyzed items
                    reasoning='Not analyzed (batch processing)',
                    themes=[]
                ))

        # Sort by importance
        analyzed_items.sort(key=lambda x: x.importance_score, reverse=True)

        # Build theme list
        themes = [
            CategoryTheme(
                name=t.get('name', ''),
                description=t.get('description', ''),
                item_count=t.get('item_count', 0),
                example_items=[],
                importance=t.get('importance', 50)
            )
            for t in all_themes.values()
        ]
        themes.sort(key=lambda t: t.importance, reverse=True)

        return analyzed_items, themes, list(all_signals)

    def _build_ranking_context(
        self,
        top_candidates: List[AnalyzedItem],
        themes: List[CategoryTheme]
    ) -> str:
        """Build context for final ranking phase."""
        parts = []

        # Add top candidates
        parts.append("TOP CANDIDATES (by initial score):\n")
        for i, item in enumerate(top_candidates[:30], 1):
            parts.append(f"{i}. [{item.item.id}] {item.item.title}")
            parts.append(f"   Score: {item.importance_score} | {item.reasoning[:100] if item.reasoning else 'N/A'}")
            parts.append(f"   Summary: {item.summary[:150] if item.summary else 'N/A'}")
            parts.append("")

        # Add aggregated themes
        parts.append("\nDETECTED THEMES:\n")
        for theme in themes[:5]:
            parts.append(f"- {theme.name}: {theme.description} ({theme.item_count} items)")

        return "\n".join(parts)

    def _get_ranking_prompt(self, ranking_context: str) -> str:
        """
        Get the ranking prompt for reduce phase.
        Subclasses must override to provide category-specific prompts.
        """
        raise NotImplementedError("Subclasses must implement _get_ranking_prompt")

    async def _reduce_phase(
        self,
        analyzed_items: List[AnalyzedItem],
        themes: List[CategoryTheme],
        cross_signals: List[str],
        batch_thinking: str
    ) -> CategoryReport:
        """
        REDUCE phase: Final ranking and summary generation.

        Takes merged results from map phase and produces final CategoryReport.
        """
        if not analyzed_items:
            return self._empty_report()

        logger.info(f"  {self.category} REDUCE: ranking {len(analyzed_items[:50])} candidates...")

        # Select top candidates for final ranking (top 50 by score)
        top_candidates = analyzed_items[:50]

        # Build ranking context
        ranking_context = self._build_ranking_context(top_candidates, themes)
        ranking_prompt = self._get_ranking_prompt(ranking_context)

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": ranking_prompt}],
                system=self.grounding_context,  # Inject ecosystem grounding
                budget_tokens=self.thinking_budget,  # DEEP
                caller=f"{self.category}_analyzer.reduce_rank"
            )

            ranking_result = self._parse_json_response(response.content)
            ranking_thinking = response.thinking

        except Exception as e:
            logger.error(f"Reduce phase ranking failed: {e}")
            ranking_result = {
                'top_10': [item.item.id for item in top_candidates[:10]],
                'category_summary': f"Analysis complete. Top items selected by score."
            }
            ranking_thinking = ""

        # Get top 10 items by ranking
        top_ids = ranking_result.get('top_10', [])[:10]
        id_to_rank = {id: i for i, id in enumerate(top_ids)}

        # Build top_items list in rank order
        top_items = []
        for id in top_ids:
            for item in analyzed_items:
                if item.item.id == id:
                    top_items.append(item)
                    break

        # Fill to 10 if needed (in case some IDs weren't found)
        if len(top_items) < 10:
            remaining = [i for i in analyzed_items if i not in top_items]
            top_items.extend(remaining[:10 - len(top_items)])

        logger.info(f"  {self.category} REDUCE: complete, {len(top_items)} top items ranked")

        # Log stats
        self._log_map_reduce_stats(analyzed_items, themes, top_items)

        return CategoryReport(
            category=self.category,
            top_items=top_items,
            all_items=analyzed_items,  # ALL items with analysis
            category_summary=ranking_result.get('category_summary', ''),
            themes=themes[:10],  # Top 10 themes
            cross_signals=cross_signals,
            total_collected=len(analyzed_items),
            thinking=f"Batch Analysis:\n{batch_thinking}\n\nRanking:\n{ranking_thinking}"
        )

    def _empty_report(self) -> CategoryReport:
        """Return an empty CategoryReport."""
        return CategoryReport(
            category=self.category,
            top_items=[],
            all_items=[],
            category_summary="No items to analyze.",
            themes=[],
            cross_signals=[],
            total_collected=0
        )

    def _log_map_reduce_stats(
        self,
        analyzed_items: List[AnalyzedItem],
        themes: List[CategoryTheme],
        top_items: List[AnalyzedItem]
    ):
        """Log comprehensive stats for map-reduce pipeline."""
        logger.info(f"═══ {self.category.upper()} MAP-REDUCE STATS ═══")
        logger.info(f"  Total items analyzed: {len(analyzed_items)}")
        logger.info(f"  Themes detected: {len(themes)}")
        if top_items:
            scores = [item.importance_score for item in top_items]
            logger.info(f"  Top 10 score range: {min(scores):.0f}-{max(scores):.0f}")
        logger.info(f"═══════════════════════════════════════")

    @abstractmethod
    async def analyze(self, items: List[CollectedItem]) -> CategoryReport:
        """
        Analyze collected items and produce a category report.

        Args:
            items: List of CollectedItem objects to analyze.

        Returns:
            CategoryReport with analysis results.
        """
        pass

    def _build_item_summary(self, item: CollectedItem) -> str:
        """Build a concise summary of an item for LLM context."""
        return json.dumps(
            {
                "id": item.id,
                "title": self._clip_context_text(item.title),
                "author": self._clip_context_text(item.author),
                "source": item.source,
                "published": item.published,
                "content": self._clip_context_text(item.content, 500),
                "url": item.url,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _clip_context_text(self, value: Any, max_chars: int = 800) -> str:
        """Trim source text before placing it in LLM context."""
        if value is None:
            return ""
        text = str(value).replace("\x00", "")
        return text[:max_chars] + "..." if len(text) > max_chars else text

    def _json_items_context(self, records: List[Dict[str, Any]]) -> str:
        """Render source items as JSON so quote-heavy content stays inert."""
        return json.dumps(records, ensure_ascii=False, indent=2)

    def _build_items_context(self, items: List[CollectedItem], max_items: int = 50) -> str:
        """Build context string from multiple items."""
        records = []
        for position, item in enumerate(items[:max_items], 1):
            records.append({
                "position": position,
                "id": item.id,
                "title": self._clip_context_text(item.title),
                "author": self._clip_context_text(item.author),
                "source": item.source,
                "published": item.published,
                "content": self._clip_context_text(item.content, 500),
                "url": item.url,
            })
        return self._json_items_context(records)

    def load_items(self, filename: str) -> List[CollectedItem]:
        """Load items from a JSON file."""
        filepath = os.path.join(self.data_dir, 'raw', filename)
        if not os.path.exists(filepath):
            logger.warning(f"Data file not found: {filepath}")
            return []

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        items_data = data.get('items', data.get('articles', data.get('papers', [])))
        return [CollectedItem.from_dict(item) for item in items_data]

    def save_report(self, report: CategoryReport, filename: str):
        """Save category report to JSON file."""
        processed_dir = os.path.join(self.data_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)
        filepath = os.path.join(processed_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Saved report to {filepath}")

    def _parse_json_response(self, content: str, expected_items: Optional[int] = None) -> dict:
        """Parse JSON from LLM response, handling various formats.

        Handles:
        - JSON wrapped in ```json ... ``` code blocks
        - JSON wrapped in ``` ... ``` code blocks
        - Raw JSON followed by explanation text
        - JSON with text before it
        """
        content = content.strip()

        # Try to extract JSON from markdown code block first
        code_block_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
        if code_block_match:
            content = code_block_match.group(1).strip()

        # Find start of JSON object or array
        if not content.startswith(('{', '[')):
            obj_start = content.find('{')
            arr_start = content.find('[')
            if obj_start == -1 and arr_start == -1:
                logger.error(f"No JSON found in content: {content[:200]}...")
                return {}
            start = min(s for s in [obj_start, arr_start] if s != -1)
            content = content[start:]

        # Find end of JSON by matching braces/brackets
        open_char = content[0]
        close_char = '}' if open_char == '{' else ']'
        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(content):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
            if not in_string:
                if char == open_char:
                    depth += 1
                elif char == close_char:
                    depth -= 1
                    if depth == 0:
                        content = content[:i+1]
                        break

        # Check for truncation: depth > 0 means JSON is incomplete
        truncated = depth > 0
        if truncated:
            logger.warning(f"JSON appears truncated (unclosed depth={depth}). Last 100 chars: ...{content[-100:]}")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            repaired = self._repair_common_json_errors(content)
            if repaired != content:
                try:
                    logger.warning(f"Repaired malformed JSON response after parse error: {e}")
                    return json.loads(repaired)
                except json.JSONDecodeError as repair_e:
                    logger.warning(f"JSON repair attempt failed: {repair_e}")

            # If the brace-walk left the object unclosed, the decode failure
            # is almost certainly truncation rather than malformed JSON.
            # Surface that to the caller so it can split-and-retry instead
            # of silently dropping the batch.
            if truncated:
                logger.error(f"Truncated JSON (parse failed at {e}); last 100 chars: ...{content[-100:]}")
                raise TruncatedJSONError(str(e)) from e
            recovered = self._recover_malformed_map_response(content, expected_items)
            if recovered is not None:
                return recovered
            logger.error(f"Failed to parse JSON: {e}")
            logger.error(f"JSON error context: {self._json_error_excerpt(content, e.pos)}")
            logger.error(f"Content was: {content[:500]}...")
            raise MalformedJSONError(str(e)) from e

    def _json_error_excerpt(self, content: str, pos: int, radius: int = 220) -> str:
        """Show a small parse-error window without dumping the full response."""
        start = max(0, pos - radius)
        end = min(len(content), pos + radius)
        excerpt = content[start:end].replace("\n", "\\n")
        pointer = " " * (pos - start) + "^"
        return f"...{excerpt}...\n...{pointer}..."

    def _repair_common_json_errors(self, content: str) -> str:
        """Repair common local syntax errors in otherwise complete JSON.

        The LLM sometimes emits a valid-looking object with a missing comma,
        especially in large map batches. Keep repairs narrow so genuinely bad
        responses still fall through to split-and-retry.
        """
        repaired = content

        # Missing comma between adjacent objects in an array:
        # [{"id": "a"} {"id": "b"}] -> [{"id": "a"}, {"id": "b"}]
        repaired = re.sub(r'}\s+(?={)', '}, ', repaired)

        # Missing comma between an object/array value and the next object key:
        # {"items": [...]\n"themes": []} -> {"items": [...], "themes": []}
        repaired = re.sub(
            r'([}\]])\s+(?="(?:items|themes|category_themes|cross_signals|top_10|category_summary)"\s*:)',
            r'\1, ',
            repaired
        )

        # Missing comma between a scalar value and the next known object key:
        # {"reasoning": "text"\n"themes": []} -> {"reasoning": "text", "themes": []}
        key_lookahead = (
            r'(?="(?:id|summary|importance_score|reasoning|themes|name|description|'
            r'item_count|importance|cross_signals|top_10|category_summary)"\s*:)'
        )
        repaired = re.sub(r'("(?:[^"\\]|\\.)*")\s+' + key_lookahead, r'\1, ', repaired)
        repaired = re.sub(
            r'(-?\d+(?:\.\d+)?|true|false|null)\s+' + key_lookahead,
            r'\1, ',
            repaired
        )

        # Trailing commas before object/array close are common and safe to drop.
        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        return repaired

    def _recover_malformed_map_response(
        self,
        content: str,
        expected_items: Optional[int],
        min_ratio: float = 0.7,
    ) -> Optional[Dict[str, Any]]:
        """Recover a mostly complete map response without another LLM call.

        This is intentionally limited to map responses where we know roughly
        how many item objects should be present. If recovery coverage is low,
        callers still split and retry so we do not silently drop most of a
        batch.
        """
        if expected_items is None or expected_items <= 0:
            return None

        items = self._extract_object_array(content, "items")
        if not items:
            return None

        min_items = max(1, int(expected_items * min_ratio))
        if len(items) < min_items:
            logger.warning(
                f"Recovered only {len(items)}/{expected_items} item analyses from malformed JSON; "
                "splitting batch instead"
            )
            return None

        themes = (
            self._extract_object_array(content, "themes")
            or self._extract_object_array(content, "category_themes")
        )
        cross_signals = self._extract_string_array(content, "cross_signals")

        logger.warning(
            f"Recovered {len(items)}/{expected_items} item analyses from malformed JSON "
            "without an extra split retry"
        )
        return {
            "items": items,
            "themes": themes,
            "cross_signals": cross_signals,
        }

    def _find_array_bounds(self, content: str, key: str) -> Optional[Tuple[int, int]]:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', content)
        if not match:
            return None

        start = content.find('[', match.start())
        depth = 0
        in_string = False
        escape_next = False

        for i in range(start, len(content)):
            char = content[i]
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
            if in_string:
                continue
            if char == '[':
                depth += 1
            elif char == ']':
                depth -= 1
                if depth == 0:
                    return start, i + 1

        return start, len(content)

    def _extract_object_array(self, content: str, key: str) -> List[Dict[str, Any]]:
        bounds = self._find_array_bounds(content, key)
        if not bounds:
            return []

        start, end = bounds
        array_text = content[start:end]
        objects = []
        depth = 0
        in_string = False
        escape_next = False
        obj_start = None

        for i, char in enumerate(array_text):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
            if in_string:
                continue
            if char == '{':
                if depth == 0:
                    obj_start = i
                depth += 1
            elif char == '}' and depth > 0:
                depth -= 1
                if depth == 0 and obj_start is not None:
                    raw_object = array_text[obj_start:i + 1]
                    try:
                        objects.append(json.loads(self._repair_common_json_errors(raw_object)))
                    except json.JSONDecodeError:
                        logger.debug(f"Skipping unrecoverable object in malformed {key} array")
                    obj_start = None

        return objects

    def _extract_string_array(self, content: str, key: str) -> List[str]:
        bounds = self._find_array_bounds(content, key)
        if not bounds:
            return []

        start, end = bounds
        try:
            values = json.loads(self._repair_common_json_errors(content[start:end]))
        except json.JSONDecodeError:
            return []
        return [str(value) for value in values if isinstance(value, str)]


def deduplicate_items(items: List[CollectedItem]) -> List[CollectedItem]:
    """
    Deduplicate items based on ID and URL.

    Args:
        items: List of items to deduplicate.

    Returns:
        List of unique items.
    """
    unique = []
    seen_ids: Set[str] = set()
    seen_urls: Set[str] = set()

    for item in items:
        if item.id in seen_ids:
            continue

        normalized_url = item.url.lower().rstrip('/') if item.url else ''
        if normalized_url and normalized_url in seen_urls:
            continue

        unique.append(item)
        seen_ids.add(item.id)
        if normalized_url:
            seen_urls.add(normalized_url)

    logger.info(f"Deduplicated {len(items)} items to {len(unique)} unique items")
    return unique
