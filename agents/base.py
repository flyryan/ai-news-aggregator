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


@dataclass
class CategoryTheme:
    """A detected theme within a category."""
    name: str
    description: str
    item_count: int
    example_items: List[str]  # Item IDs
    importance: float  # 0-100


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
    BATCH_SIZE = 75  # Items per batch for map phase
    MAX_CONCURRENT_BATCHES = 4  # Limit parallel API calls

    # --- Map-Reduce Methods ---

    async def _analyze_batch(
        self,
        batch_items: List[CollectedItem],
        batch_index: int,
        total_batches: int
    ) -> BatchResult:
        """
        MAP phase: Analyze a single batch of items.

        Uses STANDARD thinking for quality per-item analysis.
        """
        items_context = self._build_items_context(batch_items, max_items=len(batch_items))
        prompt = self._get_batch_analysis_prompt(items_context, batch_index, total_batches)

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                system=self.grounding_context,  # Inject ecosystem grounding
                budget_tokens=ThinkingLevel.STANDARD,  # Quality batch processing
                caller=f"{self.category}_analyzer.batch_{batch_index}"
            )

            result = self._parse_json_response(response.content)

            return BatchResult(
                batch_index=batch_index,
                item_analyses=result.get('items', []),
                batch_themes=result.get('themes', result.get('category_themes', [])),
                cross_signals=result.get('cross_signals', []),
                thinking=response.thinking
            )
        except Exception as e:
            logger.error(f"Batch {batch_index} analysis failed: {e}")
            # Retry once with backoff
            try:
                await asyncio.sleep(5)
                response = await self.async_client.call_with_thinking(
                    messages=[{"role": "user", "content": prompt}],
                    system=self.grounding_context,  # Inject ecosystem grounding
                    budget_tokens=ThinkingLevel.STANDARD,
                    caller=f"{self.category}_analyzer.batch_{batch_index}_retry"
                )
                result = self._parse_json_response(response.content)
                return BatchResult(
                    batch_index=batch_index,
                    item_analyses=result.get('items', []),
                    batch_themes=result.get('themes', result.get('category_themes', [])),
                    cross_signals=result.get('cross_signals', []),
                    thinking=response.thinking
                )
            except Exception as retry_e:
                logger.error(f"Batch {batch_index} retry also failed: {retry_e}")
                return BatchResult(
                    batch_index=batch_index,
                    item_analyses=[],
                    batch_themes=[],
                    cross_signals=[],
                    thinking=f"Error: {e}, Retry error: {retry_e}"
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

        logger.info(f"MAP phase: Processing {len(items)} items in {total_batches} batches")

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
        logger.info(f"MAP phase complete: {successful}/{total_batches} batches successful")

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
        parts = [f"Title: {item.title}"]
        if item.author:
            parts.append(f"Author: {item.author}")
        parts.append(f"Source: {item.source}")
        parts.append(f"Published: {item.published}")
        if item.content:
            # Truncate content for context
            content = item.content[:500] + "..." if len(item.content) > 500 else item.content
            parts.append(f"Content: {content}")
        if item.url:
            parts.append(f"URL: {item.url}")
        return "\n".join(parts)

    def _build_items_context(self, items: List[CollectedItem], max_items: int = 50) -> str:
        """Build context string from multiple items."""
        context_parts = []
        for i, item in enumerate(items[:max_items], 1):
            context_parts.append(f"--- Item {i} ---")
            context_parts.append(self._build_item_summary(item))
            context_parts.append("")
        return "\n".join(context_parts)

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

    def _parse_json_response(self, content: str) -> dict:
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
        if depth > 0:
            logger.warning(f"JSON appears truncated (unclosed depth={depth}). Last 100 chars: ...{content[-100:]}")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            logger.error(f"Content was: {content[:500]}...")
            return {}


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
