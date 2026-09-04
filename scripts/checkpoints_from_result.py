#!/usr/bin/env python3
"""
Rebuild resume checkpoints from an orchestrator_result_*.json.

`--resume-from` reads `data/checkpoints/<date>/`, but `data/` is gitignored and
dies with a GitHub Actions runner. On 2026-09-04 that cost us the one repair the
day needed: the report published with unenriched summaries, `--resume-from 4.5`
had just landed to fix exactly that, and the runner's checkpoints were gone --
the diagnostics artifact carried only llm_metrics, the cost report and the
orchestrator result. The workflow now uploads `data/checkpoints/**` going
forward; this script covers the days where only the orchestrator result survived.

It synthesizes the `gathering`, `analysis`, `topics`, `summary` and `hero`
checkpoints from that one file, in the exact shapes `_restore_gathered_items`,
`_restore_category_reports`, `_restore_top_topics`, `_restore_summary_checkpoint`
and `_load_hero_checkpoint` read back.

What it cannot rebuild, and why:

  * `_replay` -- the replay bundle is captured in memory by the recorder and
    dies with the process; it never reaches the orchestrator result. A
    synthesized checkpoint therefore carries no `_replay` key at all, and a
    repair run started from these checkpoints will publish a replay containing
    only the calls IT made. Restore just those artifacts from git after the
    repair run -- and before committing the repair, because `git checkout HEAD`
    restores from the last commit and no-ops once HEAD carries the stunted
    replay -- rather than shipping a stunted one:

        git checkout HEAD -- 'web/data/<date>/replay-*'

    Scoped to `replay-*`, and quoted so git rather than the shell expands the
    glob. Checking out the whole date directory would drag `summary.json`, the
    category files and the hero back to their pre-repair state -- reverting the
    re-enriched summaries the repair run just paid for.
  * What Phase 1 actually collected. The synthesized `gathering` checkpoint is
    built from each report's `all_items` -- the items that SURVIVED analysis,
    not what the gatherers returned; the discarded ones are nowhere in the
    result. These checkpoints are therefore only safe at `--resume-from 4.5`
    (and 4.6). Starting at 2 or 3 would re-analyze the survivors alone and
    publish a thinner day with every phase green, and `total_items_collected`
    on a day repaired from here is the survivor count, not the real one.
  * `top_items` ranking thinking, per-phase details and the wall-clock windows
    of phases that never finished. Only phases with both bounds become
    `_phase_timings`, matching PhaseTracker.export_timings().

Usage:
    python3 scripts/checkpoints_from_result.py data/processed/orchestrator_result_2026-09-04.json
    python3 scripts/checkpoints_from_result.py RESULT_JSON --data-dir ./data --force

Real checkpoints always beat synthesized ones, so an existing
`checkpoints/<date>/` is refused unless `--force` is given.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Union

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.base import CollectedItem  # noqa: E402

# Written in resume order, which is also the order _detect_resume_point scans
# them in reverse. `hero` is last because it is the only optional one.
CHECKPOINT_ORDER = ('gathering', 'analysis', 'topics', 'summary', 'hero')

# The only safe restore target after a repair run. Scoped to `replay-*` because
# a checkout of the whole date directory reverts the re-enriched summaries the
# repair just produced, and quoted so git -- not the shell -- expands the glob.
REPLAY_RESTORE_COMMAND = "git checkout HEAD -- 'web/data/{date}/replay-*'"


def replay_restore_guidance(date: str) -> str:
    """The post-repair replay note, printed by main() and mirrored in the docstring.

    One source of text so the two cannot drift: the docstring used to carry a
    directory-wide `git checkout` that undid the repair it was protecting.
    """
    return (
        "These checkpoints carry no replay bundle (spans are memory-only and died "
        "with the run), so restore only the day's replay artifacts from git after "
        "the repair run -- and before committing the repair, since once HEAD "
        "carries the stunted replay the checkout silently no-ops -- instead of "
        "publishing the stunted one it generates:\n"
        f"  {REPLAY_RESTORE_COMMAND.format(date=date)}\n"
        "Keep it scoped to replay-*: checking out the whole date directory would "
        "revert the re-enriched summaries the repair just produced."
    )


def _phase_timings(result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Rebuild PhaseTracker.export_timings() from the result's phase records.

    Only phases with BOTH bounds, exactly as export_timings() filters them: a
    phase that never ended has no window for restore_phase() to restore, and a
    half-open one would place its LLM calls anywhere in the replay.
    """
    timings: Dict[str, Dict[str, Any]] = {}
    for record in result.get('phase_status') or []:
        if not isinstance(record, dict):
            continue
        start_time = record.get('start_time')
        end_time = record.get('end_time')
        if not start_time or not end_time:
            continue
        timings[record.get('name', '')] = {
            'start_time': start_time,
            'end_time': end_time,
            'status': record.get('status'),
        }
    return timings


