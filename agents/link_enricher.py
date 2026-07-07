"""
Link Enricher

Enriches summary text with internal links to collected items.
This module adds a post-processing step that uses LLM to identify
references in summary text and inject markdown links pointing to
the corresponding items on the site.
"""

import asyncio
import json
import logging
import re
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass

from .llm_client import AsyncAnthropicClient, ThinkingLevel
from .prompt_security import (
    build_fenced_user_message,
    build_hardened_system,
    new_fence_nonce,
    normalize_untrusted_text,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config.prompts import PromptAccessor

logger = logging.getLogger(__name__)


@dataclass
class LinkResult:
    """Result of link enrichment for a single text."""
    enriched_text: str
    links_added: List[Dict[str, str]]  # [{phrase, item_id, category}]
    original_text: str


class LinkEnricher:
    """
    Enriches summary text with internal links to items.

    Uses LLM to identify phrases in summary text that reference
    specific collected items, and injects markdown links to those items.
    """

    def __init__(
        self,
        async_client: AsyncAnthropicClient,
        date: str,
        prompt_accessor: Optional['PromptAccessor'] = None
    ):
        """
        Initialize link enricher.

        Args:
            async_client: Async Anthropic client for LLM calls.
            date: Target date (YYYY-MM-DD) for link URLs.
            prompt_accessor: Optional PromptAccessor for config-based prompts.
        """
        self.async_client = async_client
        self.date = date
        self.prompt_accessor = prompt_accessor

    async def enrich_all(
        self,
        executive_summary: str,
        category_reports: Dict[str, Any],
        top_topics: List[Any]
    ) -> Tuple[str, Dict[str, str], List[Any]]:
        """
        Enrich all summary text with internal links.

        Runs all enrichment tasks in parallel for efficiency.
        - Executive summary: can link to items from ANY category
        - Category summaries: can ONLY link to items from that category
        - Topic descriptions: can link to items from ANY category

        Args:
            executive_summary: The executive summary text.
            category_reports: Dict of category -> CategoryReport.
            top_topics: List of TopTopic objects.

        Returns:
            Tuple of (enriched_exec_summary, enriched_category_summaries, enriched_topics)
        """
        # Build complete item list from all categories
        all_items = self._build_item_list(category_reports)

        if not all_items:
            logger.warning("No items available for link enrichment")
            return executive_summary, {}, top_topics

        logger.info(f"Link enrichment: {len(all_items)} items available for linking")

        # Build category-specific item lists for category summaries
        items_by_category: Dict[str, List[Dict[str, Any]]] = {}
        for item in all_items:
            cat = item['category']
            if cat not in items_by_category:
                items_by_category[cat] = []
            items_by_category[cat].append(item)

        # Prepare all enrichment tasks for parallel execution
        tasks = []
        task_keys: List[Tuple[str, Any]] = []

        # Executive summary task (all items available)
        tasks.append(self._enrich_text(executive_summary, all_items, "executive summary"))
        task_keys.append(('exec', None))

        # Category summary tasks (ONLY items from that category)
        for category, report in category_reports.items():
            summary = report.category_summary if hasattr(report, 'category_summary') else report.get('category_summary', '')
            if summary:
                category_items = items_by_category.get(category, [])
                if category_items:
                    tasks.append(self._enrich_text(summary, category_items, f"{category} summary"))
                    task_keys.append(('category', category))
                else:
                    # No items for this category, skip enrichment
                    logger.debug(f"  {category} summary: no items available, skipping")

        # Topic description tasks (all items available)
        for i, topic in enumerate(top_topics):
            description = topic.description if hasattr(topic, 'description') else topic.get('description', '')
            if description:
                topic_name = topic.name if hasattr(topic, 'name') else topic.get('name', 'unknown')
                tasks.append(self._enrich_text(description, all_items, f"topic: {topic_name}"))
                task_keys.append(('topic', i))

        logger.info(f"  Running {len(tasks)} enrichment tasks in parallel...")

        # Run all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        enriched_exec = executive_summary
        enriched_categories: Dict[str, str] = {}
        enriched_topics = list(top_topics)  # Make a copy to modify

        for (key_type, key_value), result in zip(task_keys, results):
            if isinstance(result, Exception):
                logger.error(f"Link enrichment failed for {key_type}/{key_value}: {result}")
                continue

            if key_type == 'exec':
                enriched_exec = result
            elif key_type == 'category':
                enriched_categories[key_value] = result
            elif key_type == 'topic':
                topic = enriched_topics[key_value]
                if hasattr(topic, 'description'):
                    topic.description = result
                    topic.description_html = self._markdown_links_to_html(result)
                else:
                    topic['description'] = result
                    topic['description_html'] = self._markdown_links_to_html(result)

        return enriched_exec, enriched_categories, enriched_topics

    # How many items per category to expose to the link-enrichment LLM.
    # The executive summary is generated with visibility into category summaries
    # and cross-category topics, so it often mentions stories beyond each
    # category's top 10. Passing a wider slice (ranked by importance_score)
    # gives the enricher a realistic chance of finding matches.
    ITEMS_PER_CATEGORY = 30

    def _exclude_from_summaries(self, analyzed_item: Any) -> bool:
        """Return True if freshness metadata says the item must not shape summaries."""
        metadata = {}
        if hasattr(analyzed_item, 'item'):
            item = analyzed_item.item
            metadata = item.metadata if hasattr(item, 'metadata') else {}
        elif isinstance(analyzed_item, dict):
            item = analyzed_item.get('item', analyzed_item)
            metadata = item.get('metadata', {}) if isinstance(item, dict) else {}
            if not metadata and isinstance(analyzed_item.get('freshness'), dict):
                metadata = {'freshness': analyzed_item.get('freshness')}

        freshness = metadata.get('freshness') if isinstance(metadata, dict) else {}
        return bool(isinstance(freshness, dict) and freshness.get('exclude_from_summaries'))

    def _build_item_list(self, category_reports: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build a simplified list of items for LLM context.

        Prefer ``all_items`` (sorted by importance_score descending) so the
        pool isn't capped at the per-category top-10 ranked list. This lets
        the enricher match stories the executive summary pulled in from
        cross-category context. Fall back to ``top_items`` if ``all_items``
        isn't populated on the report.
        """
        items = []

        for category, report in category_reports.items():
            # Prefer all_items (already sorted by importance_score desc in the
            # reduce phase) so we can take a wider slice. Fall back to
            # top_items for backward compatibility with older checkpoints.
            source_items = None
            if hasattr(report, 'all_items'):
                source_items = report.all_items or report.top_items
            elif isinstance(report, dict):
                source_items = report.get('all_items') or report.get('top_items', [])
            source_items = source_items or []

            added_for_category = 0
            for analyzed_item in source_items:
                if self._exclude_from_summaries(analyzed_item):
                    continue
                if added_for_category >= self.ITEMS_PER_CATEGORY:
                    break
                # Handle both object and dict formats
                if hasattr(analyzed_item, 'item'):
                    item = analyzed_item.item
                    item_id = item.id if hasattr(item, 'id') else item.get('id', '')
                    title = item.title if hasattr(item, 'title') else item.get('title', '')
                    summary = analyzed_item.summary if hasattr(analyzed_item, 'summary') else ''
                elif isinstance(analyzed_item, dict):
                    item = analyzed_item.get('item', analyzed_item)
                    item_id = item.get('id', analyzed_item.get('id', ''))
                    title = item.get('title', analyzed_item.get('title', ''))
                    summary = analyzed_item.get('summary', '')
                else:
                    continue

                if item_id and title:
                    items.append({
                        'id': item_id,
                        'title': normalize_untrusted_text(title)[:300],
                        'category': category,
                        'summary': summary[:200] if summary else ''
                    })
                    added_for_category += 1

        return items

    async def _enrich_text(
        self,
        text: str,
        items: List[Dict[str, Any]],
        context_name: str
    ) -> str:
        """
        Enrich a single text with internal links.

        Args:
            text: The text to enrich.
            items: List of items available for linking.
            context_name: Name for logging purposes.

        Returns:
            Enriched text with markdown links.
        """
        if not text or not items:
            return text

        # Build items context. Cap is 4 categories * ITEMS_PER_CATEGORY plus
        # headroom; kept generous so the LLM sees enough candidates to link
        # every story mentioned by the executive summary.
        items_json = json.dumps(items[:140], indent=2, ensure_ascii=False)

        # CWE-1427: enrichment instructions travel in the system prompt; the
        # item list and text to enrich travel in the user message inside a
        # nonce fence, as labeled sections the instruction pointers name.
        nonce = new_fence_nonce()
        items_pointer = "[Provided in the user message inside the <source_data> fence, under AVAILABLE ITEMS.]"
        text_pointer = "[Provided in the user message inside the <source_data> fence, under TEXT TO ENRICH.]"
        if self.prompt_accessor:
            instructions = self.prompt_accessor.get_post_processing_prompt(
                'link_enrichment',
                {'date': self.date, 'items_json': items_pointer, 'text': text_pointer}
            )
        else:
            # Fallback to inline prompt for backwards compatibility
            instructions = f"""You are a link enrichment agent. Add contextual "read more" links to summary text so readers can dive deeper into stories.

LINKING STRATEGY (CRITICAL):
1. Keep links SHORT (3-7 words max) - just the key action phrase
   - BAD (too long): "Google [published verification that GPT-5.2 solved an unsolved problem](/...)"
   - BAD (too long): "[announced Vera Rubin chips are in full production](/...)"
   - GOOD: "Google [published verification](/...) that GPT-5.2 solved a problem"
   - GOOD: "Nvidia [announced Vera Rubin chips](/...) are in full production"
2. Link the ACTION/EVENT phrase, NOT the leading company/entity name
   - BAD: "[Google DeepMind](/...) announced robots"
   - GOOD: "Google DeepMind [announced Atlas robots](/...)"
3. ONE link per distinct story/development in the text
4. Link to the HIGHEST-RANKED item that covers that story (items are ordered by importance)
5. Do NOT add new **bold** markers inside link labels. Preserve existing bold markers outside links.
6. Preserve ALL original formatting exactly unless a link would require moving existing bold markers outside the link.
7. For bullet points, link the key action/event after the entity prefix

LINK FORMAT (exact format required):
[descriptive phrase](/?date={self.date}&category=CATEGORY#item-ITEMID)

CRITICAL: The hash MUST start with "item-" followed by the item's id. Example:
  - Item with id "abc123def456" and category "news" becomes: /?date={self.date}&category=news#item-abc123def456

DATE: {self.date}

AVAILABLE ITEMS (ordered by importance - use id and category exactly as shown):
{items_pointer}

TEXT TO ENRICH:
{text_pointer}

OUTPUT (JSON only, no markdown code blocks):
{{
  "enriched_text": "Full text with links using format /?date={self.date}&category=CATEGORY#item-actualItemId",
  "links": [{{"phrase": "the linked phrase", "item_id": "actualItemId", "category": "news"}}]
}}

CRITICAL JSON FORMATTING:
- Double quotes inside the text MUST be escaped as \\"
- Example: "the \\"grief cycle\\" concept" NOT "the "grief cycle" concept"
- Newlines in the text must be escaped as \\n
- Use single quotes for emphasis when possible to avoid escaping issues

Remember: The anchor MUST be #item-ID (with item- prefix). Link actions, not entities. Avoid bold markers inside links."""

        system_prompt = build_hardened_system(instructions, nonce)
        fenced_payload = (
            f"AVAILABLE ITEMS (ordered by importance):\n{items_json}\n\n"
            f"TEXT TO ENRICH:\n{text}"
        )
        user_message = build_fenced_user_message(
            fenced_payload, nonce,
            task_line="Enrich the fenced text below according to your system instructions.",
        )

        try:
            response = await self.async_client.call_with_thinking(
                messages=[{"role": "user", "content": user_message}],
                system=system_prompt,
                profile=ThinkingLevel.STANDARD,
                caller=f"link_enricher.{context_name}"
            )

            # Parse JSON response
            content = response.content.strip()
            # Handle markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            # Try to extract JSON object if there's extra text
            if not content.startswith("{"):
                start = content.find("{")
                if start != -1:
                    content = content[start:]
            if not content.endswith("}"):
                end = content.rfind("}")
                if end != -1:
                    content = content[:end + 1]

            result = json.loads(content)

            enriched = result.get('enriched_text', text)
            links = result.get('links', [])

            if links:
                logger.info(f"  {context_name}: added {len(links)} links")
                for link in links:
                    logger.debug(f"    Linked '{link.get('phrase', '')}' -> {link.get('category', '')}/{link.get('item_id', '')[:8]}...")
            else:
                logger.info(f"  {context_name}: no links added")

            return enriched

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse link enrichment response for {context_name}: {e}")
            logger.debug(f"Response content: {content[:500] if content else 'None'}")

            # Try regex fallback to extract enriched_text, but validate before accepting
            match = re.search(r'"enriched_text"\s*:\s*"((?:[^"\\]|\\.)*)"', content, re.DOTALL)
            if match:
                enriched = match.group(1)
                # Unescape JSON string escapes
                enriched = enriched.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')

                # Validate: check for truncation (unbalanced brackets, incomplete links)
                open_brackets = enriched.count('[')
                close_brackets = enriched.count(']')
                has_incomplete_link = bool(re.search(r'\[[^\]]*$', enriched))
                is_too_short = len(enriched) < len(text) * 0.5

                if open_brackets == close_brackets and not has_incomplete_link and not is_too_short:
                    logger.info(f"  {context_name}: recovered enriched text via validated regex fallback")
                    return enriched
                else:
                    logger.warning(f"  {context_name}: regex extraction failed validation (brackets={open_brackets}/{close_brackets}, incomplete={has_incomplete_link}, short={is_too_short})")

            logger.warning(f"  {context_name}: JSON parse failed, using original unenriched text")
            return text
        except Exception as e:
            logger.error(f"Link enrichment failed for {context_name}: {e}")
            return text

    def _markdown_links_to_html(self, text: str) -> str:
        """Convert markdown links to HTML, differentiating internal vs external."""
        def link_replacer(match):
            link_text, url = match.groups()
            if url.startswith('/') or url.startswith('#'):
                # Internal link
                return f'<a href="{url}" class="internal-link">{link_text}</a>'
            else:
                # External link
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{link_text}</a>'

        return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, text)
