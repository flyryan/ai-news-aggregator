"""
Reddit Analyzer - Analyzes Reddit discussions.

Focuses on:
- Community discussions and debates
- Technical questions and answers
- Project showcases
- Industry sentiment
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


class RedditAnalyzer(BaseAnalyzer):
    """Analyzes Reddit discussions with extended thinking and map-reduce batching."""

    # Batch analysis prompt for map phase
    BATCH_ANALYSIS_PROMPT = """You are an AI community analyst. Analyze these Reddit discussions about AI/ML (batch {batch_index} of {total_batches}).

For each post, provide:
1. A brief summary of what the discussion is about
2. An importance score (0-100) based on discussion quality, engagement, relevance, and educational value
3. Brief reasoning for the score
4. Discussion themes

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

Prioritize: technical depth, project showcases, educational content, high-engagement quality discussions.
Deprioritize: simple questions, memes, repetitive beginner questions."""

    # Legacy prompt kept for reference
    ANALYSIS_PROMPT = """You are an AI community analyst. Analyze the following Reddit discussions about AI/ML.

For each post, provide:
1. A brief summary of what the discussion is about
2. An importance score (0-100) based on:
   - Quality of discussion and insights
   - Community engagement (score, comments)
   - Relevance to AI practitioners
   - Educational or informational value
3. Brief reasoning for the score
4. Discussion themes

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
- Technical discussions with depth
- Project showcases and demos
- Educational content and tutorials
- Industry news discussions
- High-engagement posts with quality comments

Deprioritize:
- Simple questions with easy answers
- Memes or low-effort content
- Repetitive beginner questions
- Off-topic or tangential discussions"""

    RANKING_PROMPT = """Rank the top 10 most valuable Reddit discussions.

Analysis results:
{analysis_summary}

Consider:
1. Quality and depth of discussion
2. Community engagement (score + comments)
3. Educational or practical value
4. Relevance to current AI developments
5. Uniqueness of the discussion

Return your ranking as JSON:
```json
{{
  "top_10": ["id1", "id2", ...],
  "category_summary": "Structured summary using markdown formatting (see rules below)"
}}
```

CATEGORY SUMMARY FORMATTING RULES:
- Use **bold** for subreddit names (r/MachineLearning), tools, and key topics being debated
- Use bullet points (- ) for distinct discussions or debates
- Group related threads by theme
- Keep sentences concise (under 30 words each)
- Maximum 2-3 short paragraphs OR equivalent bullet content
- Capture the community sentiment and contrasting viewpoints

Example format:
"**r/MachineLearning** and **r/LocalLLaMA** dominated today's discussions with debates about open-source model efficiency. The community showed skepticism about closed-model benchmark claims.

- Heated debate about **Nvidia's pricing strategy** and alternatives for hobbyists
- **llama.cpp** achieved **3-4x multi-GPU speedups**, drawing significant interest
- Discussion of **Intel's** local inference push as a privacy-focused alternative"

The summary should capture what the AI community on Reddit is debating and building."""

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
        return 'reddit'

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
        """Analyze Reddit discussions using map-reduce batching."""
        if not items:
            return self._empty_report()

        logger.info(f"Analyzing {len(items)} Reddit posts with map-reduce")

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
        """Build context string optimized for Reddit posts."""
        context_parts = []
        for i, item in enumerate(items[:max_items], 1):
            parts = [f"--- Post {i} (ID: {item.id}) ---"]
            parts.append(f"Subreddit: {item.source}")
            parts.append(f"Title: {item.title}")
            parts.append(f"Author: {item.author}")

            # Add content if it's a self post
            if item.content:
                content = item.content[:500] + '...' if len(item.content) > 500 else item.content
                parts.append(f"Content: {content}")

            # Add engagement metrics
            engagement = item.metadata.get('engagement', {})
            if engagement:
                parts.append(f"Score: {engagement.get('score', 0)}, Comments: {engagement.get('num_comments', 0)}")

            parts.append(f"URL: {item.url}")
            parts.append("")
            context_parts.append("\n".join(parts))
        return "\n".join(context_parts)

    # Note: _build_analyzed_items, _build_themes, and _empty_report
    # are now provided by BaseAnalyzer via map-reduce methods
