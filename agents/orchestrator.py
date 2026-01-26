"""
Main Orchestrator

Coordinates all gatherer and analyzer agents, detects cross-category topics,
assembles the final report, and triggers HTML generation.
"""

import asyncio
import logging
import os
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional

from .llm_client import AnthropicClient, AsyncAnthropicClient, ThinkingLevel, LLMResponse
from .base import (
    BaseGatherer, BaseAnalyzer, CollectedItem, AnalyzedItem,
    CategoryReport, CategoryTheme, deduplicate_items
)
from .gatherers import NewsGatherer, ResearchGatherer, SocialGatherer, RedditGatherer, LinkFollower
from .analyzers import NewsAnalyzer, ResearchAnalyzer, SocialAnalyzer, RedditAnalyzer
from .cost_tracker import get_tracker, reset_tracker
from .link_enricher import LinkEnricher
from .ecosystem_context import EcosystemContextManager
from .config import ProviderConfig
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config.prompts import PromptAccessor

# Import hero generator (optional, may not be available in all environments)
try:
    from generators.hero_generator import HeroGenerator, initialize_hero_generator
    HERO_GENERATOR_AVAILABLE = True
except ImportError:
    HERO_GENERATOR_AVAILABLE = False
    initialize_hero_generator = None

logger = logging.getLogger(__name__)


@dataclass
class TopTopic:
    """A cross-category topic detected by the orchestrator."""
    name: str
    description: str  # Plain text description
    description_html: str  # Description with inline HTML links
    category_breakdown: Dict[str, int]  # category -> item count
    representative_items: List[str]  # Item IDs from each category
    importance: float  # 0-100


@dataclass
class OrchestratorResult:
    """Final result produced by the orchestrator."""
    date: str  # Report date (YYYY-MM-DD)
    executive_summary: str
    top_topics: List[TopTopic]
    category_reports: Dict[str, CategoryReport]  # category -> report
    total_items_collected: int
    total_items_analyzed: int
    coverage_date: str = ''  # Date of news coverage (day before report date)
    coverage_start: str = ''  # ISO datetime string for coverage start
    coverage_end: str = ''  # ISO datetime string for coverage end
    collection_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # source -> status
    hero_image_url: Optional[str] = None  # URL path to generated hero image
    hero_image_prompt: Optional[str] = None  # Prompt used to generate hero image
    orchestrator_thinking: Optional[str] = None
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'date': self.date,
            'coverage_date': self.coverage_date,
            'coverage_start': self.coverage_start,
            'coverage_end': self.coverage_end,
            'executive_summary': self.executive_summary,
            'top_topics': [asdict(topic) for topic in self.top_topics],
            'category_reports': {k: v.to_dict() for k, v in self.category_reports.items()},
            'total_items_collected': self.total_items_collected,
            'total_items_analyzed': self.total_items_analyzed,
            'collection_status': self.collection_status,
            'hero_image_url': self.hero_image_url,
            'hero_image_prompt': self.hero_image_prompt,
            'orchestrator_thinking': self.orchestrator_thinking,
            'generated_at': self.generated_at
        }


