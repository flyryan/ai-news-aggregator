"""A resumed run must keep the agents that ran in the checkpointed process.

2026-08-24: a `--resume-from 3` run published a replay whose Analysis phase had
a correct, real ~19-minute window and NOTHING inside it. The four analyzers and
the continuity agent were absent from the cast entirely -- 8 agents and 28 calls
where the same day's full run had 13 and 58.

The cause was not the resume timing work (21e3637 / fb7cbc1): that restored
phase *windows* and rebased pre-run *gatherer* spans, both of which worked.
Gatherers could be rebased because their instrumentation persists in
`collection_status`. LLM calls had no persisted equivalent -- the recorder is
memory-only by design ("no I/O, buffers flush once at end of run") and
`_build_calls` is fed solely by this process's cost report and recorder -- so
when the earlier process exited, those spans went with it.

Checkpoints now carry a `_replay` bundle and the generator merges it back,
placing each restored call inside its restored phase window by absolute epoch.

Stdlib-only unittest:

  python3 -m unittest tests.replay_restored_calls_test -v
"""

import importlib
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_replay_generator():
    """Import the generator without executing `agents/__init__.py`.

    The package `__init__` eagerly imports llm_client -> httpx -> anthropic,
    none of which exist in the dependency-light CI guard job; an ImportError
    here would take every other guard in that job down with it. Same shim
    `tests/replay_steps_test.py` uses.
    """
    if "agents" not in sys.modules:
        pkg = types.ModuleType("agents")
        pkg.__path__ = [str(REPO_ROOT / "agents")]
        sys.modules["agents"] = pkg
    importlib.import_module("agents.cost_tracker")
    importlib.import_module("agents.replay_taxonomy")
    from generators.replay_generator import ReplayGenerator

    return ReplayGenerator


ReplayGenerator = _load_replay_generator()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# The original process: started at 400.0, ran Analysis 400 -> 1060.
ORIGINAL_T0 = 400.0
ANALYSIS_START = 400.0
ANALYSIS_DURATION = 660.0

# The resuming process: t0 at 1000.0.
RESUME_T0 = 1000.0


def _records():
    """Phase 1 live, Phase 2 restored from checkpoint, Phase 3 live."""
    return [
        {"name": "Phase 1: Gathering", "status": "success",
         "start_time": 999.9, "end_time": 1000.16, "duration": 0.26},
        {"name": "Phase 2: Analysis", "status": "success",
         "start_time": ANALYSIS_START, "end_time": ANALYSIS_START + ANALYSIS_DURATION,
         "duration": ANALYSIS_DURATION,
         "details": "loaded from checkpoint (1101 items)"},
        {"name": "Phase 3: Topic Detection", "status": "success",
         "start_time": 1000.27, "end_time": 1300.27, "duration": 300.0},
    ]


def _cost_report():
    """This process's spend: one live orchestrator call, no analyzers."""
    return {
        "model": "stealth/ox-alpha",
        "start_time": _iso(RESUME_T0),
        "duration_seconds": 300.0,
        "tokens": {"input_tokens": 10, "output_tokens": 5},
        "total_cost": 1.0,
        "calls": [{
            "timestamp": _iso(RESUME_T0 + 10.0),
            "caller": "orchestrator.topics",
            "input_tokens": 100, "output_tokens": 50,
            "duration_seconds": 2.0, "model": "stealth/ox-alpha",
        }],
    }


def _bundle(callers=("research_analyzer.batch_0", "research_analyzer.reduce_rank")):
    """A checkpoint's replay bundle: calls made 100s and 200s into Analysis."""
    rows, spans = [], []
    for i, caller in enumerate(callers):
        offset = 100.0 * (i + 1)
        rows.append({
            "timestamp": _iso(ANALYSIS_START + offset),
            "caller": caller,
            "input_tokens": 900, "output_tokens": 400,
            "duration_seconds": 30.0, "model": "stealth/ox-alpha",
        })
        spans.append({
            "id": f"call-{i}", "caller": caller,
            "queued_ms": int((ANALYSIS_START + offset - ORIGINAL_T0) * 1000),
            "start_ms": int((ANALYSIS_START + offset - ORIGINAL_T0) * 1000),
            "end_ms": int((ANALYSIS_START + offset + 30.0 - ORIGINAL_T0) * 1000),
            "outcome": "ok", "thinking_chars": 4200, "text_chars": 800,
        })
    return {"t0_epoch": ORIGINAL_T0, "spans": spans, "cost_calls": rows}