def _collected_item_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Strip an AnalyzedItem dict back down to its CollectedItem fields.

    AnalyzedItem.to_dict() flattens `summary`, `importance_score`, `reasoning`,
    `themes`, `thinking` and `continuation` into the same dict as the item's own
    fields. CollectedItem.from_dict() would drop them on load, but leaving them
    in the gathering checkpoint would make it look like Phase 1 collected
    analyzed items -- so they come out here, where it is visible.
    """
    return {k: v for k, v in item.items() if k in CollectedItem.__dataclass_fields__}


def build_checkpoints(result: Dict[str, Any]) -> Dict[str, dict]:
    """Map an orchestrator result onto the five resume checkpoints.

    Pure: no I/O, so the shapes can be asserted directly against the
    orchestrator's restore paths in tests.
    """
    category_reports = result.get('category_reports') or {}
    top_topics = result.get('top_topics') or []

    checkpoints: Dict[str, dict] = {
        'gathering': {
            'collection_status': result.get('collection_status') or {},
            'categories': {
                category: [
                    _collected_item_fields(item)
                    for item in (report.get('all_items') or [])
                ]
                for category, report in category_reports.items()
            },
        },
        'analysis': {'category_reports': category_reports},
        # Topic-detection thinking is not carried on the result (only the
        # orchestrator's summary thinking is), so the replay of a repaired day
        # shows Phase 3 with no prose rather than invented prose.
        'topics': {'top_topics': top_topics, 'thinking': ''},
        'summary': {
            'executive_summary': result.get('executive_summary', ''),
            'thinking': result.get('orchestrator_thinking') or '',
            # The published texts, links and all: a 4.5 repair re-enriches only
            # what still has none, so whatever was already linked is preserved.
            'enriched_category_summaries': {
                category: report.get('category_summary', '')
                for category, report in category_reports.items()
            },
            'enriched_topics': top_topics,
        },
    }

    # Only when the day actually produced an image. _load_hero_checkpoint
    # ignores a payload with no url, and writing one would just be a file that
    # never does anything.
    if result.get('hero_image_url'):
        checkpoints['hero'] = {
            'hero_image_url': result.get('hero_image_url'),
            'hero_image_prompt': result.get('hero_image_prompt'),
            'hero_image_usage': result.get('hero_image_usage'),
        }

    timings = _phase_timings(result)
    if timings:
        for checkpoint in checkpoints.values():
            checkpoint['_phase_timings'] = timings

    return checkpoints


def write_checkpoints(
    checkpoints: Dict[str, dict],
    data_dir: Union[str, Path],
    date: str,
    force: bool = False,
) -> List[Path]:
    """Write the checkpoints where a resume of `date` will look for them.

    Refuses an existing directory: real checkpoints from the run itself are
    always better than these reconstructions, and silently replacing them would
    trade a complete `_replay` bundle for none.
    """
    checkpoint_dir = Path(data_dir) / 'checkpoints' / date
    if checkpoint_dir.exists() and not force:
        raise FileExistsError(
            f"{checkpoint_dir} already exists -- refusing to overwrite real "
            f"checkpoints. Pass --force if the synthesized ones are wanted."
        )
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for name in CHECKPOINT_ORDER:
        if name not in checkpoints:
            continue
        path = checkpoint_dir / f"{name}.json"
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(checkpoints[name], handle, indent=2, ensure_ascii=False)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Rebuild data/checkpoints/<date>/ from an orchestrator_result_*.json, '
            'so a day whose runner checkpoints are gone can still be repaired.'
        )
    )
    parser.add_argument('result_json', help='Path to an orchestrator_result_YYYY-MM-DD.json')
    parser.add_argument('--data-dir', default='./data', help='Data directory (default: ./data)')
    parser.add_argument(
        '--force', action='store_true',
        help='Overwrite an existing checkpoints/<date>/ directory',
    )
    args = parser.parse_args()

    result_path = Path(args.result_json)
    try:
        with open(result_path, 'r', encoding='utf-8') as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not read {result_path}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    date = result.get('date')
    if not date:
        print(f"{result_path} has no 'date' field -- not an orchestrator result?", file=sys.stderr)
        return 1

    checkpoints = build_checkpoints(result)
    if 'hero' not in checkpoints:
        print(f"Note: {date} recorded no hero image; no hero checkpoint written.")

    try:
        written = write_checkpoints(checkpoints, args.data_dir, date, force=args.force)
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1

    for path in written:
        print(f"  wrote {path}")

    print()
    print(
        "Caution: `gathering` here holds the items that survived analysis, not what "
        "Phase 1 collected, so these checkpoints are only safe at --resume-from 4.5 "
        "(or 4.6) -- an earlier resume point would re-analyze the survivors alone and "
        "publish a thinner day with green phases."
    )
    print()
    print("Repair the day's link enrichment with:")
    print(
        f'  TARGET_DATE="{date}" python3 run_pipeline.py --resume-from 4.5 '
        f'--config-dir ./config --data-dir {args.data_dir} --web-dir ./web'
    )
    print()
    print(replay_restore_guidance(date))
    return 0


if __name__ == '__main__':
    sys.exit(main())
