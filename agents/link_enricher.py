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

# The signature of an internal link this module writes:
# [phrase](/?date=2026-09-04&category=news#item-abc123def456). Its presence in
# a summary or topic description is the only evidence a finished run leaves
# that the text was enriched, which is what the `--resume-from 4.5` repair
# mode keys on to decide what still needs asking for.
INTERNAL_LINK_MARKER = "](/?date="


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

    After `enrich()`, `degradations` lists the summaries and topics that fell
    back to unenriched text. Callers must read it: every failure path here
    returns readable prose, so a silent failure is indistinguishable from
    success by inspecting the return value alone.

    Each text gets at most `len(ENRICH_PROFILES)` LLM calls. A truncated reply
    escalates to the next profile. An unparseable one is first offered to the
    validated regex fallback, and only escalates if that declines it -- so a
    reply the fallback can rescue still costs exactly one call.
    """

    # Class-level default so a caller that inspects `degradations` before
    # `enrich()` runs sees an empty list rather than an AttributeError.
    degradations: List[str] = []

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
        top_topics: List[Any],
        only_unlinked: bool = False
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
            only_unlinked: Repair mode (`--resume-from 4.5`). Leave every text
                that already contains an internal link exactly as it was
                published and re-ask only for the ones that lost theirs. A
                skipped text is not a degradation -- it is already correct.

        Returns:
            Tuple of (enriched_exec_summary, enriched_category_summaries, enriched_topics)

            In repair mode the returned category dict holds only the summaries
            that were actually re-enriched, so a caller writing it back leaves
            the skipped ones untouched.
        """
        # Reset per run: `degradations` describes this enrichment pass only.
        self.degradations = []

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

        def already_linked(text: str, context_name: str) -> bool:
            """True when repair mode should leave this text exactly as it is."""
            if not only_unlinked or INTERNAL_LINK_MARKER not in (text or ''):
                return False
            logger.info(f"  {context_name}: already linked, skipping (repair mode)")
            return True

        # Executive summary task (all items available)
        if not already_linked(executive_summary, "executive summary"):
            tasks.append(self._enrich_text(executive_summary, all_items, "executive summary"))
            task_keys.append(('exec', None))

        # Category summary tasks (ONLY items from that category)
        for category, report in category_reports.items():
            summary = report.category_summary if hasattr(report, 'category_summary') else report.get('category_summary', '')
            if summary and not already_linked(summary, f"{category} summary"):
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
                if already_linked(description, f"topic: {topic_name}"):
                    continue
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
                self.degradations.append(f"{key_type}/{key_value}: {type(result).__name__}")
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

    # Bounded escalation for a single enrichment. 2026-09-04: on
    # google/gemini-3.8-flash the 65536-token completion cap is SHARED between
    # reasoning and visible output, so the reddit summary's enrichment reasoned
    # for ~80k chars and its JSON answer was cut off at max_tokens — which the
    # old code parsed as though the model had finished. Re-asking at a lower
    # profile leaves more of that shared budget for the answer, the same move
    # the analyzers make on a truncated batch (BaseAnalyzer._handle_truncated_batch).
    # Two profiles is the entire budget: this runs once per summary and topic
    # on the critical path, and the transport already owns transient retries.
    ENRICH_PROFILES = (ThinkingLevel.STANDARD, ThinkingLevel.QUICK)

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
        items_pointer = "[Provided in the user message inside the <source_data> fence, between the \"=== AVAILABLE ITEMS ===\" and \"=== END AVAILABLE ITEMS ===\" markers.]"
        text_pointer = "[Provided in the user message inside the <source_data> fence, between the \"=== TEXT TO ENRICH ===\" and \"=== END TEXT TO ENRICH ===\" markers.]"
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
        # Explicit END markers: the text block is arbitrary multi-paragraph
        # markdown, so its boundary must not depend on where the fence closes.
        fenced_payload = (
            f"=== AVAILABLE ITEMS (ordered by importance) ===\n{items_json}\n"
            f"=== END AVAILABLE ITEMS ===\n\n"
            f"=== TEXT TO ENRICH ===\n{text}\n"
            f"=== END TEXT TO ENRICH ==="
        )
        user_message = build_fenced_user_message(
            fenced_payload, nonce,
            task_line="Enrich the fenced text below according to your system instructions.",
        )

        # Shared extractor: trims fences/preamble and repairs the two
        # ox-alpha failure modes (raw control chars, unescaped inner
        # quotes) that used to dump every enrichment to regex fallback.
        from agents.base import extract_json_str

        # How the LAST attempt failed, which is the only thing the post-loop
        # degradation note has left to say. An unparseable attempt is resolved
        # in place (recovered or discarded) before the loop moves on, so this
        # flag is the whole state that has to survive an iteration.
        last_truncated = False

        for profile in self.ENRICH_PROFILES:
            try:
                response = await self.async_client.call_with_thinking(
                    messages=[{"role": "user", "content": user_message}],
                    system=system_prompt,
                    profile=profile,
                    # Identical on every attempt: the replay taxonomy keys on
                    # this tag, and each attempt is already an independent call
                    # there.
                    caller=f"link_enricher.{context_name}"
                )
            except Exception as e:
                # Returning the original text keeps the summary readable, but it is
                # NOT the enriched output the page promises. Before 2026-08-24 this
                # was invisible: a provider brownout stripped every internal link
                # from the report and the run still reported Phase 4.5 [ok].
                # No further profile: the transport layer already exhausted its
                # retry window (in-band OpenRouter stream errors included, as of
                # 2026-09-04), so re-asking here only re-asks a dead provider.
                logger.error(f"Link enrichment failed for {context_name}: {e}")
                self.degradations.append(f"{context_name}: {type(e).__name__}")
                return text

            content = (response.content or "").strip()

            if response.stop_reason == "max_tokens":
                # Deliberately NOT parsed. A clipped object can still be
                # well-formed JSON — on 2026-09-04 it was, and the half-written
                # value it carried was published as the reader's link text.
                last_truncated = True
                logger.warning(
                    f"  {context_name}: enrichment reply truncated at max_tokens "
                    f"(profile={profile.name}, output_chars={len(content)}); "
                    f"JSON is clipped, not parsing"
                )
                continue

            # Handle markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = extract_json_str(content.strip())

            try:
                result = json.loads(content)
                if not isinstance(result, dict):
                    # A bare array or scalar parses cleanly and then explodes on
                    # `.get`. That used to be swallowed by a blanket `except`;
                    # now it must escalate like any other malformed reply rather
                    # than escape this method, which is the only place that
                    # guarantees the caller gets readable prose back.
                    raise json.JSONDecodeError(
                        "enrichment payload is not a JSON object", content or "", 0
                    )
            except json.JSONDecodeError as e:
                last_truncated = False
                logger.error(
                    f"Failed to parse link enrichment response for {context_name} "
                    f"(profile={profile.name}): {e}"
                )
                logger.debug(f"Response content: {content[:500] if content else 'None'}")
                # Offer it to the validated regex fallback NOW, on the attempt
                # that produced it. Two reasons it cannot wait until the
                # profiles are exhausted: only the last attempt's content would
                # ever reach it, so a recoverable reply here would be thrown
                # away by a later truncated one (worse than the single-call
                # behaviour this escalation replaced); and a reply the fallback
                # can rescue does not need a second call at all, which is
                # exactly what that behaviour cost.
                recovered = self._recover_enriched_text(content, text, context_name)
                if recovered is not None:
                    return recovered
                continue

            enriched = result.get('enriched_text', text)
            links = result.get('links', [])

            if links:
                logger.info(f"  {context_name}: added {len(links)} links")
                for link in links:
                    logger.debug(f"    Linked '{link.get('phrase', '')}' -> {link.get('category', '')}/{link.get('item_id', '')[:8]}...")
            else:
                logger.info(f"  {context_name}: no links added")

            return enriched

        if last_truncated:
            logger.warning(
                f"  {context_name}: truncated at max_tokens on all "
                f"{len(self.ENRICH_PROFILES)} attempts, using original unenriched text"
            )
            self.degradations.append(f"{context_name}: truncated at max_tokens on every attempt")
            return text

        # Every attempt was unparseable AND the fallback already declined each
        # one in the loop above, so there is nothing left to try.
        logger.warning(f"  {context_name}: JSON parse failed, using original unenriched text")
        self.degradations.append(f"{context_name}: unparseable enrichment response")
        return text

    def _recover_enriched_text(
        self,
        content: str,
        text: str,
        context_name: str
    ) -> Optional[str]:
        """Regex recovery of `enriched_text` from an unparseable reply.

        Run on every unparseable attempt, immediately, before deciding whether
        to escalate: a rescued reply is the answer and costs nothing further.
        Returns None when the extraction does not survive validation, which
        means the caller should escalate (or, out of profiles, degrade).

        Only ever called on a reply the model finished writing. A `max_tokens`
        reply is clipped by definition, and recovering a known-clipped text is
        the 2026-09-04 incident.

        The validation exists because an unparseable response is usually a
        clipped one, and half a summary reads like a whole one.
        """
        match = re.search(r'"enriched_text"\s*:\s*"((?:[^"\\]|\\.)*)"', content, re.DOTALL)
        if not match:
            return None

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

        logger.warning(f"  {context_name}: regex extraction failed validation (brackets={open_brackets}/{close_brackets}, incomplete={has_incomplete_link}, short={is_too_short})")
        return None

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
