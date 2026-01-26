"""
News Analyzer - Analyzes news articles and blog posts.

Focuses on FRONTIER AI news only:
- Model releases (GPT, Claude, Gemini, Llama, etc.)
- Provider announcements (OpenAI, Anthropic, Google, Meta, etc.)
- New AI products and tools
- Major breakthroughs and research milestones
- Significant AI company news (funding >$100M, acquisitions, leadership)
"""

import json
import logging
from typing import List, Optional, Set

from ..base import (
    BaseAnalyzer, CollectedItem, AnalyzedItem,
    CategoryReport, CategoryTheme
)
from ..llm_client import AnthropicClient, AsyncAnthropicClient, ThinkingLevel

logger = logging.getLogger(__name__)


class NewsAnalyzer(BaseAnalyzer):
    """Analyzes news articles with extended thinking and map-reduce batching."""

    # Batch analysis prompt for map phase (used after filtering)
    BATCH_ANALYSIS_PROMPT = """You are an AI news analyst covering the frontier of artificial intelligence.
Analyze these AI news articles (batch {batch_index} of {total_batches}).

For each article, provide:
1. A concise summary (2-3 sentences) focusing on what's new/significant
2. An importance score (0-100) based on FRONTIER AI significance
3. Brief reasoning for the score
4. Relevant themes

Articles:
{items_context}

Return your analysis as JSON:
```json
{{
  "items": [
    {{"id": "item_id", "summary": "...", "importance_score": 85, "reasoning": "...", "themes": ["theme1", "theme2"]}}
  ],
  "themes": [
    {{"name": "Theme Name", "description": "...", "item_count": 5, "importance": 80}}
  ],
  "cross_signals": ["signal1", "signal2"]
}}
```

Prioritize: model releases, breakthrough capabilities, major product launches, significant funding (>$100M), AI policy news, open source releases, safety developments.
Deprioritize: routine updates, minor features, opinion pieces, rehashed coverage."""

    # LLM filter for frontier AI relevance
    FILTER_PROMPT = """You are filtering news articles for a FRONTIER AI newsletter.

Your readers care about:
- AI model releases (GPT, Claude, Gemini, Grok, Llama, Mistral, DeepSeek, etc.)
- AI company news (OpenAI, Anthropic, Google AI, xAI, Meta AI, etc.)
- AI products, tools, and capabilities
- AI research breakthroughs and papers
- AI safety, ethics, and alignment
- AI regulation and policy
- AI infrastructure (chips, training clusters)

Your readers do NOT care about:
- Space/astronomy (SpaceX satellites, planets)
- General health news (unless AI diagnosis/treatment)
- Tech job market news
- University/education news (unless AI research)
- Government electronics/manufacturing (unless AI-specific)
- Generic "AI in marketing" fluff pieces

Articles:
{items_context}

Return the IDs of articles relevant to frontier AI:
```json
{{"ai_article_ids": ["{example_id}", ...]}}
```

Be inclusive of AI safety issues, controversies, and negative news about AI companies - these are still frontier AI news."""

    # Combined analysis + ranking prompt for small batches (< 75 items)
    COMBINED_ANALYSIS_PROMPT = """You are an AI news analyst covering the frontier of artificial intelligence.

Analyze these {count} AI news articles and rank the top 10 most important.

For each article, provide:
1. A concise summary (2-3 sentences) focusing on what's new/significant
2. An importance score (0-100) based on FRONTIER AI significance:
   - 90-100: Major model releases, breakthrough announcements, industry-shaking news
   - 70-89: Significant product launches, notable research, important company news
   - 50-69: Interesting developments, useful tools, incremental progress
   - Below 50: Minor updates, routine news
3. Brief reasoning for the score
4. Relevant themes

Then select the top 10 most important stories and write a category summary.

Articles:
{items_context}

Return your analysis as JSON:
```json
{{
  "items": [
    {{"id": "item_id", "summary": "...", "importance_score": 85, "reasoning": "...", "themes": ["theme1", "theme2"]}}
  ],
  "top_10": ["id1", "id2", "id3", "id4", "id5", "id6", "id7", "id8", "id9", "id10"],
  "category_summary": "Structured summary using markdown formatting (see rules below)",
  "themes": [
    {{"name": "Theme Name", "description": "...", "item_count": 5, "importance": 80}}
  ]
}}
```

PRIORITIZE (high scores):
- New model releases from major labs
- Breakthrough capabilities or benchmarks
- Major product launches with AI features
- Significant funding rounds (>$100M) for AI companies
- Important AI policy/regulation news
- Open source model releases
- Notable AI safety developments

DEPRIORITIZE (lower scores):
- Routine company updates
- Minor feature additions
- Opinion pieces without news value
- Rehashed coverage of old news

CATEGORY SUMMARY FORMATTING RULES:
- Use **bold** for company names, product names, model names, and key numbers
- Use bullet points (- ) for lists of related developments
- Group similar items together by theme
- Keep sentences concise (under 30 words each)
- Maximum 2-3 short paragraphs OR equivalent bullet content
- Write in factual, professional tone"""

    ANALYSIS_PROMPT = """You are an AI news analyst covering the frontier of artificial intelligence.

Analyze these AI news articles. For each, provide:
1. A concise summary (2-3 sentences) focusing on what's new/significant
2. An importance score (0-100) based on FRONTIER AI significance:
   - 90-100: Major model releases, breakthrough announcements, industry-shaking news
   - 70-89: Significant product launches, notable research, important company news
   - 50-69: Interesting developments, useful tools, incremental progress
   - Below 50: Minor updates, routine news
3. Brief reasoning for the score
4. Relevant themes

Articles to analyze:
{items_context}

Return your analysis as JSON:
```json
{{
  "items": [
    {{
      "id": "item_id",
      "summary": "...",
      "importance_score": 85,
      "reasoning": "...",
      "themes": ["theme1", "theme2"]
    }}
  ],
  "category_themes": [
    {{
      "name": "Theme Name",
      "description": "...",
      "item_count": 5,
      "importance": 80
    }}
  ],
  "cross_signals": ["signal1", "signal2"]
}}
```

PRIORITIZE (high scores):
- New model releases from major labs
- Breakthrough capabilities or benchmarks
- Major product launches with AI features
- Significant funding rounds (>$100M) for AI companies
- Important AI policy/regulation news
- Open source model releases
- Notable AI safety developments

DEPRIORITIZE (lower scores):
- Routine company updates
- Minor feature additions
- Opinion pieces without news value
- Rehashed coverage of old news"""

    RANKING_PROMPT = """Rank the top 10 most important AI news stories.

Analysis results:
{analysis_summary}

Ranking criteria (in order of importance):
1. FRONTIER SIGNIFICANCE: Does this advance the state of AI?
2. INDUSTRY IMPACT: Will this affect how AI is built or used?
3. NEWS VALUE: Is this breaking news or major announcement?
4. SOURCE QUALITY: Is this from a reliable source with direct information?

Return your ranking as JSON:
```json
{{
  "top_10": ["id1", "id2", ...],
  "category_summary": "Structured summary using markdown formatting (see rules below)"
}}
```

CATEGORY SUMMARY FORMATTING RULES:
- Use **bold** for company names, product names, model names, and key numbers
- Use bullet points (- ) for lists of related developments
- Group similar items together by theme
- Keep sentences concise (under 30 words each)
- Maximum 2-3 short paragraphs OR equivalent bullet content
- Write in factual, professional tone

Example format:
"**Nvidia** dominated AI infrastructure news with six new chip announcements. **Jensen Huang** confirmed Vera Rubin chips are in full production with promised cost reductions for training and inference.

- **Google DeepMind** and **Boston Dynamics** announced Gemini integration into Atlas robots
- **Cosmos Reason 2** brings advanced reasoning to physical AI applications
- **xAI** faces safety concerns after Grok was found generating problematic content"

The summary should read like a professional briefing, focusing on what matters for people following frontier AI."""

    # Keywords for fast pre-filtering
    AI_KEYWORDS = {
        'ai', 'artificial intelligence', 'machine learning', 'ml', 'llm',
        'gpt', 'claude', 'gemini', 'grok', 'llama', 'mistral', 'openai',
        'anthropic', 'deepmind', 'meta ai', 'chatbot', 'neural network',
        'deep learning', 'transformer', 'language model', 'generative',
        'diffusion', 'stable diffusion', 'midjourney', 'dall-e', 'copilot',
        'chatgpt', 'bard', 'perplexity', 'hugging face', 'huggingface',
        'rlhf', 'alignment', 'benchmark', 'inference', 'embedding',
        'agi', 'superintelligence', 'multimodal', 'reasoning model',
        'foundation model', 'frontier model', 'xai', 'cohere', 'databricks',
        'deepseek', 'qwen', 'phi-', 'phi 3', 'phi 4', 'nvidia ai'
    }

    def __init__(
        self,
        llm_client: Optional[AnthropicClient] = None,
        async_client: Optional[AsyncAnthropicClient] = None,
        data_dir: str = './data',
        grounding_context: Optional[str] = None,
        prompt_accessor=None
    ):
        super().__init__(llm_client, async_client, data_dir, grounding_context, prompt_accessor)

    @property
    def category(self) -> str:
        return 'news'

    @property
    def thinking_budget(self) -> int:
        """DEEP thinking for reduce phase ranking."""
        return ThinkingLevel.DEEP

    def _get_batch_analysis_prompt(
        self,
        items_context: str,
        batch_index: int,
        total_batches: int
    ) -> str:
        """Get the batch analysis prompt for map phase."""
        if self.prompt_accessor:
            return self.prompt_accessor.get_analyzer_prompt(
                self.category, 'batch_analysis',
                {'batch_index': batch_index + 1, 'total_batches': total_batches, 'items_context': items_context}
            )
        # Fallback to class constant for backwards compatibility
        return self.BATCH_ANALYSIS_PROMPT.format(
            batch_index=batch_index + 1,
            total_batches=total_batches,
            items_context=items_context
        )

    def _get_ranking_prompt(self, ranking_context: str) -> str:
        """Get the ranking prompt for reduce phase."""
        if self.prompt_accessor:
            return self.prompt_accessor.get_analyzer_prompt(
                self.category, 'ranking',
                {'analysis_summary': ranking_context}
            )
        # Fallback to class constant for backwards compatibility
        return self.RANKING_PROMPT.format(analysis_summary=ranking_context)

    def _has_ai_keywords(self, item: CollectedItem) -> bool:
        """Quick keyword check to identify likely AI articles."""
        text = f"{item.title} {item.content}".lower()
        return any(kw in text for kw in self.AI_KEYWORDS)

    def _truncate_id(self, full_id: str) -> str:
        """Truncate ID to first 16 chars for display."""
        return full_id[:16]

    async def _filter_with_llm(self, items: List[CollectedItem]) -> List[CollectedItem]:
        """Use LLM to filter items for frontier AI relevance."""
        if not items:
            return items

        # Build context with truncated IDs
        context_parts = []
        id_map = {}  # truncated -> full ID
        for item in items:
            truncated_id = self._truncate_id(item.id)
            id_map[truncated_id] = item.id
            context_parts.append(f"""
ID: {truncated_id}
Title: {item.title}
Source: {item.source}
Snippet: {item.content[:300]}...
""")

        items_context = '\n---'.join(context_parts)
        example_id = self._truncate_id(items[0].id)
        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_analyzer_prompt(
                self.category, 'filter',
                {'items_context': items_context, 'example_id': example_id}
            )
        else:
            # Fallback to class constant for backwards compatibility
            prompt = self.FILTER_PROMPT.format(
                items_context=items_context,
                example_id=example_id
            )

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                budget_tokens=ThinkingLevel.QUICK,  # Fast filter
                caller="news_analyzer.filter"
            )

            result = self._parse_json_response(response.content)
            ai_ids = set(result.get('ai_article_ids', []))

            logger.info(f"LLM filter thinking: {response.thinking[:500]}..." if response.thinking else "No thinking")
            logger.info(f"LLM filter returned {len(ai_ids)} AI article IDs")

            # Match truncated IDs back to full IDs
            full_ai_ids: Set[str] = set()
            for aid in ai_ids:
                # Try exact match first
                if aid in id_map:
                    full_ai_ids.add(id_map[aid])
                else:
                    # Try prefix match (in case LLM truncated differently)
                    for truncated, full in id_map.items():
                        if truncated.startswith(aid[:8]) or aid.startswith(truncated[:8]):
                            full_ai_ids.add(full)
                            break

            filtered = [item for item in items if item.id in full_ai_ids]
            logger.info(f"LLM filter: {len(items)} -> {len(filtered)} frontier AI articles")

            # Log which articles were filtered out
            if len(filtered) < len(items):
                removed = [item for item in items if item.id not in full_ai_ids]
                for item in removed[:5]:  # Log first 5 removed
                    logger.debug(f"  Filtered out: {item.title}")

            return filtered

        except Exception as e:
            logger.error(f"LLM filter failed: {e}")
            # Fall back to returning all items
            return items

    async def _analyze_small_batch(self, items: List[CollectedItem]) -> CategoryReport:
        """
        Combined analysis + ranking for small batches (< BATCH_SIZE items).

        Saves one LLM call by doing per-item analysis and ranking in a single prompt.
        """
        logger.info(f"Small batch analysis: {len(items)} items (combined analysis+ranking)")

        # Build items context
        items_context = self._build_items_context(items, max_items=len(items))

        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_analyzer_prompt(
                self.category, 'combined_analysis',
                {'count': len(items), 'items_context': items_context}
            )
        else:
            # Fallback to class constant for backwards compatibility
            prompt = self.COMBINED_ANALYSIS_PROMPT.format(
                count=len(items),
                items_context=items_context
            )

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                budget_tokens=ThinkingLevel.DEEP,  # Higher budget for combined task
                caller="news_analyzer.small_batch"
            )

            result = self._parse_json_response(response.content)
            thinking = response.thinking

            logger.info(f"Small batch thinking: {thinking[:500]}..." if thinking else "No thinking")

        except Exception as e:
            logger.error(f"Small batch analysis failed: {e}")
            return self._empty_report()

        # Build item lookup for efficient access
        item_by_id = {item.id: item for item in items}

        # Build AnalyzedItem list from response
        analyzed_items: List[AnalyzedItem] = []
        for item_result in result.get('items', []):
            item_id = item_result.get('id', '')
            if item_id not in item_by_id:
                logger.warning(f"Unknown item ID in response: {item_id}")
                continue

            analyzed_items.append(AnalyzedItem(
                item=item_by_id[item_id],
                summary=item_result.get('summary', ''),
                importance_score=float(item_result.get('importance_score', 50)),
                reasoning=item_result.get('reasoning', ''),
                themes=item_result.get('themes', []),
                thinking=thinking
            ))

        # Sort by importance score (descending)
        analyzed_items.sort(key=lambda x: x.importance_score, reverse=True)

        # Build themes from response
        themes: List[CategoryTheme] = []
        for theme_data in result.get('themes', []):
            themes.append(CategoryTheme(
                name=theme_data.get('name', ''),
                description=theme_data.get('description', ''),
                item_count=int(theme_data.get('item_count', 0)),
                example_items=[],  # Not tracked in combined prompt
                importance=float(theme_data.get('importance', 50))
            ))

        # Get top 10 items by ranking
        top_ids = result.get('top_10', [])[:10]
        top_items: List[AnalyzedItem] = []
        for item_id in top_ids:
            for item in analyzed_items:
                if item.item.id == item_id:
                    top_items.append(item)
                    break

        # Fill to 10 if needed
        if len(top_items) < 10:
            remaining = [i for i in analyzed_items if i not in top_items]
            top_items.extend(remaining[:10 - len(top_items)])

        # Log stats
        logger.info(f"═══ NEWS SMALL BATCH STATS ═══")
        logger.info(f"  Total items analyzed: {len(analyzed_items)}")
        logger.info(f"  Themes detected: {len(themes)}")
        if top_items:
            scores = [item.importance_score for item in top_items]
            logger.info(f"  Top 10 score range: {min(scores):.0f}-{max(scores):.0f}")
        logger.info(f"═══════════════════════════════")

        return CategoryReport(
            category=self.category,
            top_items=top_items,
            all_items=analyzed_items,
            category_summary=result.get('category_summary', ''),
            themes=themes[:10],
            cross_signals=[],
            total_collected=len(analyzed_items),
            thinking=thinking or ""
        )

    async def analyze(self, items: List[CollectedItem]) -> CategoryReport:
        """
        Analyze news articles using map-reduce batching.

        Keeps pre-filter phases (keyword + LLM) then applies map-reduce to filtered items.
        """
        if not items:
            return self._empty_report()

        logger.info(f"Analyzing {len(items)} news articles with map-reduce")

        # Phase 0a: Fast keyword pre-filter
        keyword_filtered = [item for item in items if self._has_ai_keywords(item)]
        logger.info(f"Keyword filter: {len(items)} -> {len(keyword_filtered)} AI articles")

        if not keyword_filtered:
            logger.warning("No AI-relevant articles found after keyword filter")
            return self._empty_report()

        # Phase 0b: LLM filter for frontier AI relevance
        filtered_items = await self._filter_with_llm(keyword_filtered)

        if not filtered_items:
            logger.warning("No frontier AI articles found after LLM filter")
            return self._empty_report()

        # Use combined analysis+ranking for small batches (saves one LLM call)
        if len(filtered_items) < self.BATCH_SIZE:
            return await self._analyze_small_batch(filtered_items)

        # MAP phase: Parallel batch analysis of filtered items (for large batches)
        batch_results, filtered_items = await self._map_phase(filtered_items)

        # Merge batch results
        analyzed_items, themes, cross_signals = self._merge_batch_results(batch_results, filtered_items)

        # Collect thinking from batches for logging
        batch_thinking = "\n---\n".join(
            f"Batch {r.batch_index}: {r.thinking[:500] if r.thinking else 'N/A'}..."
            for r in batch_results
        )

        # REDUCE phase: Final ranking
        return await self._reduce_phase(analyzed_items, themes, cross_signals, batch_thinking)

    def _build_items_context(self, items: List[CollectedItem], max_items: int = 50) -> str:
        """Format items for LLM analysis with full IDs."""
        context_parts = []
        for item in items[:max_items]:
            context_parts.append(f"""
---
ID: {item.id}
Title: {item.title}
Source: {item.source}
URL: {item.url}
Content: {item.content[:800]}...
Tags: {', '.join(item.tags)}
""")
        return '\n'.join(context_parts)

    # Note: _build_analyzed_items, _build_themes, and _empty_report
    # are now provided by BaseAnalyzer via map-reduce methods
