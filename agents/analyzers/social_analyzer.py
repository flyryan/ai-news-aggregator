"""
Social Analyzer - Analyzes social media posts (Twitter, Bluesky, Mastodon).

Focuses on:
- Industry discussions and reactions
- Expert opinions and insights
- Viral content and trends
- Community sentiment
"""

import json
import logging
from typing import List, Optional

from ..base import (
    BaseAnalyzer, CollectedItem, AnalyzedItem,
    CategoryReport, CategoryTheme
)
from ..llm_client import AnthropicClient, AsyncAnthropicClient, ThinkingLevel

logger = logging.getLogger(__name__)


class SocialAnalyzer(BaseAnalyzer):
    """Analyzes social media posts with extended thinking and map-reduce batching."""

    # Batch analysis prompt for map phase
    BATCH_ANALYSIS_PROMPT = """You are an AI social media analyst. Analyze these social media posts about AI/ML (batch {batch_index} of {total_batches}).

For each post, provide:
1. A brief summary of what the post is discussing
2. An importance score (0-100) based on author credibility, engagement, uniqueness, and relevance
3. Brief reasoning for the score
4. Themes discussed

Posts:
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

Prioritize: recognized AI researchers, original insights, breaking news, technical depth, high engagement.
Deprioritize: promotional content, retweets without commentary, off-topic tangents."""

    # Legacy prompt kept for reference
    ANALYSIS_PROMPT = """You are an AI social media analyst. Analyze the following social media posts about AI/ML.

For each post, provide:
1. A brief summary of what the post is discussing
2. An importance score (0-100) based on:
   - Author credibility and influence
   - Engagement metrics
   - Uniqueness of insight or information
   - Relevance to current AI developments
3. Brief reasoning for the score
4. Themes discussed

Posts to analyze:
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

Prioritize:
- Posts from recognized AI researchers and practitioners
- Original insights or analysis (not just sharing links)
- Breaking news or exclusive information
- Technical discussions with depth
- High engagement relative to account size

Deprioritize:
- Generic promotional content
- Retweets without commentary
- Off-topic tangents
- Inflammatory or purely opinion-based content"""

    RANKING_PROMPT = """Rank the top 10 most valuable social media posts.

Analysis results:
{analysis_summary}

Consider:
1. Quality of insight or information
2. Author expertise in AI/ML
3. Engagement and discussion generated
4. Timeliness and relevance
5. Uniqueness of perspective

Return your ranking as JSON:
```json
{{
  "top_10": ["id1", "id2", ...],
  "category_summary": "Structured summary using markdown formatting (see rules below)"
}}
```

CATEGORY SUMMARY FORMATTING RULES:
- Use **bold** for researcher names, company names, and key topics being discussed
- Use bullet points (- ) for distinct discussions or threads
- Group related conversations by theme
- Keep sentences concise (under 30 words each)
- Maximum 2-3 short paragraphs OR equivalent bullet content
- Capture the sentiment and perspectives in the community

Example format:
"AI safety concerns dominated social discussions today. **Sam Altman** signaled that AI memory will drive more impact than reasoning improvements, sparking debate about persistent AI systems.

- **Andrej Karpathy** shared technical insights on training efficiency for small models
- The robotics community reacted positively to the **Google-Boston Dynamics** collaboration
- Concerns about **xAI's Grok** safety issues drew widespread criticism"

The summary should capture the pulse of AI community discussions."""

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
        return 'social'

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

    async def analyze(self, items: List[CollectedItem]) -> CategoryReport:
        """Analyze social media posts using map-reduce batching."""
        if not items:
            return self._empty_report()

        logger.info(f"Analyzing {len(items)} social posts with map-reduce")

        # MAP phase: Parallel batch analysis
        batch_results, items = await self._map_phase(items)

        # Merge batch results
        analyzed_items, themes, cross_signals = self._merge_batch_results(batch_results, items)

        # Collect thinking from batches for logging
        batch_thinking = "\n---\n".join(
            f"Batch {r.batch_index}: {r.thinking[:500] if r.thinking else 'N/A'}..."
            for r in batch_results
        )

        # REDUCE phase: Final ranking
        return await self._reduce_phase(analyzed_items, themes, cross_signals, batch_thinking)

    def _build_items_context(self, items: List[CollectedItem], max_items: int = 50) -> str:
        """Build context string optimized for social posts."""
        context_parts = []
        for i, item in enumerate(items[:max_items], 1):
            parts = [f"--- Post {i} (ID: {item.id}) ---"]
            parts.append(f"Platform: {item.source_type}")
            parts.append(f"Author: {item.author}")
            parts.append(f"Content: {item.content}")

            # Add engagement metrics if available
            engagement = item.metadata.get('engagement', {})
            if engagement:
                eng_str = ', '.join(f"{k}: {v}" for k, v in engagement.items() if v)
                parts.append(f"Engagement: {eng_str}")

            parts.append(f"URL: {item.url}")
            parts.append("")
            context_parts.append("\n".join(parts))
        return "\n".join(context_parts)

    # Note: _build_analyzed_items, _build_themes, and _empty_report
    # are now provided by BaseAnalyzer via map-reduce methods
