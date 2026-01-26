"""
Continuity Coordinator

Orchestrates the two-stage continuity detection process:
  Stage A: Parallel story matching (4 matchers)
  Stage B: Editorial curation (1 curator)
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from ..llm_client import AsyncAnthropicClient
from ..base import CategoryReport, AnalyzedItem, StoryMatch, ContinuationInfo
from .matcher import StoryMatcher
from .curator import EditorialCurator

logger = logging.getLogger(__name__)


class ContinuityCoordinator:
    """
    Coordinates the two-stage continuity detection process.

    Flow:
    1. Load historical data from past N days
    2. Run 4 parallel matchers (Stage A) - one per category
    3. Aggregate all matches
    4. Run editorial curator (Stage B) - makes nuanced decisions
    5. Apply continuation info to items
    6. Filter demoted items from top_items
    """

    # Categories to process
    CATEGORIES = ['news', 'research', 'social', 'reddit']

    def __init__(
        self,
        async_client: AsyncAnthropicClient,
        web_dir: str,
        target_date: str,
        lookback_days: int = 2
    ):
        """
        Initialize continuity coordinator.

        Args:
            async_client: Async Anthropic client for LLM calls.
            web_dir: Directory containing web/data/{date} folders.
            target_date: Current report date (YYYY-MM-DD).
            lookback_days: Number of days to look back for historical items.
        """
        self.async_client = async_client
        self.web_dir = web_dir
        self.target_date = target_date
        self.lookback_days = lookback_days
        self.data_dir = os.path.join(web_dir, 'data')

    async def process(
        self,
        category_reports: Dict[str, CategoryReport]
    ) -> Dict[str, CategoryReport]:
        """
        Full continuity detection pipeline.

        Args:
            category_reports: Dict mapping category to CategoryReport.

        Returns:
            Updated category_reports with continuation info applied.
        """
        logger.info(f"Continuity detection: looking back {self.lookback_days} days from {self.target_date}")

        # Step 1: Load historical data
        historical_items = self._load_historical_items()
        if not historical_items:
            logger.info("No historical data found, skipping continuity detection")
            return category_reports

        logger.info(f"Loaded {len(historical_items)} historical items")

        # Step 2: Stage A - Parallel matching
        logger.info("Stage A: Running parallel matchers...")
        all_matches = await self._run_matchers(category_reports, historical_items)

        if not all_matches:
            logger.info("No story matches found")
            return category_reports

        logger.info(f"Stage A complete: {len(all_matches)} matches found")

        # Fill in historical item details for matches
        historical_lookup = {item['id']: item for item in historical_items}
        for match in all_matches:
            hist_item = historical_lookup.get(match.historical_item_id, {})
            match.historical_category = hist_item.get('category', '')
            match.historical_date = hist_item.get('date', '')
            match.historical_title = hist_item.get('title', '')

        # Step 3: Stage B - Editorial curation
        logger.info("Stage B: Running editorial curator...")
        today_lookup = self._build_today_lookup(category_reports)
        continuations = await self._run_curator(all_matches, today_lookup, historical_lookup)

        if not continuations:
            logger.info("No continuation decisions made")
            return category_reports

        logger.info(f"Stage B complete: {len(continuations)} decisions made")

        # Step 4: Apply continuations to items
        category_reports = self._apply_continuations(category_reports, continuations)

        # Step 5: Filter demoted items from top_items
        category_reports = self._filter_demoted_items(category_reports)

        return category_reports

    def _load_historical_items(self) -> List[Dict[str, Any]]:
        """
        Load historical items from previous days.

        Returns:
            List of historical item dicts with id, title, summary, category, date.
        """
        items = []
        target_dt = datetime.strptime(self.target_date, '%Y-%m-%d')

        for days_ago in range(1, self.lookback_days + 1):
            check_date = target_dt - timedelta(days=days_ago)
            date_str = check_date.strftime('%Y-%m-%d')
            date_dir = os.path.join(self.data_dir, date_str)

            if not os.path.exists(date_dir):
                logger.debug(f"No data directory for {date_str}")
                continue

            for category in self.CATEGORIES:
                category_file = os.path.join(date_dir, f'{category}.json')
                if not os.path.exists(category_file):
                    continue

                try:
                    with open(category_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Get items from the category file
                    category_items = data.get('items', [])

                    # Only take top items (to reduce context size)
                    for item in category_items[:15]:
                        items.append({
                            'id': item.get('id', ''),
                            'date': date_str,
                            'category': category,
                            'title': item.get('title', ''),
                            'summary': item.get('summary', ''),
                            'source': item.get('source', ''),
                            'url': item.get('url', '')
                        })

                except Exception as e:
                    logger.warning(f"Failed to load {category_file}: {e}")
                    continue

        return items

    async def _run_matchers(
        self,
        category_reports: Dict[str, CategoryReport],
        historical_items: List[Dict[str, Any]]
    ) -> List[StoryMatch]:
        """
        Run all category matchers in parallel.

        Returns:
            Aggregated list of matches from all matchers.
        """
        # Create matchers for each category
        matchers = {
            cat: StoryMatcher(self.async_client, cat)
            for cat in self.CATEGORIES
        }

        # Create matching tasks
        async def match_category(category: str) -> List[StoryMatch]:
            if category not in category_reports:
                return []

            report = category_reports[category]
            # Match top 15 items per category
            items_to_match = report.top_items[:15]

            if not items_to_match:
                return []

            matcher = matchers[category]
            return await matcher.find_matches(items_to_match, historical_items)

        # Run all matchers in parallel
        tasks = [match_category(cat) for cat in self.CATEGORIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        all_matches = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Matcher for {self.CATEGORIES[i]} failed: {result}")
                continue
            all_matches.extend(result)

        return all_matches

    async def _run_curator(
        self,
        matches: List[StoryMatch],
        today_lookup: Dict[str, AnalyzedItem],
        historical_lookup: Dict[str, Dict[str, Any]]
    ) -> List[ContinuationInfo]:
        """
        Run the editorial curator on matched pairs.

        Returns:
            List of ContinuationInfo decisions.
        """
        curator = EditorialCurator(self.async_client)
        return await curator.curate(matches, today_lookup, historical_lookup)

    def _build_today_lookup(
        self,
        category_reports: Dict[str, CategoryReport]
    ) -> Dict[str, AnalyzedItem]:
        """Build lookup of all today's items by ID."""
        lookup = {}
        for category, report in category_reports.items():
            for item in report.all_items:
                lookup[item.item.id] = item
        return lookup

    def _apply_continuations(
        self,
        category_reports: Dict[str, CategoryReport],
        continuations: List[ContinuationInfo]
    ) -> Dict[str, CategoryReport]:
        """
        Apply continuation info to matching items.

        Also prepends reference text to item summaries.
        """
        # Build lookup of continuations by today's item ID
        cont_lookup = {}
        for cont in continuations:
            today_id = getattr(cont, '_today_item_id', None)
            if today_id:
                cont_lookup[today_id] = cont

        if not cont_lookup:
            return category_reports

        # Apply to items in all categories
        for category, report in category_reports.items():
            for item in report.all_items:
                if item.item.id in cont_lookup:
                    cont = cont_lookup[item.item.id]
                    item.continuation = cont

                    # Prepend reference text to summary if not already there
                    if cont.reference_text and not item.summary.startswith(cont.reference_text[:20]):
                        # Add link to the original item
                        link = f"/?date={cont.original_date}&category={cont.original_category}#item-{cont.original_item_id}"

                        ref_text = cont.reference_text
                        link_added = False

                        # Replace category placeholder with link
                        for cat in self.CATEGORIES:
                            cat_formatted = f"**{cat.capitalize()}**"
                            if cat_formatted in ref_text:
                                ref_text = ref_text.replace(
                                    cat_formatted,
                                    f"[{cat.capitalize()}]({link})"
                                )
                                link_added = True

                        # Handle follow_up type (same category, no placeholder)
                        # Link "yesterday" to the original coverage
                        if not link_added and "yesterday" in ref_text.lower():
                            ref_text = ref_text.replace(
                                "yesterday",
                                f"[yesterday]({link})"
                            )

                        # Ensure comma before the new content
                        if not ref_text.rstrip().endswith((',', '.', ':', ';')):
                            ref_text = ref_text.rstrip() + ","

                        item.summary = f"{ref_text} {item.summary}"

            # Also update top_items references
            for item in report.top_items:
                if item.item.id in cont_lookup:
                    cont = cont_lookup[item.item.id]
                    item.continuation = cont

        return category_reports

    def _filter_demoted_items(
        self,
        category_reports: Dict[str, CategoryReport]
    ) -> Dict[str, CategoryReport]:
        """
        Filter demoted items from top_items (but keep in all_items).

        Backfills from all_items if top_items becomes too small.
        """
        for category, report in category_reports.items():
            original_top = report.top_items
            filtered_top = [
                item for item in original_top
                if not (item.continuation and item.continuation.should_demote)
            ]

            demoted_count = len(original_top) - len(filtered_top)
            if demoted_count > 0:
                logger.info(f"{category}: Demoted {demoted_count} items from top stories")

                # Backfill if needed
                if len(filtered_top) < 10:
                    # Get items not already in filtered_top
                    filtered_ids = {item.item.id for item in filtered_top}
                    remaining = [
                        item for item in report.all_items
                        if item.item.id not in filtered_ids
                        and not (item.continuation and item.continuation.should_demote)
                    ]
                    needed = 10 - len(filtered_top)
                    filtered_top.extend(remaining[:needed])

                report.top_items = filtered_top

        return category_reports
