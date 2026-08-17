"""Per-unit source steps: capture, publication, and the bug that motivated them.

Before 2026-08-17 the replay drew Social, Reddit and Research as bars that all
advanced together and finished together. Two separate defects produced that, and
both are locked down here.

**The social key mismatch.** `social_gatherer` timed its three platforms under the
bare keys `twitter` / `bluesky` / `mastodon`, but the orchestrator publishes those
rows as `social_twitter` / `social_bluesky` / `social_mastodon`. Timing joined to
`collection_status` by exact key, so the bare names became orphan rows the generator
did not recognise and dropped, while the three real rows never received a span and
fell back to the whole gathering phase. Published data confirms it: on 2026-08-15,
-16 and -17 all three social rows carry `timing_measured: false` with byte-identical
spans.

**One bar over many units.** A source row covers 15 subreddits or 26 feeds, and the
fill between its two ends was linear interpolation -- an animation presented as a
measurement, which the replay's own design principle forbids. `time_step` records
each unit so the bar can advance as a step function over real completions.

The load-bearing property in the second half is that a step is either a genuine
measurement or absent. There is no phase-span fallback and no clamping, because a
step exists precisely to say when one unit came back.
"""

import importlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_base_module():
    """Load `agents/base.py` without executing `agents/__init__.py`.

    The package `__init__` eagerly imports llm_client -> httpx -> anthropic, and
    base.py itself pulls llm_client and analysis_schema (pydantic). None of those
    exist in the dependency-light CI guard job, where an ImportError here would
    take every other guard in the job down with it -- exactly what happened on
    2026-07-31. Stubbing the two heavy siblings is safe: `BaseGatherer` never
    touches them, they are used only by `BaseAnalyzer`.
    """
    if "agents" not in sys.modules:
        pkg = types.ModuleType("agents")
        pkg.__path__ = [str(REPO_ROOT / "agents")]
        sys.modules["agents"] = pkg

    if "agents.llm_client" not in sys.modules:
        stub = types.ModuleType("agents.llm_client")
        for name in ("AnthropicClient", "AsyncAnthropicClient", "ThinkingLevel", "LLMResponse"):
            setattr(stub, name, type(name, (), {}))
        sys.modules["agents.llm_client"] = stub

    if "agents.analysis_schema" not in sys.modules:
        stub = types.ModuleType("agents.analysis_schema")
        stub.sanitize_batch_result = lambda *a, **k: None
        stub.sanitize_ranking_result = lambda *a, **k: None
        sys.modules["agents.analysis_schema"] = stub

    return importlib.import_module("agents.base")


def _load_replay_generator():
    """Import the generator the same dependency-light way the guard tests do."""
    if "agents" not in sys.modules:
        pkg = types.ModuleType("agents")
        pkg.__path__ = [str(REPO_ROOT / "agents")]
        sys.modules["agents"] = pkg
    importlib.import_module("agents.cost_tracker")
    importlib.import_module("agents.replay_taxonomy")
    from generators.replay_generator import ReplayGenerator

    return ReplayGenerator


base = _load_base_module()
ReplayGenerator = _load_replay_generator()


class _Gatherer(base.BaseGatherer):
    """Minimal concrete gatherer; only the step bookkeeping is under test."""

    def __init__(self):
        super().__init__(config_dir=str(REPO_ROOT / "config"), data_dir="/tmp")

    @property
    def category(self) -> str:
        return "test"

    async def gather(self):
        return []


