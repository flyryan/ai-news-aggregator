"""
Editorial Curator (Stage B)

Makes editorial decisions about story continuations.
Uses DEEP thinking for nuanced judgment calls.
"""

import json
import logging
import re
from typing import List, Dict, Any

from ..llm_client import AsyncAnthropicClient, ThinkingLevel
from ..base import AnalyzedItem, StoryMatch, ContinuationInfo

logger = logging.getLogger(__name__)


class EditorialCurator:
    """
    Makes editorial decisions about story continuations.

    Part of Stage B in the two-stage continuity detection architecture.
    Receives matched pairs from Stage A matchers and makes nuanced decisions.
    """

    def __init__(self, async_client: AsyncAnthropicClient):
        """
        Initialize editorial curator.

        Args:
            async_client: Async Anthropic client for LLM calls.
        """
        self.async_client = async_client

    async def curate(
        self,
        matches: List[StoryMatch],
        today_items: Dict[str, AnalyzedItem],
        historical_items: Dict[str, Dict[str, Any]]
    ) -> List[ContinuationInfo]:
        """
        Classify matches and generate reference text.

        Args:
            matches: List of StoryMatch objects from Stage A.
            today_items: Lookup of today's items by ID.
            historical_items: Lookup of historical items by ID.

        Returns:
            List of ContinuationInfo objects with editorial decisions.
        """
        if not matches:
            logger.info("Editorial curator: no matches to curate")
            return []

        # Build context for matched pairs
        pairs_context = self._build_pairs_context(matches, today_items, historical_items)

        prompt = self._build_prompt(pairs_context)

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": prompt}],
                budget_tokens=ThinkingLevel.DEEP,  # Nuanced judgment
                caller="continuity.curator"
            )

            continuations = self._parse_response(response.content, historical_items)
            logger.info(f"Editorial curator: made {len(continuations)} decisions")

            # Log demotions
            demoted = [c for c in continuations if c.should_demote]
            if demoted:
                logger.info(f"  - Demoting {len(demoted)} items from homepage")

            return continuations

        except Exception as e:
            logger.error(f"Editorial curator failed: {e}")
            return []

    def _build_pairs_context(
        self,
        matches: List[StoryMatch],
        today_items: Dict[str, AnalyzedItem],
        historical_items: Dict[str, Dict[str, Any]]
    ) -> str:
        """Build context string for matched pairs."""
        parts = []

        for i, match in enumerate(matches, 1):
            today_item = today_items.get(match.today_item_id)
            hist_item = historical_items.get(match.historical_item_id, {})

            if not today_item:
                continue

            parts.append(f"=== MATCH {i} (confidence: {match.confidence:.2f}) ===")
            parts.append("")

            parts.append("TODAY'S ITEM:")
            parts.append(f"  ID: {match.today_item_id}")
            parts.append(f"  Category: {match.today_category}")
            parts.append(f"  Title: {today_item.item.title}")
            parts.append(f"  Source: {today_item.item.source}")
            parts.append(f"  Summary: {today_item.summary}")
            parts.append("")

            parts.append("HISTORICAL ITEM:")
            parts.append(f"  ID: {match.historical_item_id}")
            parts.append(f"  Date: {hist_item.get('date', 'unknown')}")
            parts.append(f"  Category: {hist_item.get('category', 'unknown')}")
            parts.append(f"  Title: {hist_item.get('title', 'unknown')}")
            parts.append(f"  Source: {hist_item.get('source', 'unknown')}")
            parts.append(f"  Summary: {hist_item.get('summary', 'N/A')}")
            parts.append("")
            parts.append("")

        return "\n".join(parts)

    def _build_prompt(self, pairs_context: str) -> str:
        """Build the curation prompt."""
        return f"""You are the editorial curator for an AI news publication.

TASK: For each matched story pair, determine the relationship and
whether to demote from homepage.

MATCHED PAIRS:
{pairs_context}

RELATIONSHIP TYPES AND RULES:

A) NEWS/RESEARCH FOLLOWING SOCIAL/REDDIT:
   - "rehash": Today's mainstream coverage adds NO new information beyond what was
     already reported in yesterday's social/reddit post → DEMOTE from homepage
     Reference: "As first reported in **Social** yesterday"

   - "mainstream_pickup": The FACT that mainstream media picked up a story is itself
     newsworthy (e.g., small project goes viral, major outlet validates a rumor)
     → KEEP on homepage
     Reference: "First spotted on **Reddit**, now making mainstream headlines"

   - "new_development": Today's coverage contains SUBSTANTIAL new information,
     official announcements, or developments beyond the original
     → KEEP on homepage
     Reference: "Building on yesterday's **Social** buzz"

B) SOCIAL/REDDIT FOLLOWING NEWS:
   - "community_reaction": Discussion or analysis that adds value to yesterday's
     news coverage → KEEP on homepage
     Reference: "Following yesterday's **News** coverage"

C) SAME CATEGORY:
   - "follow_up": Ongoing story with new developments → KEEP on homepage
     Reference: "Continuing our coverage from yesterday"

DECISION GUIDELINES:
- Be thoughtful about demotions. Only demote true "rehashes" with no new value.
- Consider: Would a reader who saw yesterday's coverage learn anything new?
- If in doubt, KEEP (should_demote: false)
- Reference text should be natural and conversational

Return JSON:
```json
{{
  "decisions": [
    {{
      "today_item_id": "abc123",
      "original_item_id": "xyz789",
      "original_date": "2026-01-08",
      "original_category": "social",
      "original_title": "Title of the historical item",
      "continuation_type": "rehash",
      "should_demote": true,
      "reference_text": "As first reported in **Social** yesterday",
      "reasoning": "TechCrunch article has no new info beyond @karpathy's tweet"
    }}
  ]
}}
```

Make a decision for EACH matched pair."""

    def _parse_response(
        self,
        content: str,
        historical_items: Dict[str, Dict[str, Any]]
    ) -> List[ContinuationInfo]:
        """Parse LLM response into ContinuationInfo objects."""
        try:
            # Extract JSON from response
            content = content.strip()

            # Try to find JSON block
            json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
            if json_match:
                content = json_match.group(1).strip()

            # Find JSON object
            if not content.startswith('{'):
                start = content.find('{')
                if start == -1:
                    return []
                content = content[start:]

            # Parse JSON
            result = json.loads(content)

            continuations = []
            for decision in result.get('decisions', []):
                today_id = decision.get('today_item_id', '')
                original_id = decision.get('original_item_id', '')

                # Get historical item info if available
                hist_item = historical_items.get(original_id, {})

                continuations.append(ContinuationInfo(
                    original_item_id=original_id,
                    original_date=decision.get('original_date', hist_item.get('date', '')),
                    original_category=decision.get('original_category', hist_item.get('category', '')),
                    original_title=decision.get('original_title', hist_item.get('title', '')),
                    continuation_type=decision.get('continuation_type', 'follow_up'),
                    should_demote=decision.get('should_demote', False),
                    reference_text=decision.get('reference_text', '')
                ))

                # Store the today_item_id for the coordinator to use
                continuations[-1]._today_item_id = today_id  # type: ignore

            return continuations

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse curator response: {e}")
            logger.error(f"Content was: {content[:500]}...")
            return []
        except Exception as e:
            logger.error(f"Error parsing curator response: {e}")
            return []