def _result():
    return {"coverage_date": "", "collection_status": {}, "phase_status": _records()}


def _live_recorder():
    """The resuming process records its own calls -- only the live ones."""
    return {
        "enabled": True,
        "t0_epoch": RESUME_T0,
        "calls": [{
            "id": "live-0", "caller": "orchestrator.topics",
            "queued_ms": 10_000, "start_ms": 10_000, "end_ms": 12_000,
            "outcome": "ok", "thinking_chars": 1200, "text_chars": 400,
        }],
    }


class RestoredCallsTest(unittest.TestCase):
    def setUp(self):
        self.gen = ReplayGenerator("/tmp/replay-test-web")

    def _build(self, restored):
        index, _, _ = self.gen.build(
            date="2026-08-24",
            cost_report=_cost_report(),
            orchestrator_result=_result(),
            recorder_snapshot=_live_recorder(),
            restored_replay=restored,
        )
        return index

    def test_without_a_bundle_the_cast_is_missing(self):
        # The 2026-08-24 symptom, pinned: this is what we are fixing.
        index = self._build(None)
        agents = {a["id"] for a in index["agents"]}
        self.assertNotIn("research_analyzer", agents)
        self.assertEqual(index["run"].get("restored_calls"), 0)

    def test_restored_calls_put_the_analyst_back_on_stage(self):
        index = self._build(_bundle())
        agents = {a["id"] for a in index["agents"]}
        self.assertIn("research_analyzer", agents)
        callers = [c["caller"] for c in index["calls"]]
        self.assertIn("research_analyzer.batch_0", callers)
        self.assertIn("research_analyzer.reduce_rank", callers)
        self.assertEqual(index["run"]["restored_calls"], 2)

    def test_live_calls_survive_the_merge(self):
        index = self._build(_bundle())
        callers = [c["caller"] for c in index["calls"]]
        self.assertIn("orchestrator.topics", callers)

    def test_restored_calls_land_inside_their_restored_phase(self):
        # Containment is the whole point: a restored call must sit in the
        # Analysis window, not in the live phases around it.
        index = self._build(_bundle())
        phase = next(p for p in index["phases"] if p["id"] == "phase-2")
        restored = [c for c in index["calls"]
                    if c["caller"].startswith("research_analyzer")]
        self.assertEqual(len(restored), 2)
        for call in restored:
            self.assertGreaterEqual(call["start_ms"], phase["start_ms"])
            self.assertLessEqual(call["start_ms"], phase["end_ms"])

    def test_restored_calls_keep_their_relative_order_and_spacing(self):
        index = self._build(_bundle())
        phase = next(p for p in index["phases"] if p["id"] == "phase-2")
        by_caller = {c["caller"]: c for c in index["calls"]}
        first = by_caller["research_analyzer.batch_0"]["start_ms"]
        second = by_caller["research_analyzer.reduce_rank"]["start_ms"]
        # 100s and 200s into a phase that now starts at phase.start_ms.
        self.assertEqual(first, phase["start_ms"] + 100_000)
        self.assertEqual(second, phase["start_ms"] + 200_000)

    def test_restored_spend_is_excluded_from_this_run_totals(self):
        # The resuming process did not spend that money; saying it did would
        # double-count the day across two runs.
        plain = self._build(None)
        merged = self._build(_bundle())
        self.assertEqual(
            merged["run"]["total_cost_usd"], plain["run"]["total_cost_usd"]
        )

    def test_timings_stay_measured(self):
        # Restored values are real measurements from the original process, not
        # reconstructions -- so the never-fake-a-measurement flag stays true.
        index = self._build(_bundle())
        self.assertTrue(index["run"]["timings_measured"])

    def test_legacy_checkpoint_without_bundle_degrades_quietly(self):
        # Checkpoints written before this feature have no _replay key.
        index = self._build({"t0_epoch": None, "spans": [], "cost_calls": []})
        self.assertEqual(index["run"]["restored_calls"], 0)
        self.assertIn("orchestrator.topics", [c["caller"] for c in index["calls"]])

    def test_malformed_bundle_never_costs_the_replay(self):
        # A replay is a bonus feature; a bad bundle must not lose the artifact.
        index = self._build({"spans": "not-a-list", "cost_calls": [{"bad": object()}]})
        self.assertIn("calls", index)
        self.assertIn("orchestrator.topics", [c["caller"] for c in index["calls"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
