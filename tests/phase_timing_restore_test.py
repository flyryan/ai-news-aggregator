"""Resumed runs must replay checkpoint-loaded phases with their real timing.

2026-08-22: a ``--resume-from 2`` run published a replay in which source
gathering played back instantly. The original run had collected for ~11
minutes, but the resuming run recorded Phase 1 via ``skip_phase`` -- a
zero-width, status=skipped record -- because no timing survived the
checkpoint round-trip. Every checkpoint-loaded phase (1, 2, 2.5, 3,
4-4.7 depending on resume point) has this problem; the newsroom paces each
agent by its phase window, so zero-width means invisible work.

Fix contract:
  - ``PhaseTracker.export_timings()`` snapshots completed-phase windows so
    the orchestrator can persist them alongside checkpoint payloads.
  - ``PhaseTracker.restore_phase()`` re-registers such a window on the
    resuming run's tracker.
  - ``_save_checkpoint(..., phase_timings=...)`` embeds them under
    ``_phase_timings``; legacy checkpoints without the key keep the old
    skip behavior.

Stdlib-only unittest:

  python3 -m unittest tests.phase_timing_restore_test -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.phase_tracker import PhaseTracker  # noqa: E402
from agents.orchestrator import MainOrchestrator  # noqa: E402


def _run_two_phases(tracker):
    tracker.start_phase("Phase 0: Ecosystem Context")
    time.sleep(0.01)
    tracker.end_phase('success', details="fast")
    tracker.start_phase("Phase 1: Gathering")
    time.sleep(0.02)
    tracker.end_phase('success', details="849 items")


class ExportRestoreRoundTripTest(unittest.TestCase):
    def test_export_captures_real_windows(self):
        tracker = PhaseTracker()
        _run_two_phases(tracker)

        timings = tracker.export_timings()
        self.assertIn("Phase 1: Gathering", timings)
        rec = timings["Phase 1: Gathering"]
        self.assertGreater(rec["start_time"], 0)
        self.assertGreater(rec["end_time"], rec["start_time"])
        self.assertEqual(rec["status"], "success")

    def test_restore_reproduces_window_and_status(self):
        source = PhaseTracker()
        _run_two_phases(source)
        timings = source.export_timings()

        resumed = PhaseTracker()
        resumed.restore_phase(
            "Phase 1: Gathering", timings["Phase 1: Gathering"],
            details="loaded from checkpoint (849 items)")

        self.assertEqual(len(resumed.phases), 1)
        rec = resumed.phases[0]
        self.assertEqual(rec.status, "success",
                         "a restored phase ran successfully once; it is not skipped")
        self.assertAlmostEqual(rec.duration,
                               timings["Phase 1: Gathering"]["end_time"]
                               - timings["Phase 1: Gathering"]["start_time"],
                               places=3)
        self.assertEqual(rec.details, "loaded from checkpoint (849 items)")

        # And the serialized shape feeds the replay generator directly.
        as_dict = resumed.to_dict()[0]
        self.assertIsNotNone(as_dict["start_time"])
        self.assertIsNotNone(as_dict["end_time"])
        self.assertGreater(as_dict["duration"], 0)


class CheckpointEmbeddingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orch = MainOrchestrator.__new__(MainOrchestrator)
        self.orch.data_dir = self.tmp
        self.orch.target_date = "2026-08-22"

    def test_save_checkpoint_embeds_phase_timings(self):
        tracker = PhaseTracker()
        _run_two_phases(tracker)

        self.orch._save_checkpoint(
            "gathering", {"categories": {"news": [1]}},
            phase_timings=tracker.export_timings())

        saved = self._saved_checkpoint()
        self.assertIn("_phase_timings", saved)
        self.assertIn("Phase 1: Gathering", saved["_phase_timings"])
        # Payload keys survive untouched for the restore paths.
        self.assertEqual(saved["categories"], {"news": [1]})

    def test_save_checkpoint_without_timings_stays_clean(self):
        self.orch._save_checkpoint("gathering", {"categories": {}})
        saved = self._saved_checkpoint()
        self.assertNotIn("_phase_timings", saved)

    def _saved_checkpoint(self):
        """Read the checkpoint back, closing the handle (else a ResourceWarning
        leaks into every run of this suite)."""
        path = os.path.join(self.tmp, "checkpoints", "2026-08-22", "gathering.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)


class RestoreOrSkipTest(unittest.TestCase):
    def setUp(self):
        self.orch = MainOrchestrator.__new__(MainOrchestrator)

    def test_restores_when_checkpoint_carries_timing(self):
        tracker = PhaseTracker()
        _run_two_phases(tracker)
        checkpoint = {
            "_phase_timings": tracker.export_timings(),
            "categories": {},
        }

        phases = PhaseTracker()
        self.orch._restore_or_skip_phase(
            phases, "Phase 1: Gathering", checkpoint,
            "loaded from checkpoint (849 items)")

        self.assertEqual(len(phases.phases), 1)
        self.assertEqual(phases.phases[0].status, "success")

    def test_skips_legacy_checkpoints_without_timing(self):
        phases = PhaseTracker()
        self.orch._restore_or_skip_phase(
            phases, "Phase 1: Gathering", {},
            "loaded from checkpoint (849 items)")

        self.assertEqual(len(phases.phases), 1)
        self.assertEqual(phases.phases[0].status, "skipped")

    def test_skips_timing_entry_missing_start(self):
        phases = PhaseTracker()
        self.orch._restore_or_skip_phase(
            phases, "Phase 1: Gathering",
            {"_phase_timings": {"Phase 1: Gathering": {"start_time": None}}},
            "loaded from checkpoint")

        self.assertEqual(phases.phases[0].status, "skipped")


if __name__ == "__main__":
    unittest.main()