class TimeStepCaptureTests(unittest.TestCase):
    """What `time_step` records, and what it refuses to let break."""

    def test_records_span_and_item_count(self):
        g = _Gatherer()
        with g.time_step("reddit", "r/LocalLLaMA") as step:
            step.items = 83

        (entry,) = g.source_steps["reddit"]
        self.assertEqual(entry["name"], "r/LocalLLaMA")
        self.assertEqual(entry["items"], 83)
        self.assertEqual(entry["status"], "success")
        self.assertLessEqual(entry["started_at"], entry["ended_at"])

    def test_failed_unit_is_recorded_and_the_exception_still_propagates(self):
        """A feed that died at 4s is a fact worth drawing.

        Swallowing the error to keep the bar tidy would be strictly worse than the
        interpolation this replaces, and leaving the step unrecorded would make the
        bar run to the end of the phase and read as a hang.
        """
        g = _Gatherer()
        with self.assertRaises(RuntimeError):
            with g.time_step("news", "example.com"):
                raise RuntimeError("connection reset")

        (entry,) = g.source_steps["news"]
        self.assertEqual(entry["status"], "failed")

    def test_caller_can_mark_a_unit_that_returned_short(self):
        """arXiv's OAI leg returns a floor, not an answer, without raising."""
        g = _Gatherer()
        with g.time_step("research_arxiv", "OAI-PMH") as step:
            step.items = 0
            step.status = "partial"

        self.assertEqual(g.source_steps["research_arxiv"][0]["status"], "partial")

    def test_bookkeeping_failure_never_escapes(self):
        """Replay capture must never fail a run -- the recorder's first rule."""
        g = _Gatherer()
        g.source_steps = None  # force `_record_step` to raise internally

        with g.time_step("news", "example.com") as step:
            step.items = 1  # must not raise on exit

    def test_per_source_cap_is_enforced_and_counted(self):
        g = _Gatherer()
        for i in range(base.MAX_STEPS_PER_SOURCE + 7):
            with g.time_step("news", f"feed-{i}"):
                pass

        self.assertEqual(len(g.source_steps["news"]), base.MAX_STEPS_PER_SOURCE)
        self.assertEqual(g._source_steps_dropped["news"], 7)

    def test_steps_from_concurrent_threads_all_land(self):
        """RSS, Reddit, Bluesky and Mastodon all fan out across thread pools."""
        import threading

        g = _Gatherer()

        def unit(i):
            with g.time_step("news", f"feed-{i}") as step:
                step.items = i

        threads = [threading.Thread(target=unit, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(g.source_steps["news"]), 40)


class StepLabelTests(unittest.TestCase):
    """Step names are the one new free-text field entering the public index."""

    def test_url_is_reduced_to_its_host(self):
        self.assertEqual(
            base.step_label_for_url("https://www.techcrunch.com/feed/ai/latest.xml"),
            "techcrunch.com",
        )

    def test_the_2026_07_31_killer_url_survives_labelling(self):
        """The slug that destroyed a day's replay must not reach the index.

        `_assert_publishable` matched `ta|sk-orchestration-and-multi-robot-...`
        inside a DeepMind URL path and dropped the whole artifact. Labelling by
        host removes the path entirely, so the shape cannot recur.
        """
        label = base.step_label_for_url(
            "https://deepmind.google/blog/gemini-robotics-er-2-powering-robotics-"
            "with-video-understanding-task-orchestration-and-multi-robot-collaboration/"
        )
        self.assertEqual(label, "deepmind.google")
        self.assertNotIn("sk-", label)

    def test_credentials_in_a_url_never_survive_labelling(self):
        label = base.step_label_for_url("https://user:hunter2@feeds.example.com/rss")
        self.assertEqual(label, "feeds.example.com")
        self.assertNotIn("hunter2", label)

    def test_plain_labels_pass_through(self):
        self.assertEqual(base.step_label_for_url("cs.AI"), "cs.AI")
        self.assertEqual(base.step_label_for_url("r/LocalLLaMA"), "r/LocalLLaMA")


class SocialTimingKeyTests(unittest.TestCase):
    """The gatherer must time the keys the orchestrator actually publishes.

    `BuildSourcesWithStepsTests` proves the generator does the right thing *given*
    prefixed keys, but it cannot see the gatherer. The original bug lived entirely
    on this side: `time_source('twitter')` against a row published as
    `social_twitter`. Read the source, because importing the social gatherer pulls
    requests/feedparser and this has to stay in the dependency-light guard job.
    """

    SOURCE = (REPO_ROOT / "agents" / "gatherers" / "social_gatherer.py").read_text()

    def test_every_timing_key_is_social_prefixed(self):
        import re

        keys = re.findall(r"time_(?:source|step)\(\s*(f?['\"][^'\"]*)", self.SOURCE)
        self.assertTrue(keys, "no timing calls found -- did the file move?")
        for key in keys:
            self.assertIn(
                "social_",
                key,
                f"{key} is not the key the orchestrator publishes; this is the "
                "2026-08-17 mismatch that made all three social bars identical",
            )

    def test_all_three_platforms_are_stepped(self):
        for platform in ("social_twitter", "social_bluesky", "social_mastodon"):
            self.assertIn(f"time_step('{platform}'", self.SOURCE)

    def test_platform_fetch_failures_are_not_swallowed(self):
        """A step marked `failed` inside a row marked `success` contradicts itself.

        Both per-account handlers used to absorb every transport error and return
        an empty list, so `failed_handles` / `failed_accounts` could never fire and
        the platform reported clean no matter how many accounts were dead.
        """
        for helper in ("Error fetching Bluesky @", "Error fetching Mastodon @"):
            idx = self.SOURCE.index(helper)
            tail = self.SOURCE[idx : idx + 400]
            self.assertRegex(
                tail,
                r"\n\s+raise\b",
                f"the handler logging {helper!r} swallows the failure again",
            )


class BuildStepsTests(unittest.TestCase):
    """Epoch -> ms conversion, and the refusal to invent a measurement."""

    T0 = 1_000_000.0

    def _steps(self, raw, t0=None):
        return ReplayGenerator._build_steps(raw, self.T0 if t0 is None else t0)

    def test_epochs_become_ms_offsets_from_t0(self):
        out = self._steps(
            [{"name": "r/agi", "started_at": self.T0 + 0.142, "ended_at": self.T0 + 21.033, "items": 5}]
        )
        self.assertEqual(out[0]["start_ms"], 142)
        self.assertEqual(out[0]["end_ms"], 21033)
        self.assertEqual(out[0]["items"], 5)

    def test_pre_origin_steps_are_dropped_not_clamped(self):
        """A resumed run replays gathering under an earlier `t0`.

        Clamping those to 0 would present a checkpointed span as though it had been
        clocked in this run. Dropping says nothing, which is the honest answer.
        """
        out = self._steps(
            [
                {"name": "stale", "started_at": self.T0 - 60, "ended_at": self.T0 - 30},
                {"name": "fresh", "started_at": self.T0 + 1, "ended_at": self.T0 + 2},
            ]
        )
        self.assertEqual([s["name"] for s in out], ["fresh"])

    def test_inverted_and_malformed_spans_are_dropped(self):
        out = self._steps(
            [
                {"name": "backwards", "started_at": self.T0 + 9, "ended_at": self.T0 + 4},
                {"name": "no-end", "started_at": self.T0 + 1},
                {"name": "", "started_at": self.T0 + 1, "ended_at": self.T0 + 2},
                {"started_at": self.T0 + 1, "ended_at": self.T0 + 2},
                "not-a-dict",
            ]
        )
        self.assertEqual(out, [])

    def test_steps_are_sorted_by_completion(self):
        """The frontend counts completions as a forward scan, so end order rules.

        A thread pool finishes out of dispatch order; sorting by start would make
        the step function non-monotonic and the bar would appear to go backwards.
        """
        out = self._steps(
            [
                {"name": "slow", "started_at": self.T0, "ended_at": self.T0 + 90},
                {"name": "fast", "started_at": self.T0 + 1, "ended_at": self.T0 + 3},
                {"name": "middle", "started_at": self.T0 + 2, "ended_at": self.T0 + 40},
            ]
        )
        self.assertEqual([s["name"] for s in out], ["fast", "middle", "slow"])
        self.assertEqual(out, sorted(out, key=lambda s: s["end_ms"]))

    def test_absent_t0_yields_nothing(self):
        raw = [{"name": "x", "started_at": self.T0, "ended_at": self.T0 + 1}]
        self.assertEqual(ReplayGenerator._build_steps(raw, None), [])

    def test_non_list_input_is_tolerated(self):
        for junk in (None, {}, "steps", 7):
            self.assertEqual(self._steps(junk), [])


class BuildSourcesWithStepsTests(unittest.TestCase):
    """How steps ride along on the published `sources` rows."""

    T0 = 2_000_000.0
    PHASES = [{"ordinal": "1", "start_ms": 100, "end_ms": 95_000}]

    def _sources(self, status):
        return {
            s["name"]: s
            for s in ReplayGenerator._build_sources(status, self.PHASES, self.T0)
        }

    def test_social_platforms_are_measured_independently(self):
        """The regression that motivated all of this.

        Timing must arrive on the `social_`-prefixed keys the orchestrator
        publishes. If a future change re-introduces the bare-name mismatch, these
        rows silently revert to `timing_measured: false` with identical spans --
        which is exactly what shipped for weeks without anyone noticing.
        """
        status = {
            "social_twitter": {
                "status": "success", "count": 264,
                "started_at": self.T0 + 0.1, "ended_at": self.T0 + 80.0,
            },
            "social_bluesky": {
                "status": "success", "count": 8,
                "started_at": self.T0 + 0.1, "ended_at": self.T0 + 12.0,
            },
            "social_mastodon": {
                "status": "success", "count": 0,
                "started_at": self.T0 + 0.1, "ended_at": self.T0 + 4.0,
            },
        }
        rows = self._sources(status)

        for name in ("Twitter", "Bluesky", "Mastodon"):
            self.assertTrue(rows[name]["timing_measured"], f"{name} fell back to the phase span")

        ends = {rows[n]["end_ms"] for n in ("Twitter", "Bluesky", "Mastodon")}
        self.assertEqual(len(ends), 3, "all three social bars still finish together")

    def test_bare_platform_keys_are_not_published(self):
        """The orphan rows the old mismatch created must stay unmapped."""
        rows = self._sources(
            {"twitter": {"status": "success", "count": 0,
                         "started_at": self.T0, "ended_at": self.T0 + 1}}
        )
        self.assertEqual(rows, {})

    def test_steps_attach_to_their_row(self):
        status = {
            "reddit": {
                "status": "success", "count": 509,
                "started_at": self.T0, "ended_at": self.T0 + 81.0,
                "steps": [
                    {"name": "r/LocalLLaMA", "started_at": self.T0 + 0.1,
                     "ended_at": self.T0 + 21.0, "items": 83, "status": "success"},
                    {"name": "r/agi", "started_at": self.T0 + 0.1,
                     "ended_at": self.T0 + 6.0, "items": 5, "status": "success"},
                ],
            }
        }
        row = self._sources(status)["Reddit"]
        self.assertEqual([s["name"] for s in row["steps"]], ["r/agi", "r/LocalLLaMA"])
        self.assertNotIn("steps_dropped", row)

    def test_dropped_steps_are_reported(self):
        """A short bar must read as truncated, never as complete."""
        status = {
            "reddit": {
                "status": "success", "count": 10,
                "started_at": self.T0, "ended_at": self.T0 + 10.0,
                "steps_dropped": 3,
                "steps": [
                    {"name": "kept", "started_at": self.T0, "ended_at": self.T0 + 1},
                    # Pre-origin: dropped here, and counted on top of the capture cap.
                    {"name": "stale", "started_at": self.T0 - 5, "ended_at": self.T0 - 4},
                ],
            }
        }
        row = self._sources(status)["Reddit"]
        self.assertEqual(len(row["steps"]), 1)
        self.assertEqual(row["steps_dropped"], 4)

    def test_rows_without_steps_are_unchanged(self):
        """Every day published before this shipped, and any un-instrumented source."""
        row = self._sources(
            {"news": {"status": "success", "count": 20,
                      "started_at": self.T0, "ended_at": self.T0 + 4.0}}
        )["RSS feeds"]
        self.assertNotIn("steps", row)
        self.assertTrue(row["timing_measured"])

    def test_step_names_pass_the_publish_gate(self):
        """Real feed hosts must survive `_assert_publishable`.

        Step names are the first gatherer-authored free text in the index; the gate
        has already cost this project one full day's replay.
        """
        hosts = [
            base.step_label_for_url(u)
            for u in (
                "https://deepmind.google/blog/task-orchestration-and-multi-robot/",
                "https://www.sk-hynix.com/feed",
                "https://openai.com/index/rss.xml",
            )
        ]
        artifact = {
            "sources": [{
                "name": "RSS feeds",
                "steps": [{"name": h, "start_ms": 0, "end_ms": 1, "items": 0,
                           "status": "success"} for h in hosts],
            }]
        }
        ReplayGenerator._assert_publishable(artifact, "replay-index.json")


if __name__ == "__main__":
    unittest.main()
