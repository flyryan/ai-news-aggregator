"""Resumed-run replays must prepend checkpoint-loaded phases, not drop them.

2026-08-22: a ``--resume-from 2`` run published a replay where source
gathering (11 real minutes in the original run) played back instantly --
the restored phase carried absolute times from *before* the resuming
process's t0, so they clamped to a zero-width window.

Contract: phase records whose start predates this process's cost-report
start are "pre-run segments". The generator lays them sequentially at the
front of the timeline and shifts every in-run coordinate (phases, calls,
sources) by their total span, so call->phase containment stays exact and
nothing goes negative.

Stdlib-only unittest:

  python3 -m unittest tests.replay_resume_timing_test -v
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generators.replay_generator import ReplayGenerator  # noqa: E402


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _cost_report(start_epoch, duration_s, calls=()):
    return {
        "model": "stealth/ox-alpha",
        "start_time": _iso(start_epoch),
        "duration_seconds": duration_s,
        "tokens": {"input_tokens": 10, "output_tokens": 5},
        "calls": [
            {
                "timestamp": _iso(start_epoch + offset),
                "caller": f"test_analyzer.batch_{i}",
                "input_tokens": 100,
                "output_tokens": 50,
                "duration_seconds": 2.0,
                "model": "stealth/ox-alpha",
            }
            for i, offset in enumerate(calls)
        ],
    }


def _result(phase_records):
    return {"coverage_date": "", "collection_status": {}, "phase_status": phase_records}


class ResumedRunTimelineTest(unittest.TestCase):
    def setUp(self):
        self.gen = ReplayGenerator("/tmp/replay-test-web")

    def test_pre_run_phase_prepends_and_shifts_timeline(self):
        # Process started at t=1000; Phase 1 actually ran 400 -> 1060 in the
        # original process (a resume-from-2 restore).
        records = [
            {"name": "Phase 0: Ecosystem Context", "status": "success",
             "start_time": 999.9, "end_time": 1000.16, "duration": 0.26},
            {"name": "Phase 1: Gathering", "status": "success",
             "start_time": 400.0, "end_time": 1060.0, "duration": 660.0,
             "details": "loaded from checkpoint (863 items)"},
            {"name": "Phase 2: Analysis", "status": "success",
             "start_time": 1000.27, "end_time": 1300.27, "duration": 300.0},
        ]
        index, _, _ = self.gen.build(
            date="2026-08-22",
            cost_report=_cost_report(1000.0, 300, calls=(10.0, 20.0)),
            orchestrator_result=_result(records),
        )

        phases = index["phases"]
        # Phase 0 ran live first (t0 resolves to its start, 999.9).
        self.assertEqual(phases[0]["id"], "phase-0")
        self.assertEqual(phases[0]["start_ms"], 0)
        self.assertEqual(phases[0]["end_ms"], 260)

        # The restored Gathering slots into its ordinal position with its
        # real 11-minute width, sequenced right after Phase 0.
        self.assertEqual(phases[1]["id"], "phase-1")
        self.assertEqual(phases[1]["start_ms"], 260)
        self.assertEqual(phases[1]["end_ms"], 660_260)
        self.assertEqual(phases[1]["status"], "success")

        # Live Phase 2 keeps its in-run offset, shifted by the restored span.
        self.assertEqual(phases[2]["id"], "phase-2")
        self.assertEqual(phases[2]["start_ms"], 660_000 + 370)
        self.assertGreaterEqual(index["duration_ms"], phases[2]["end_ms"])

        # Calls land in Phase 2 by containment, never in the prepended span.
        analysis_calls = [c for c in index["calls"]
                          if c["caller"] == "test_analyzer.batch_0"]
        self.assertTrue(analysis_calls)
        self.assertEqual(analysis_calls[0]["phase_id"], "phase-2")

    def test_normal_run_has_no_offset(self):
        records = [
            {"name": "Phase 1: Gathering", "status": "success",
             "start_time": 1000.0, "end_time": 1060.0, "duration": 60.0},
            {"name": "Phase 2: Analysis", "status": "success",
             "start_time": 1060.27, "end_time": 1360.27, "duration": 300.0},
        ]
        index, _, _ = self.gen.build(
            date="2026-08-22",
            cost_report=_cost_report(1000.0, 360),
            orchestrator_result=_result(records),
        )
        phases = index["phases"]
        self.assertEqual(phases[0]["start_ms"], 0)
        self.assertEqual(phases[1]["start_ms"], 60_270)

    def test_legacy_skipped_stays_zero_width(self):
        records = [
            {"name": "Phase 1: Gathering", "status": "skipped",
             "start_time": None, "end_time": None, "duration": 0.0,
             "details": "loaded from checkpoint"},
            {"name": "Phase 2: Analysis", "status": "success",
             "start_time": 1000.27, "end_time": 1300.27, "duration": 300.0},
        ]
        index, _, _ = self.gen.build(
            date="2026-08-22",
            cost_report=_cost_report(1000.0, 300),
            orchestrator_result=_result(records),
        )
        phases = index["phases"]
        self.assertEqual(phases[0]["status"], "skipped")
        self.assertEqual(phases[0]["end_ms"], phases[0]["start_ms"])


if __name__ == "__main__":
    unittest.main()