class MainOrchestrator:
    """
    Main orchestrator that coordinates all agents.

    Flow:
    0. Ecosystem Context (fetch AI model state for grounding)
    1. Parallel Gathering (4 gatherers)
    2. Parallel Analysis (4 analyzers with grounding context)
    3. Cross-Category Topic Detection (ULTRATHINK)
    4. Main Page Assembly
    5. Deduplication & QC
    6. HTML Generation
    """

    def __init__(
        self,
        config_dir: str = './config',
        data_dir: str = './data',
        web_dir: str = './web',
        lookback_hours: int = 24,
        target_date: Optional[str] = None,
        provider_config: Optional[ProviderConfig] = None,
        prompt_accessor: Optional['PromptAccessor'] = None
    ):
        """
        Initialize orchestrator.

        Args:
            config_dir: Directory containing configuration files.
            data_dir: Directory for storing data.
            web_dir: Directory for generated HTML.
            lookback_hours: Hours to look back for items.
            target_date: Specific date to collect (YYYY-MM-DD format).
            provider_config: Provider configuration. If None, loads from env vars.
            prompt_accessor: Optional PromptAccessor for config-based prompts.
        """
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.web_dir = web_dir
        self.lookback_hours = lookback_hours
        self.target_date = target_date or self._get_today()
        self.provider_config = provider_config
        self.prompt_accessor = prompt_accessor

        # Initialize LLM clients from config
        if provider_config:
            self.llm_client = AnthropicClient.from_config(provider_config.llm)
            self.async_client = AsyncAnthropicClient.from_config(provider_config.llm)
        else:
            # Fallback to env vars for backwards compatibility
            self.llm_client = AnthropicClient()
            self.async_client = AsyncAnthropicClient()

        # Initialize gatherers
        self.gatherers: Dict[str, BaseGatherer] = {
            'news': NewsGatherer(
                config_dir=config_dir,
                data_dir=data_dir,
                lookback_hours=lookback_hours,
                target_date=self.target_date,
                llm_client=self.llm_client,  # For link following
                prompt_accessor=prompt_accessor
            ),
            'research': ResearchGatherer(
                config_dir=config_dir,
                data_dir=data_dir,
                lookback_hours=lookback_hours,
                target_date=self.target_date
            ),
            'social': SocialGatherer(
                config_dir=config_dir,
                data_dir=data_dir,
                lookback_hours=lookback_hours,
                target_date=self.target_date
            ),
            'reddit': RedditGatherer(
                config_dir=config_dir,
                data_dir=data_dir,
                lookback_hours=lookback_hours,
                target_date=self.target_date
            )
        }

        # Initialize analyzers
        self.analyzers: Dict[str, BaseAnalyzer] = {
            'news': NewsAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=data_dir,
                prompt_accessor=prompt_accessor
            ),
            'research': ResearchAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=data_dir,
                prompt_accessor=prompt_accessor
            ),
            'social': SocialAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=data_dir,
                prompt_accessor=prompt_accessor
            ),
            'reddit': RedditAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=data_dir,
                prompt_accessor=prompt_accessor
            )
        }

        # Initialize hero generator if available AND configured
        self.hero_generator: Optional['HeroGenerator'] = None
        if HERO_GENERATOR_AVAILABLE and initialize_hero_generator:
            image_config = provider_config.image if provider_config else None
            self.hero_generator = initialize_hero_generator(image_config)

        # Initialize ecosystem context manager (Phase 0)
        from pathlib import Path
        self.ecosystem_manager = EcosystemContextManager(Path(config_dir), prompt_accessor=prompt_accessor)
        self.grounding_context: Optional[str] = None  # Set in run()

        logger.info(f"Orchestrator initialized for {self.target_date}")

    def _get_today(self) -> str:
        """Get today's date as YYYY-MM-DD."""
        return datetime.now().strftime('%Y-%m-%d')

    async def run(self) -> OrchestratorResult:
        """
        Run the full pipeline.

        Returns:
            OrchestratorResult with all analysis.
        """
        logger.info(f"Starting orchestrator run for {self.target_date}")
        start_time = datetime.now()

        # Initialize cost tracking
        tracker = reset_tracker()
        tracker.start()

        # Phase 0: Ecosystem Context (grounding for LLM analysis)
        logger.info("Phase 0: Loading ecosystem context...")
        from datetime import date as date_type
        report_date = date_type.fromisoformat(self.target_date)
        self.grounding_context = await self.ecosystem_manager.initialize(report_date)

        # Phase 1: Parallel Gathering
        logger.info("Phase 1: Gathering from all sources...")
        gathered_items, collection_status = await self._gather_all()

        # Phase 2: Parallel Analysis (with grounding context)
        logger.info("Phase 2: Analyzing all categories...")
        category_reports = await self._analyze_all(gathered_items)

        # Phase 2.5: Continuity Detection
        logger.info("Phase 2.5: Detecting story continuations...")
        from .continuity import ContinuityCoordinator
        continuity_coordinator = ContinuityCoordinator(
            async_client=self.async_client,
            web_dir=self.web_dir,
            target_date=self.target_date,
            lookback_days=2
        )
        category_reports = await continuity_coordinator.process(category_reports)

        # Phase 3: Cross-Category Topic Detection
        logger.info("Phase 3: Detecting cross-category topics...")
        top_topics, topic_thinking = await self._detect_cross_category_topics(category_reports)

        # Phase 4: Generate Executive Summary
        logger.info("Phase 4: Generating executive summary...")
        executive_summary, summary_thinking = await self._generate_executive_summary(
            category_reports, top_topics
        )

        # Phase 4.5: Link Enrichment
        logger.info("Phase 4.5: Enriching summaries with internal links...")
        enricher = LinkEnricher(self.async_client, self.target_date, prompt_accessor=self.prompt_accessor)
        executive_summary, enriched_category_summaries, top_topics = await enricher.enrich_all(
            executive_summary, category_reports, top_topics
        )

        # Update category reports with enriched summaries
        for category, enriched_summary in enriched_category_summaries.items():
            if category in category_reports:
                category_reports[category].category_summary = enriched_summary

        # Phase 4.6: Ecosystem Enrichment (detect new model releases from news)
        if self.ecosystem_manager and 'news' in category_reports:
            logger.info("Phase 4.6: Enriching ecosystem context from news...")
            try:
                news_items = category_reports['news'].all_items
                enrichment_result = await self.ecosystem_manager.enrich_from_news(
                    news_items, self.async_client
                )
                if enrichment_result.get('updates_made', 0) > 0:
                    logger.info(f"  Added {enrichment_result['updates_made']} new model releases")
            except Exception as e:
                logger.warning(f"Ecosystem enrichment failed: {e}")

        # Phase 4.7: Hero Image Generation
        hero_image_url = None
        hero_image_prompt = None
        if self.hero_generator and top_topics:
            logger.info("Phase 4.7: Generating hero image...")
            try:
                from pathlib import Path
                hero_result = await self.hero_generator.generate(
                    top_topics=top_topics,
                    date=self.target_date,
                    output_dir=Path(self.web_dir)
                )
                if hero_result:
                    hero_image_url = hero_result['path']
                    # Add cache-busting timestamp based on file mtime
                    hero_file = Path(self.web_dir) / "data" / self.target_date / "hero.webp"
                    if hero_file.exists():
                        mtime = int(hero_file.stat().st_mtime)
                        hero_image_url = f"{hero_image_url}?v={mtime}"
                    hero_image_prompt = hero_result['prompt']
                    logger.info(f"Hero image generated: {hero_image_url}")
                else:
                    logger.warning("Hero image generation returned no result")
            except Exception as e:
                logger.error(f"Hero image generation failed: {e}")
        else:
            if not self.hero_generator:
                logger.info("Skipping hero image generation (generator not available)")
            elif not top_topics:
                logger.info("Skipping hero image generation (no topics)")

        # Phase 5: Assemble Result
        logger.info("Phase 5: Assembling final result...")
        total_collected = sum(len(items) for items in gathered_items.values())
        total_analyzed = sum(len(report.all_items) for report in category_reports.values())

        # Get coverage info from any gatherer (all have the same dates)
        any_gatherer = next(iter(self.gatherers.values()))
        coverage_date = getattr(any_gatherer, 'coverage_date', '')
        coverage_start = any_gatherer.start_time.isoformat() if any_gatherer.start_time else ''
        coverage_end = any_gatherer.end_time.isoformat() if any_gatherer.end_time else ''

        result = OrchestratorResult(
            date=self.target_date,
            executive_summary=executive_summary,
            top_topics=top_topics,
            category_reports=category_reports,
            total_items_collected=total_collected,
            total_items_analyzed=total_analyzed,
            coverage_date=coverage_date,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            collection_status=collection_status,
            hero_image_url=hero_image_url,
            hero_image_prompt=hero_image_prompt,
            orchestrator_thinking=f"Topic Detection:\n{topic_thinking}\n\nSummary:\n{summary_thinking}"
        )

        # Save result
        self._save_result(result)

        # Stop cost tracking and print summary
        tracker.stop()

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Orchestrator run completed in {elapsed:.1f}s")
        logger.info(f"  - Total collected: {total_collected}")
        logger.info(f"  - Total analyzed: {total_analyzed}")
        logger.info(f"  - Top topics: {len(top_topics)}")

        # Log collection status summary
        self._log_collection_status(collection_status)

        # Print cost report
        print("\n" + tracker.get_summary())

        # Save cost report
        cost_report_path = os.path.join(
            self.data_dir, 'processed',
            f"cost_report_{self.target_date}.json"
        )
        tracker.save_report(cost_report_path)

        return result

    async def _gather_all(self) -> tuple:
        """
        Run all gatherers with proper coordination for link following.

        The workflow is:
        1. Run papers, reddit, and social gatherers in parallel
        2. Pass social posts to news gatherer for link extraction
        3. Run news gatherer (which also collects RSS)

        Returns:
            Tuple of (Dict mapping category to list of collected items, collection_status dict).
        """
        results = {}
        collection_status = {}

        # Phase 1: Run papers, reddit, and social in parallel
        logger.info("  Phase 1: Gathering papers, reddit, social...")

        async def gather_category(name: str) -> tuple:
            gatherer = self.gatherers[name]
            try:
                items = await gatherer.gather()
                logger.info(f"    {name} gatherer collected {len(items)} items")
                return name, items, None
            except Exception as e:
                logger.error(f"    {name} gatherer failed: {e}")
                return name, [], str(e)

        phase1_tasks = [
            gather_category('research'),
            gather_category('reddit'),
            gather_category('social')
        ]
        phase1_results = await asyncio.gather(*phase1_tasks)

        for name, items, error in phase1_results:
            results[name] = items
            if error:
                collection_status[name] = {'status': 'failed', 'count': 0, 'error': error}
            else:
                collection_status[name] = {'status': 'success', 'count': len(items), 'error': None}

        # Capture social sub-platform status from SocialGatherer
        social_gatherer = self.gatherers.get('social')
        if social_gatherer and hasattr(social_gatherer, 'get_collection_status'):
            social_platform_status = social_gatherer.get_collection_status()
            for platform, status in social_platform_status.items():
                collection_status[f'social_{platform}'] = status

        # Phase 2: Run news gatherer with social posts for link following
        logger.info("  Phase 2: Gathering news with link following...")
        social_posts = results.get('social', [])

        try:
            news_gatherer = self.gatherers['news']
            news_items = await news_gatherer.gather(social_posts=social_posts)
            logger.info(f"    news gatherer collected {len(news_items)} items")
            results['news'] = news_items
            collection_status['news'] = {'status': 'success', 'count': len(news_items), 'error': None}
        except Exception as e:
            logger.error(f"    news gatherer failed: {e}")
            results['news'] = []
            collection_status['news'] = {'status': 'failed', 'count': 0, 'error': str(e)}

        return results, collection_status

    async def _analyze_all(
        self,
        gathered_items: Dict[str, List[CollectedItem]]
    ) -> Dict[str, CategoryReport]:
        """
        Run all analyzers in parallel with grounding context.

        Args:
            gathered_items: Dict mapping category to collected items.

        Returns:
            Dict mapping category to CategoryReport.
        """
        # Re-instantiate analyzers with grounding context
        # (they were created in __init__ without it)
        analyzers_with_context = {
            'news': NewsAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=self.data_dir,
                grounding_context=self.grounding_context
            ),
            'research': ResearchAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=self.data_dir,
                grounding_context=self.grounding_context
            ),
            'social': SocialAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=self.data_dir,
                grounding_context=self.grounding_context
            ),
            'reddit': RedditAnalyzer(
                llm_client=self.llm_client,
                async_client=self.async_client,
                data_dir=self.data_dir,
                grounding_context=self.grounding_context
            )
        }

        async def analyze_category(
            name: str,
            analyzer: BaseAnalyzer,
            items: List[CollectedItem]
        ) -> tuple:
            logger.info(f"  Starting {name} analyzer with {len(items)} items...")
            try:
                report = await analyzer.analyze(items)
                logger.info(f"  {name} analyzer completed. Top items: {len(report.top_items)}")
                return name, report
            except Exception as e:
                logger.error(f"  {name} analyzer failed: {e}")
                # Return empty report on failure
                return name, CategoryReport(
                    category=name,
                    top_items=[],
                    all_items=[],
                    category_summary=f"Analysis failed: {e}",
                    themes=[],
                    cross_signals=[],
                    total_collected=len(items)
                )

        # Run all analyzers in parallel
        tasks = [
            analyze_category(name, analyzers_with_context[name], gathered_items.get(name, []))
            for name in analyzers_with_context.keys()
        ]
        results = await asyncio.gather(*tasks)

        return dict(results)

    def _markdown_links_to_html(self, text: str) -> str:
        """Convert markdown links [text](url) to HTML <a> tags."""
        import re
        pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        return re.sub(pattern, r'<a href="\2" target="_blank">\1</a>', text)

    async def _detect_cross_category_topics(
        self,
        category_reports: Dict[str, CategoryReport]
    ) -> tuple:
        """
        Detect topics that span multiple categories.

        Uses ULTRATHINK for deep analysis across all categories.

        Returns:
            Tuple of (list of TopTopic, thinking string).
        """
        # Build context from all category reports with URLs for linking
        context_parts = []
        for category, report in category_reports.items():
            context_parts.append(f"=== {category.upper()} ===")
            context_parts.append(f"Summary: {report.category_summary}")
            context_parts.append(f"Themes: {', '.join(t.name for t in report.themes)}")
            context_parts.append(f"Top items ({len(report.top_items)}):")
            for i, item in enumerate(report.top_items[:10], 1):
                # Include URL so LLM can create inline links
                context_parts.append(f"  {i}. {item.item.title}")
                context_parts.append(f"     URL: {item.item.url}")
                context_parts.append(f"     Source: {item.item.source}")
                if item.summary:
                    context_parts.append(f"     Summary: {item.summary[:150]}...")
            context_parts.append("")

        context = "\n".join(context_parts)

        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_orchestration_prompt(
                'topic_detection', {'context': context}
            )
        else:
            # Fallback to inline prompt for backwards compatibility
            prompt = f"""Analyze the following category reports from today's AI news collection and identify the TOP 6 cross-category topics that appear across multiple categories. If there are fewer than 6 distinct topics worth covering, return exactly 3 instead.

{context}

For each cross-category topic, provide:
1. A concise name (2-5 words)
2. A description (2-3 sentences) as PLAIN TEXT without any links
   - DO NOT include any markdown links or URLs
   - Write factual claims that reference specific sources by name
   - Links will be added automatically in a later processing step
3. Which categories it appears in and roughly how many items
4. An importance score (0-100)

IMPORTANT: Write descriptions as plain text WITHOUT any links. Reference sources by name (e.g., "Google announced...", "A Stanford paper found...") but do NOT include URLs or markdown link syntax.

Example description format:
"Google announced a major breakthrough in reasoning models, while researchers at Stanford published findings showing improved benchmark performance. The Reddit community has been actively discussing implications."

Return your analysis as JSON:
```json
{{
  "topics": [
    {{
      "name": "Topic Name",
      "description": "Plain text description referencing sources by name without any links.",
      "categories": {{"news": 5, "papers": 2, "social": 10, "reddit": 3}},
      "importance": 85
    }}
  ]
}}
```

Focus on genuinely cross-cutting themes representing the day's most significant AI developments."""

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                system=self.grounding_context,  # Inject ecosystem grounding
                budget_tokens=ThinkingLevel.ULTRATHINK,
                caller="orchestrator.topics"
            )

            # Parse JSON response
            result = json.loads(
                response.content.strip().strip('```json').strip('```').strip()
            )

            topics = []
            for topic_data in result.get('topics', []):
                description = topic_data.get('description', '')
                topics.append(TopTopic(
                    name=topic_data['name'],
                    description=description,
                    description_html=self._markdown_links_to_html(description),
                    category_breakdown=topic_data.get('categories', {}),
                    representative_items=[],
                    importance=topic_data.get('importance', 50)
                ))

            # Sort by importance
            topics.sort(key=lambda t: t.importance, reverse=True)

            return topics, response.thinking or ""

        except Exception as e:
            logger.error(f"Cross-category topic detection failed: {e}")
            return [], f"Error: {e}"

    async def _generate_executive_summary(
        self,
        category_reports: Dict[str, CategoryReport],
        top_topics: List[TopTopic]
    ) -> tuple:
        """
        Generate an executive summary of all AI news for the day.

        Uses DEEP thinking for quality synthesis.

        Returns:
            Tuple of (summary string, thinking string).
        """
        # Build context
        context_parts = [f"Date: {self.target_date}", ""]

        context_parts.append("TOP TOPICS:")
        for i, topic in enumerate(top_topics[:6], 1):
            context_parts.append(f"{i}. {topic.name}: {topic.description}")
        context_parts.append("")

        for category, report in category_reports.items():
            context_parts.append(f"--- {category.upper()} ---")
            context_parts.append(f"Summary: {report.category_summary}")
            if report.top_items:
                context_parts.append("Top story: " + report.top_items[0].item.title)
            context_parts.append("")

        context = "\n".join(context_parts)

        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_orchestration_prompt(
                'executive_summary', {'context': context}
            )
        else:
            # Fallback to inline prompt for backwards compatibility
            prompt = f"""Write a structured executive summary of today's AI news using markdown formatting.

{context}

FORMAT YOUR SUMMARY LIKE THIS:

#### Top Story
One sentence about the most important development of the day.

#### Key Developments
- **[Company/Product Name]**: Brief description of what happened (1 sentence)
- **[Company/Product Name]**: Brief description (1 sentence)
- (Include 3-5 bullet points for the main developments)

#### Safety & Regulation
- Brief bullet points on any safety, ethics, or regulatory news (omit section if none)

#### Research Highlights
- Brief bullet points on notable research or papers (omit section if none)

#### Looking Ahead
One sentence on forward-looking implications or what to watch.

FORMATTING RULES:
- Use **bold** for company names (OpenAI, Google, Anthropic), product names (GPT-4, Claude, Gemini), model names, and key numbers/stats
- Use #### for section headers
- Use - for bullet points
- Keep each bullet to 1-2 sentences maximum
- Only include sections that have relevant content today (skip empty sections)
- Write in factual, professional tone - no hype or speculation
- Do NOT use phrases like "significant developments", "noteworthy", or "thought leaders"

The summary should help a busy professional quickly scan and understand what matters in AI today."""

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                system=self.grounding_context,  # Inject ecosystem grounding
                budget_tokens=ThinkingLevel.DEEP,
                caller="orchestrator.summary"
            )

            return response.content, response.thinking or ""

        except Exception as e:
            logger.error(f"Executive summary generation failed: {e}")
            return f"Executive summary generation failed: {e}", f"Error: {e}"

    def _save_result(self, result: OrchestratorResult):
        """Save orchestrator result to JSON file."""
        processed_dir = os.path.join(self.data_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)

        filename = f"orchestrator_result_{self.target_date}.json"
        filepath = os.path.join(processed_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Saved orchestrator result to {filepath}")

    def _log_collection_status(self, collection_status: Dict[str, Dict[str, Any]]):
        """Log collection status summary with clear indicators."""
        logger.info("\n" + "=" * 60)
        logger.info("COLLECTION STATUS SUMMARY")
        logger.info("=" * 60)

        has_failures = False
        has_partial = False

        # Group by category vs sub-platform
        main_sources = ['news', 'research', 'social', 'reddit']
        sub_platforms = [k for k in collection_status.keys() if k.startswith('social_')]

        for source in main_sources:
            status = collection_status.get(source, {})
            self._log_source_status(source, status)
            if status.get('status') == 'failed':
                has_failures = True
            elif status.get('status') == 'partial':
                has_partial = True

        if sub_platforms:
            logger.info("\n  Social Platform Breakdown:")
            for platform in sorted(sub_platforms):
                status = collection_status.get(platform, {})
                platform_name = platform.replace('social_', '').capitalize()
                self._log_source_status(f"    {platform_name}", status, indent=True)
                if status.get('status') == 'failed':
                    has_failures = True
                elif status.get('status') == 'partial':
                    has_partial = True

        logger.info("=" * 60)

        # Clear warning at the end if there were issues
        if has_failures:
            logger.warning("⚠️  SOME SOURCES FAILED TO COLLECT - check errors above")
        elif has_partial:
            logger.warning("⚠️  SOME SOURCES HAD PARTIAL COLLECTION - check warnings above")
        else:
            logger.info("✅ All sources collected successfully")

    def _log_source_status(self, source: str, status: Dict[str, Any], indent: bool = False):
        """Log status for a single source."""
        prefix = "  " if indent else ""
        status_val = status.get('status', 'unknown')
        count = status.get('count', 0)
        error = status.get('error')

        if status_val == 'success':
            logger.info(f"{prefix}✓ {source}: {count} items")
        elif status_val == 'partial':
            logger.warning(f"{prefix}⚠ {source}: {count} items (partial - {error})")
        elif status_val == 'failed':
            logger.error(f"{prefix}✗ {source}: FAILED - {error}")
        elif status_val == 'skipped':
            logger.info(f"{prefix}- {source}: skipped ({error})")
        else:
            logger.warning(f"{prefix}? {source}: unknown status")

    async def close(self):
        """Close LLM clients."""
        self.llm_client.close()
        await self.async_client.close()
