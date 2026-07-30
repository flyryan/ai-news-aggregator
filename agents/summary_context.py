"""
Shared context assembly for the executive summary.

Both the live pipeline (agents/orchestrator.py) and the offline regeneration
script (scripts/regenerate_summary.py) feed the same fenced context blob to the
executive_summary prompt. The scaffolding lives here so the two entry points
cannot drift apart.

Section vocabulary: `=== X ===` opens a top-level section (with an explicit
`=== END X ===` close where variable-length prose would otherwise run into the
next section), `--- x ---` labels sub-blocks. The executive_summary template in
config/prompts.yaml references these sections by name -- keep them in sync.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PREVIOUS_COVERAGE_OPEN = (
    "=== PREVIOUS DAYS' COVERAGE (do NOT repeat these as new/breaking news) ==="
)
PREVIOUS_COVERAGE_CLOSE = "=== END PREVIOUS DAYS' COVERAGE ==="
TODAYS_DATA_OPEN = "=== TODAY'S DATA ==="


def load_previous_summaries(
    web_dir: str,
    target_date: str,
    lookback_days: int = 3,
) -> List[Tuple[str, str]]:
    """Collect (date, executive_summary) pairs for the days before target_date.

    Returns newest-first pairs; days with no summary.json (or no
    executive_summary in it) are skipped silently.
    """
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    pairs = []

    for days_ago in range(1, lookback_days + 1):
        check_date = target_dt - timedelta(days=days_ago)
        date_str = check_date.strftime('%Y-%m-%d')
        summary_path = os.path.join(web_dir, 'data', date_str, 'summary.json')

        if not os.path.exists(summary_path):
            continue

        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load previous summary for {date_str}: {e}")
            continue

        exec_summary = data.get('executive_summary', '')
        if exec_summary:
            pairs.append((date_str, exec_summary))

    return pairs


def format_previous_coverage(dated_summaries: Sequence[Tuple[str, str]]) -> str:
    """Wrap prior executive summaries in an explicitly closed section.

    Prior summaries are multi-paragraph markdown ending in a "Looking Ahead"
    paragraph; without the END marker the last one would flow straight into
    today's data.
    """
    if not dated_summaries:
        return ""

    blocks = [f"--- {date_str} ---\n{summary}" for date_str, summary in dated_summaries]
    return "\n\n".join([PREVIOUS_COVERAGE_OPEN, *blocks, PREVIOUS_COVERAGE_CLOSE])


def build_executive_context(
    target_date: str,
    previous_coverage: str,
    topics: Sequence[Tuple[str, str]],
    categories: Sequence[Tuple[str, str, Optional[str]]],
) -> str:
    """Assemble the fenced user-message context for the executive summary.

    Args:
        target_date: Report date (YYYY-MM-DD).
        previous_coverage: Output of format_previous_coverage(), or "".
        topics: (name, description) pairs in rank order, already sliced.
        categories: (category, category_summary, top_story_title or None).
    """
    parts = [f"Date: {target_date}", ""]

    if previous_coverage:
        parts.append(previous_coverage)
        parts.append("")

    parts.append(TODAYS_DATA_OPEN)
    parts.append("")

    parts.append("TOP TOPICS:")
    for i, (name, description) in enumerate(topics, 1):
        parts.append(f"{i}. {name}: {description}")
    parts.append("")

    for category, category_summary, top_story in categories:
        parts.append(f"--- {category.upper()} ---")
        parts.append(f"Summary: {category_summary}")
        if top_story:
            parts.append(f"Top story: {top_story}")
        parts.append("")

    return "\n".join(parts)
