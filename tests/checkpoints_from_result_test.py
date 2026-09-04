"""A CI-run day must stay repairable after the runner is gone.

What happened
-------------
2026-09-04: the report ran on a GitHub Actions runner and lost internal links
on the executive summary and three of four category summaries. Task 3 had just
made that exact failure repairable with ``--resume-from 4.5`` -- but the repair
reads ``data/checkpoints/<date>/``, and ``data/`` is gitignored and dies with
the runner. The diagnostics artifact carried only ``llm_metrics.jsonl``,
``cost_report_*.json`` and ``orchestrator_result_*.json``, so the one day the
repair path existed for was the one day it could not run. Fixing four missing
link sets meant re-running gathering, analysis, topic detection, the executive
summary and a paid hero image.

Two changes close that: the workflow now uploads ``data/checkpoints/**`` going
forward, and ``scripts/checkpoints_from_result.py`` rebuilds the checkpoints
from an ``orchestrator_result_*.json`` for the days where only that survived.

This test pins the second one to the shapes the orchestrator actually restores.
A synthesized checkpoint that ``_restore_gathered_items`` /
``_restore_category_reports`` / ``_restore_summary_checkpoint`` cannot read is
worse than none at all -- it would fail deep inside a resume, after the operator
has already decided the day is recoverable. So the fixture builds real
``AnalyzedItem`` objects and asserts the output round-trips through the real
``from_dict`` classmethods rather than through a hand-written expectation.

Locked in:

  1. ``gathering`` entries are CollectedItem-shaped, not AnalyzedItem-shaped.
     ``AnalyzedItem.to_dict()`` flattens the analysis fields into the same dict
     as the item's own, so an unfiltered copy would carry ``summary`` /
     ``importance_score`` / ``reasoning`` / ``themes`` into a gathering
     checkpoint and quietly re-import an analyzed item as a collected one.
  2. ``analysis`` survives ``CategoryReport.from_dict`` with its items and
     summary intact.
  3. ``_phase_timings`` matches ``PhaseTracker.export_timings()``: only phases
     with BOTH bounds, since ``restore_phase`` needs a real window and a phase
     that never finished has nothing to restore.
  4. ``summary`` carries the enriched texts the repair keeps, and ``hero``
     appears only when the day actually produced an image -- restoring a hero
     checkpoint with no url would publish the day without one.
  5. No ``_replay`` key anywhere: spans are memory-only and died with the run.
  6. ``write_checkpoints`` refuses to overwrite an existing checkpoint
     directory unless ``--force``; real checkpoints beat synthesized ones.
  7. Every ``git checkout`` this script hands an operator -- in the module
     docstring and in what ``main()`` prints -- is scoped to ``replay-*``.
     ``git checkout HEAD -- web/data/<date>/`` restores the WHOLE published
     date directory: ``summary.json``, the category files and the hero. Run
     straight after a ``--resume-from 4.5`` repair, as the guidance tells the
     operator to, it silently reverts the re-enriched summaries that repair
     just paid for -- undoing the exact failure this script exists to fix.

Stdlib-only unittest (no network, no LLM):

  python3 -m unittest tests.checkpoints_from_result_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from agents.base import AnalyzedItem, CategoryReport, CollectedItem  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "checkpoints_from_result.py"


def load_script():
    """Import the script by path -- `scripts/` is not a package.

    Same pattern `run_pipeline._validate_generated_report` uses to reach
    `scripts/validate_report.py`.
    """
    spec = importlib.util.spec_from_file_location("checkpoints_from_result", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_analyzed_item(item_id: str, category: str) -> AnalyzedItem:
    """A real AnalyzedItem, so its flattened dict shape is authoritative."""
    return AnalyzedItem(
        item=CollectedItem(
            id=item_id,
            title=f"{category} headline {item_id}",
            content=f"body text for {item_id}",
            url=f"https://example.test/{category}/{item_id}",
            author="someone",
            published="2026-09-03T12:00:00",
            source="Example Source",
            source_type="rss" if category == "news" else "arxiv",
            tags=["ai"],
            metadata={"feed": "example"},
            collected_at="2026-09-04T03:05:00",
            keywords=["agents"],
        ),
        summary=f"summary for {item_id}",
        importance_score=72.5,
        reasoning="matters because it ships",
        themes=["agents", "tooling"],
        thinking="some thinking",
        continuation=None,
    )


def make_result() -> dict:
    """An orchestrator_result_*.json payload, in OrchestratorResult.to_dict() shape."""
    categories = {}
    for category in ("news", "research"):
        items = [
            make_analyzed_item(f"{category}-1", category),
            make_analyzed_item(f"{category}-2", category),
        ]
        categories[category] = {
            "category": category,
            "top_items": [i.to_dict() for i in items],
            "all_items": [i.to_dict() for i in items],
            "category_summary": f"{category} summary with [a link](/?date=2026-09-04#item-x)",
            "themes": [
                {
                    "name": f"{category} theme",
                    "description": "theme description",
                    "item_count": 2,
                    "example_items": [f"{category}-1"],
                    "importance": 80.0,
                }
            ],
            "cross_signals": [f"{category} signal"],
            "total_collected": 2,
            "analysis_timestamp": "2026-09-04T03:30:00",
            "thinking": "analysis thinking",
            "degradations": [],
        }

    return {
        "date": "2026-09-04",
        "coverage_date": "2026-09-03",
        "coverage_start": "2026-09-03T00:00:00",
        "coverage_end": "2026-09-03T23:59:59",
        "executive_summary": "Exec summary with [a link](/?date=2026-09-04#item-news-1).",
        "top_topics": [
            {
                "name": "Agents everywhere",
                "description": "plain description",
                "description_html": "<p>html description</p>",
                "category_breakdown": {"news": 2, "research": 1},
                "representative_items": ["news-1", "research-1"],
                "importance": 91.0,
            }
        ],
        "category_reports": categories,
        "total_items_collected": 4,
        "total_items_analyzed": 4,
        "collection_status": {
            "news": {"status": "success", "items": 2},
            "research": {"status": "partial", "items": 2},
        },
        "hero_image_url": "/data/2026-09-04/hero.webp?v=1",
        "hero_image_prompt": "a skunk reading the news",
        "hero_image_usage": {"input_tokens": 10, "output_tokens": 20},
        "phase_status": [
            {
                "name": "Phase 1: Gathering",
                "status": "success",
                "duration": 120.0,
                "start_time": 1000.0,
                "end_time": 1120.0,
                "error": None,
                "details": "4 sources",
            },
            {
                "name": "Phase 2: Analysis",
                "status": "partial",
                "duration": 300.0,
                "start_time": 1120.0,
                "end_time": 1420.0,
                "error": None,
                "details": "1 batch lost",
            },
            {
                "name": "Phase 4.5: Link Enrichment",
                "status": "success",
                "duration": 60.0,
                "start_time": 1420.0,
                "end_time": 1480.0,
                "error": None,
                "details": None,
            },
            # Killed mid-phase: no end_time, so export_timings() would drop it
            # and restore_phase() has no window to restore.
            {
                "name": "Phase 5: Assembly",
                "status": "failed",
                "duration": 0.0,
                "start_time": 1480.0,
                "end_time": None,
                "error": "runner died",
                "details": None,
            },
        ],
        "degradations": ["3/4 category summaries unenriched"],
        "orchestrator_thinking": "summary thinking",
        "generated_at": "2026-09-04T04:00:00",
    }


class BuildCheckpointsTest(unittest.TestCase):
    """build_checkpoints() must emit exactly what the orchestrator restores."""

    def setUp(self):
        self.module = load_script()
        self.result = make_result()
        self.checkpoints = self.module.build_checkpoints(self.result)

    def test_gathering_items_are_collected_not_analyzed(self):
        gathering = self.checkpoints["gathering"]
        self.assertEqual(gathering["collection_status"], self.result["collection_status"])
        self.assertEqual(sorted(gathering["categories"]), ["news", "research"])

        for category, entries in gathering["categories"].items():
            self.assertEqual(len(entries), 2, category)
            for entry in entries:
                # The analysis fields AnalyzedItem.to_dict() flattens in must be gone.
                for leaked in ("summary", "importance_score", "reasoning", "themes"):
                    self.assertNotIn(
                        leaked, entry, f"{leaked} leaked into {category} gathering entry"
                    )
                restored = CollectedItem.from_dict(entry)
                self.assertTrue(restored.id)
                self.assertTrue(restored.url)
                self.assertEqual(restored.source_type, "rss" if category == "news" else "arxiv")

    def test_analysis_round_trips_through_category_report(self):
        report = CategoryReport.from_dict(self.checkpoints["analysis"]["category_reports"]["news"])
        self.assertEqual(len(report.all_items), 2)
        self.assertEqual(len(report.top_items), 2)
        self.assertEqual(
            report.category_summary,
            self.result["category_reports"]["news"]["category_summary"],
        )
        self.assertEqual(report.total_collected, 2)
        self.assertEqual(len(report.themes), 1)
        self.assertEqual(report.all_items[0].summary, "summary for news-1")

    def test_topics_checkpoint_matches_result(self):
        topics = self.checkpoints["topics"]
        self.assertEqual(topics["top_topics"], self.result["top_topics"])
        self.assertEqual(topics["thinking"], "")

    def test_summary_checkpoint_carries_enriched_texts(self):
        summary = self.checkpoints["summary"]
        self.assertEqual(summary["executive_summary"], self.result["executive_summary"])
        self.assertEqual(summary["thinking"], self.result["orchestrator_thinking"])
        self.assertEqual(
            summary["enriched_category_summaries"],
            {
                "news": self.result["category_reports"]["news"]["category_summary"],
                "research": self.result["category_reports"]["research"]["category_summary"],
            },
        )
        self.assertEqual(summary["enriched_topics"], self.result["top_topics"])

    def test_hero_checkpoint_present_when_the_day_made_an_image(self):
        hero = self.checkpoints["hero"]
        self.assertEqual(hero["hero_image_url"], self.result["hero_image_url"])
        self.assertEqual(hero["hero_image_prompt"], self.result["hero_image_prompt"])
        self.assertEqual(hero["hero_image_usage"], self.result["hero_image_usage"])

    def test_hero_checkpoint_absent_when_no_image_was_generated(self):
        result = make_result()
        result["hero_image_url"] = None
        checkpoints = self.module.build_checkpoints(result)
        self.assertNotIn("hero", checkpoints)
        # The rest of the day is still repairable.
        self.assertEqual(sorted(checkpoints), ["analysis", "gathering", "summary", "topics"])

    def test_phase_timings_only_carry_completed_phases(self):
        expected = {
            "Phase 1: Gathering": {"start_time": 1000.0, "end_time": 1120.0, "status": "success"},
            "Phase 2: Analysis": {"start_time": 1120.0, "end_time": 1420.0, "status": "partial"},
            "Phase 4.5: Link Enrichment": {
                "start_time": 1420.0, "end_time": 1480.0, "status": "success",
            },
        }
        for name, checkpoint in self.checkpoints.items():
            self.assertEqual(checkpoint.get("_phase_timings"), expected, f"{name} timings")

    def test_no_replay_bundle_is_invented(self):
        for name, checkpoint in self.checkpoints.items():
            self.assertNotIn(
                "_replay", checkpoint, f"{name} must not carry a fabricated replay bundle"
            )


class WriteCheckpointsTest(unittest.TestCase):
    """write_checkpoints() must land the files where a resume looks for them."""

    def setUp(self):
        self.module = load_script()
        self.checkpoints = self.module.build_checkpoints(make_result())
        self.data_dir = Path(tempfile.mkdtemp(prefix="checkpoints-from-result-"))
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def test_writes_all_five_checkpoints(self):
        written = self.module.write_checkpoints(self.checkpoints, self.data_dir, "2026-09-04")
        names = sorted(path.name for path in written)
        self.assertEqual(
            names,
            ["analysis.json", "gathering.json", "hero.json", "summary.json", "topics.json"],
        )
        checkpoint_dir = self.data_dir / "checkpoints" / "2026-09-04"
        for path in written:
            self.assertEqual(path.parent, checkpoint_dir)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertIsInstance(json.load(handle), dict)

    def test_refuses_to_clobber_existing_checkpoints(self):
        self.module.write_checkpoints(self.checkpoints, self.data_dir, "2026-09-04")
        with self.assertRaises(FileExistsError):
            self.module.write_checkpoints(self.checkpoints, self.data_dir, "2026-09-04")

    def test_force_overwrites(self):
        self.module.write_checkpoints(self.checkpoints, self.data_dir, "2026-09-04")
        written = self.module.write_checkpoints(
            self.checkpoints, self.data_dir, "2026-09-04", force=True
        )
        self.assertEqual(len(written), 5)


class ReplayRestoreGuidanceTest(unittest.TestCase):
    """The replay-restore command must not revert the repair it protects.

    The script's whole reason to exist is letting ``--resume-from 4.5``
    re-enrich a day whose checkpoints died with the runner. It then tells the
    operator to restore the replay artifacts, which the repair cannot rebuild.
    If that instruction names the date directory instead of ``replay-*``, the
    copy-paste immediately after the repair throws the repair away.
    """

    # Any web/data/<date>/ path the guidance names, in either the docstring's
    # placeholder form or a real formatted date, plus whatever follows it.
    DATE_PATH = re.compile(r"web/data/(?:<date>|\{date\}|\d{4}-\d{2}-\d{2})/(\S*)")

    def setUp(self):
        self.module = load_script()

    def _assert_scoped_to_replay(self, text, label):
        tails = self.DATE_PATH.findall(text)
        self.assertTrue(tails, f"{label} names no web/data/<date>/ path at all")
        for tail in tails:
            self.assertTrue(
                tail.startswith("replay-"),
                f"{label} points at web/data/<date>/{tail!r}: restoring anything "
                "wider than replay-* discards the re-enriched summaries the "
                "repair run just produced",
            )

    def test_module_docstring_scopes_the_checkout_to_replay_files(self):
        doc = self.module.__doc__
        self._assert_scoped_to_replay(doc, "module docstring")
        # Quoted so the shell hands the glob to git rather than expanding it
        # against the working tree.
        self.assertIn("git checkout HEAD -- 'web/data/<date>/replay-*'", doc)

    def test_printed_guidance_scopes_the_checkout_to_replay_files(self):
        guidance = self.module.replay_restore_guidance("2026-09-04")
        self._assert_scoped_to_replay(guidance, "printed guidance")
        self.assertIn("git checkout HEAD -- 'web/data/2026-09-04/replay-*'", guidance)

    def test_main_prints_the_same_guidance_it_documents(self):
        work_dir = Path(tempfile.mkdtemp(prefix="checkpoints-from-result-main-"))
        self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
        result_path = work_dir / "orchestrator_result_2026-09-04.json"
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(make_result(), handle)

        argv = ["checkpoints_from_result.py", str(result_path), "--data-dir", str(work_dir / "data")]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stdout):
                exit_code = self.module.main()

        printed = stdout.getvalue()
        self.assertEqual(exit_code, 0, printed)
        self._assert_scoped_to_replay(printed, "main() output")
        self.assertIn(
            self.module.replay_restore_guidance("2026-09-04"),
            printed,
            "main() must print the guidance verbatim, so docstring and output cannot drift",
        )


if __name__ == "__main__":
    unittest.main()
