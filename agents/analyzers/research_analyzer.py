"""
Research Analyzer - Analyzes research papers and blog posts.

Focuses on:
- Research significance and novelty
- Technical breakthroughs
- Practical implications
- Influential authors and institutions
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


class ResearchAnalyzer(BaseAnalyzer):
    """Analyzes research content with extended thinking and map-reduce batching."""

    # Batch analysis prompt for map phase (simpler, per-batch)
    BATCH_ANALYSIS_PROMPT = """You are an AI research analyst. Analyze these research papers and blog posts (batch {batch_index} of {total_batches}).

For each item, provide:
1. A plain-language summary (2-3 sentences) explaining what it covers and why it matters
2. An importance score (0-100) based on research novelty, potential impact, methodology quality, and author credibility
3. Brief reasoning for the score
4. Research themes (e.g., "Language Models", "Reinforcement Learning", "AI Safety", "Alignment")

Items:
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

Focus on: papers from major labs (Google, OpenAI, Anthropic, Meta, DeepMind), novel architectures, SOTA results, safety/alignment research, efficiency improvements. For research blog posts, prioritize substantive technical content and original research over commentary."""

    # Legacy prompt kept for reference
    ANALYSIS_PROMPT = """You are an AI research analyst. Analyze the following research papers and blog posts.

For each item, provide:
1. A plain-language summary (2-3 sentences) explaining what it covers and why it matters
2. An importance score (0-100) based on:
   - Research novelty and significance
   - Potential impact on the field
   - Quality of methodology (based on abstract/content)
   - Author/institution credibility
3. Brief reasoning for the score
4. Research themes (e.g., "Language Models", "Reinforcement Learning", "AI Safety")

Items to analyze:
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

Focus on:
- Papers from major labs (Google, OpenAI, Anthropic, Meta, DeepMind)
- Novel architectures or training methods
- State-of-the-art results on important benchmarks
- Papers addressing safety, alignment, or interpretability
- Significant efficiency improvements
- Substantive research blog posts with original technical content"""

    RANKING_PROMPT = """Rank the top 10 most significant research items (papers and blog posts).

Analysis results:
{analysis_summary}

Prioritize:
1. Groundbreaking research with potential industry impact
2. Papers from top researchers/institutions
3. Novel approaches that could influence future work
4. Practical improvements with real-world applications
5. Safety and alignment research
6. Substantive blog posts with original technical insights

Return your ranking as JSON:
```json
{{
  "top_10": ["id1", "id2", ...],
  "category_summary": "Structured summary using markdown formatting (see rules below)"
}}
```

CATEGORY SUMMARY FORMATTING RULES:
- Use **bold** for model names, architecture names, benchmark names, and key metrics
- Use bullet points (- ) for lists of related papers or findings
- Group similar items together by theme (e.g., efficiency, safety, multimodal)
- Keep sentences concise (under 30 words each)
- Maximum 2-3 short paragraphs OR equivalent bullet content
- Write in factual, technical tone

Example format:
"Today's research highlights advances in multimodal architectures and reasoning efficiency. **NextFlow** achieves orders-of-magnitude faster image generation through a novel hybrid prediction strategy.

- **Falcon-H1R** demonstrates that **7B-parameter** models can match **2-7x larger** models through careful data curation
- Streaming hallucination detection introduces treating hallucinations as evolving latent states
- **DatBench** exposes fundamental flaws in current VLM benchmarks"

The summary should read like a technical briefing for researchers and practitioners."""

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
        return 'research'

    @property
    def thinking_budget(self) -> int:
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
        """Analyze research items using map-reduce batching."""
        if not items:
            return self._empty_report()

        logger.info(f"Analyzing {len(items)} research items with map-reduce")

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
        """Build context string optimized for research items."""
        context_parts = []
        for i, item in enumerate(items[:max_items], 1):
            parts = [f"--- Item {i} (ID: {item.id}) ---"]
            parts.append(f"Title: {item.title}")
            parts.append(f"Authors: {item.author}")
            parts.append(f"Source: {item.source} ({item.source_type})")
            if item.metadata.get('category_name'):
                parts.append(f"Category: {item.metadata['category_name']}")
            # Truncate content for research blog posts which can be very long
            content_preview = item.content[:1200] if item.source_type == 'research_blog' else item.content[:800]
            parts.append(f"Content: {content_preview}...")
            parts.append(f"URL: {item.url}")
            parts.append("")
            context_parts.append("\n".join(parts))
        return "\n".join(context_parts)

    # Note: _build_analyzed_items, _build_themes, and _empty_report
    # are now provided by BaseAnalyzer via map-reduce methods
