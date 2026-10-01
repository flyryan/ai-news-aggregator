"""The stream size ladder must trim outputs gradually, not fall off a cliff.

Regression test for 2026-09-08 onward. Output volume roughly doubled, the full
stream gzipped to 0.9-1.5 MB against a 600 KB cap, and the ladder went straight
from "everything" to "thinking only for every minor call" -- so ~150 of ~160
transcripts showed reasoning but no output. A modest overshoot must now cost only
the largest outputs, and only as many as needed.
"""

import gzip
import importlib
import json
import random
import string
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Same dependency-light import shim as replay_secret_guard_test.
if "agents" not in sys.modules:
    _pkg = types.ModuleType("agents")
    _pkg.__path__ = [str(REPO_ROOT / "agents")]
    sys.modules["agents"] = _pkg
    importlib.import_module("agents.cost_tracker")
    importlib.import_module("agents.replay_taxonomy")

from generators.replay_generator import DEFAULT_MAX_STREAM_BYTES, ReplayGenerator


def _noise(rng: random.Random, n: int) -> str:
    # Incompressible, so the gzip size tracks the text size predictably.
    return "".join(rng.choice(string.ascii_letters) for _ in range(n))


def _fixture(sizes):
    rng = random.Random(7)
    spans, calls = [], []
    for i, size in enumerate(sizes, start=1):
        cid = f"c{i:03d}"
        spans.append({
            "id": cid,
            "deltas": {"t": [10, 20], "kind": [0, 1], "text": [_noise(rng, 200), _noise(rng, size)]},
        })
        calls.append({"id": cid, "role": "map", "has_stream": True})
    return {"calls": spans}, calls


def _stored_text(blob):
    doc = json.loads(gzip.decompress(blob))
    return {
        cid: "".join(x for k, x in zip(v["kind"], v["text"]) if k == 1)
        for cid, v in doc["calls"].items()
    }


class StreamCapTests(unittest.TestCase):
    def test_default_cap_fits_a_measured_weekday(self):
        """2026-10-01's uncapped stream measured 922,936 bytes gzipped."""
        self.assertGreaterEqual(DEFAULT_MAX_STREAM_BYTES, 1_500_000)

    def test_small_overshoot_drops_only_the_largest_outputs(self):
        recorder, calls = _fixture([40_000, 30_000] + [5_000] * 20)
        generator = ReplayGenerator("/tmp/unused", max_stream_bytes=110_000)

        blob, note = generator._build_stream(recorder, calls, "2026-10-01")

        self.assertLessEqual(len(blob), 110_000)
        self.assertEqual(note, "text_dropped_for_largest_minor_calls")
        text = _stored_text(blob)
        self.assertEqual(text.get("c001", ""), "", "the largest output goes first")
        kept = [cid for cid in (f"c{i:03d}" for i in range(3, 23)) if text.get(cid)]
        self.assertEqual(len(kept), 20, "small outputs survive a modest overshoot")
        self.assertTrue(all(c["has_stream"] for c in calls), "thinking is kept for every call")

    def test_fits_untouched_when_under_the_cap(self):
        recorder, calls = _fixture([5_000] * 5)
        blob, note = ReplayGenerator("/tmp/unused", max_stream_bytes=1_000_000)._build_stream(
            recorder, calls, "2026-10-01"
        )
        self.assertIsNone(note)
        self.assertTrue(all(_stored_text(blob).values()))


if __name__ == "__main__":
    unittest.main()
