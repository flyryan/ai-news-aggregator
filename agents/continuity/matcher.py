"""
Story Matcher (Stage A)

Matches items from one category against all historical items across categories.
Uses QUICK thinking for fast, parallelizable matching.
"""

import json
import logging
import re
from typing import List, Dict, Any

from ..llm_client import AsyncAnthropicClient, ThinkingLevel
from ..base import AnalyzedItem, StoryMatch
from ..prompt_security import (
    build_fenced_user_message,
    build_hardened_system,
    new_fence_nonce,
    normalize_untrusted_text,
)

logger = logging.getLogger(__name__)


class StoryMatcher:
    """
    Matches items from one category against all historical items.

    Part of Stage A in the two-stage continuity detection architecture.
    Runs in parallel with other category matchers.
    """

    def __init__(self, async_client: AsyncAnthropicClient, category: str):
        """
        Initialize story matcher.

        Args:
            async_client: Async Anthropic client for LLM calls.
            category: The category this matcher handles (news, research, social, reddit).
        """
        self.async_client = async_client
        self.category = category

    async def find_matches(
        self,
        today_items: List[AnalyzedItem],
        historical_items: List[Dict[str, Any]]
    ) -> List[StoryMatch]:
        """
        Find items that match historical stories (cross-category).

        Args:
            today_items: Today's analyzed items for this category.
            historical_items: Historical items from all categories (past 2 days).

        Returns:
            List of StoryMatch objects for matched pairs.
        """
        if not today_items or not historical_items:
            logger.info(f"{self.category} matcher: no items to match")
            return []

        # Build context for today's items
        today_context = self._build_today_context(today_items)

        # Build context for historical items
        historical_context = self._build_historical_context(historical_items)

        # CWE-1427: matching instructions travel in the system prompt; the
        # item lists (untrusted titles/summaries) travel in the user message
        # inside a nonce fence.
        nonce = new_fence_nonce()
        system_prompt = build_hardened_system(self._build_instructions(), nonce)
        user_message = build_fenced_user_message(
            f"{today_context}\n\n{historical_context}", nonce,
            task_line="Match the fenced items below according to your system instructions.",
        )

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": user_message}],
                system=system_prompt,
                profile=ThinkingLevel.QUICK,  # Fast matching
                caller=f"continuity.matcher.{self.category}"
            )

            matches = self._parse_response(response.content, today_items)
            logger.info(f"{self.category} matcher: found {len(matches)} matches")
            return matches

        except Exception as e:
            logger.error(f"{self.category} matcher failed: {e}")
            return []

    def _build_today_context(self, items: List[AnalyzedItem]) -> str:
        """Build context string for today's items."""
        parts = [f"=== TODAY'S {self.category.upper()} ITEMS ===", ""]
        for i, item in enumerate(items, 1):
            parts.append(f"ITEM {i}:")
            parts.append(f"  ID: {item.item.id}")
            parts.append(f"  Title: {normalize_untrusted_text(item.item.title)[:300]}")
            summary = normalize_untrusted_text(item.summary)[:300] if item.summary else 'N/A'
            parts.append(f"  Summary: {summary}")
            parts.append(f"  Source: {normalize_untrusted_text(item.item.source)}")
            parts.append("")
        parts.append(f"=== END TODAY'S {self.category.upper()} ITEMS ===")
        return "\n".join(parts)

    def _build_historical_context(self, items: List[Dict[str, Any]]) -> str:
        """Build context string for historical items."""
        parts = ["=== HISTORICAL ITEMS (past 2 days, all categories) ===", ""]
        for i, item in enumerate(items, 1):
            parts.append(f"ITEM {i}:")
            parts.append(f"  ID: {item.get('id', 'unknown')}")
            parts.append(f"  Date: {item.get('date', 'unknown')}")
            parts.append(f"  Category: {item.get('category', 'unknown')}")
            parts.append(f"  Title: {normalize_untrusted_text(item.get('title', 'unknown'))[:300]}")
            summary = item.get('summary', '')
            parts.append(f"  Summary: {normalize_untrusted_text(summary)[:300] if summary else 'N/A'}")
            parts.append("")
        parts.append("=== END HISTORICAL ITEMS ===")
        return "\n".join(parts)

    def _build_instructions(self) -> str:
        """Build the matching instructions (system prompt)."""
        return f"""You are finding story matches for an AI news publication.

TASK: For each of today's {self.category.upper()} items, determine if it covers the
SAME underlying story as any historical item (from ANY category).

The items are provided in the user message inside the <source_data> fence, as
two sections: "=== TODAY'S {self.category.upper()} ITEMS ===" and
"=== HISTORICAL ITEMS (past 2 days, all categories) ===", each closed by a
matching "=== END ... ===" marker.

MATCHING CRITERIA:
- A match means the SAME underlying event/announcement/development
- Different coverage angles of the same story = MATCH
- Related but different stories = NOT a match
- Be conservative: only match if you're confident they're the same story

Return JSON:
```json
{{
  "matches": [
    {{
      "today_item_id": "abc123",
      "historical_item_id": "xyz789",
      "confidence": 0.9,
      "reasoning": "Both about OpenAI's GPT-5 announcement"
    }}
  ],
  "no_match": ["id1", "id2"]
}}
```

If no matches are found, return an empty matches array."""

    def _parse_response(
        self,
        content: str,
        today_items: List[AnalyzedItem]
    ) -> List[StoryMatch]:
        """Parse LLM response into StoryMatch objects."""
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

            # Build lookup for today's items
            today_lookup = {item.item.id: item for item in today_items}

            matches = []
            for match_data in result.get('matches', []):
                today_id = match_data.get('today_item_id', '')
                hist_id = match_data.get('historical_item_id', '')
                confidence = match_data.get('confidence', 0.5)

                # Skip if today's item not found
                if today_id not in today_lookup:
                    logger.warning(f"Match references unknown today item: {today_id}")
                    continue

                # Create StoryMatch (historical details will be filled by coordinator)
                matches.append(StoryMatch(
                    today_item_id=today_id,
                    today_category=self.category,
                    historical_item_id=hist_id,
                    historical_category='',  # Filled by coordinator
                    historical_date='',  # Filled by coordinator
                    historical_title='',  # Filled by coordinator
                    confidence=confidence
                ))

            return matches

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse matcher response: {e}")
            logger.error(f"Content was: {content[:500]}...")
            return []
        except Exception as e:
            logger.error(f"Error parsing matcher response: {e}")
            return []
