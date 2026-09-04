"""A degraded Phase 4.5 must be repairable on its own.

What happened
-------------
2026-09-04: the published report lost internal links on the executive summary
and three of the four category summaries. The links were the only thing wrong
with the day -- the summaries themselves, the topics and the hero image were
all fine -- but there was no way to re-run Phase 4.5 alone. ``--resume-from
4.5`` fell into the ``resume_from > 4`` else-branch, which re-generated the
executive summary (Phase 4), re-asked the enricher for texts that already
carried links, and generated a second hero image. Repairing the links meant
paying for, and replacing, content that was already correct.

Locks in the repair path:

  1. ``--resume-from 4.5`` keeps the checkpointed executive summary. Phase 4
     does not re-run; the phase is registered as restored, not as a fresh
     success.
  2. Only texts with no internal link are sent to the enricher. A summary or
     topic description that already contains ``INTERNAL_LINK_MARKER`` comes
     back byte-identical and costs nothing -- and skipping it is NOT a
     degradation.
  3. The hero image is reused from its own checkpoint. Phase 4.7 saves one
     now precisely so the repair has something to restore; regenerating is a
     paid image call for an image the site is already serving.
  4. ``_detect_resume_point`` distinguishes "died after the summary" (4.6, so
     the hero still gets generated) from "died after the hero" (5.0). Before
     the ``hero`` checkpoint existed, ``summary.json`` alone resumed at 5.0
     and the day published with no hero at all.

Two more, from the 2026-09-04 review of that repair path:

  5. Re-saving a checkpoint must never NARROW its replay bundle. The repair
     rewrites summary.json, and its own recorder holds only the two or three
     enrichment calls it made; without folding the absorbed bundle back in, the
     next resume of that date restores a stunted bundle and publishes a replay
     with no gatherers and no analyzers -- the 2026-08-24 regression the bundle
     mechanism exists to prevent.
  6. ``--resume-from 4.7`` names Phase 4.7 and therefore RE-RUNS it. Reusing the
     checkpointed hero at every resume point past Phase 4 would have left no
     resume point at all that regenerates a hero once hero.json exists.

Stdlib-only unittest (no network, no LLM):

  python3 -m unittest tests.enrichment_repair_resume_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import asdict  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from unittest import mock  # noqa: E402

import agents.orchestrator as orchestrator_module  # noqa: E402
from agents.base import AnalyzedItem, CategoryReport, CollectedItem  # noqa: E402
from agents.cost_tracker import get_tracker, reset_tracker  # noqa: E402
from agents.link_enricher import INTERNAL_LINK_MARKER, LinkEnricher  # noqa: E402
from agents.orchestrator import MainOrchestrator, TopTopic  # noqa: E402
from agents.replay_recorder import get_recorder, reset_recorder  # noqa: E402

# Restore/repair paths log at INFO, and the non-fatal branches log warnings.
# Silence them at import so they don't leak into CI output through logging's
# lastResort handler.
for _noisy in ("agents.orchestrator", "agents.link_enricher",
               "agents.staleness_checker", "agents.cost_tracker"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

DATE = "2026-09-04"
NEWS_ITEM_ID = "aaa111bbb222"
RESEARCH_ITEM_ID = "ccc333ddd444"
NEWS_LINK = f"/?date={DATE}&category=news#item-{NEWS_ITEM_ID}"
RESEARCH_LINK = f"/?date={DATE}&category=research#item-{RESEARCH_ITEM_ID}"

# What the 2026-09-04 checkpoint looked like: some texts enriched, some not.
EXEC_UNLINKED = "Two labs shipped agent frameworks and a preprint disputed the benchmark."
NEWS_SUMMARY_UNLINKED = "A lab shipped an agent framework on Wednesday afternoon."
RESEARCH_SUMMARY_LINKED = (
    f"A preprint [disputed the benchmark]({RESEARCH_LINK}) the same day."
)
TOPIC_LINKED_NAME = "Benchmark dispute"
TOPIC_LINKED_DESC = f"The [benchmark dispute]({RESEARCH_LINK}) ran all day."
TOPIC_UNLINKED_NAME = "Agent frameworks"
TOPIC_UNLINKED_DESC = "Two agent frameworks shipped on the same afternoon."

HERO_URL = f"/data/{DATE}/hero.webp?v=1"
HERO_PROMPT = "A skunk reviewing two agent frameworks on a circuit-board bench."

REGEN_HERO_URL = f"/data/{DATE}/hero.webp"
REGEN_HERO_PROMPT = "A freshly drawn skunk."

# The replay bundle the ORIGINAL run left on summary.json: one analyzer span and
# its cost row. Nothing in the repair run can produce these again -- the
# recorder is memory-only and died with that process -- so they exist only in
# the bundle, and re-saving the checkpoint is the only thing that can lose them.
ORIGINAL_T0 = 1_000.0
ORIGINAL_SPAN = {
    "id": "c001",  # deliberately collides: span ids restart at c001 each process
    "caller": "news_analyzer.batch_1",
    "queued_ms": 500,
    "start_ms": 600,
    "first_token_ms": 900,
    "end_ms": 4_600,
    "wait_ms": 100,
    "outcome": "ok",
    "context": {"caller": "news_analyzer.batch_1"},
    "deltas": {"t": [], "kind": [], "text": []},
}
ORIGINAL_COST_ROW = {
    "timestamp": "2026-09-04T03:00:04.123456",
    "caller": "news_analyzer.batch_1",
    "thinking_level": "STANDARD",
    "input_tokens": 1200,
    "output_tokens": 800,
    "cache_creation_tokens": 0,
    "cache_read_tokens": 0,
    "model": "z-ai/GLM-5.3-Flash",
    "provider_id": "openrouter",
    "analysis_profile": "STANDARD",
    "adaptive_effort": "xhigh",
    "duration_seconds": 4.0,
    "partial": False,
}
ORIGINAL_REPLAY = {
    "t0_epoch": ORIGINAL_T0,
    "spans": [ORIGINAL_SPAN],
    "cost_calls": [ORIGINAL_COST_ROW],
}

TEXT_FENCE_START = "=== TEXT TO ENRICH ==="
TEXT_FENCE_END = "=== END TEXT TO ENRICH ==="

# Windows the original run measured, so the restored phases are 'success'
# records with real durations rather than zero-width skips.
SUMMARY_TIMINGS = {
    "Phase 4: Executive Summary": {
        "start_time": 1_000.0, "end_time": 1_090.0, "status": "success"},
    "Phase 4.5: Link Enrichment": {
        "start_time": 1_090.0, "end_time": 1_150.0, "status": "partial"},
}
HERO_TIMINGS = {
    "Phase 4.7: Hero Image": {
        "start_time": 1_160.0, "end_time": 1_220.0, "status": "success"},
}


def _fenced_text(user_message: str) -> str:
    """Pull the text-to-enrich back out of the enricher's fenced user message."""
    start = user_message.index(TEXT_FENCE_START) + len(TEXT_FENCE_START)
    end = user_message.index(TEXT_FENCE_END)
    return user_message[start:end].strip("\n")


class _FakeEnrichClient:
    """Enriches whatever text it is handed, and records who asked."""

    def __init__(self):
        self.callers = []
        self.texts = []

    async def call_with_thinking(self, **kwargs):
        caller = kwargs.get('caller')
        self.callers.append(caller)
        text = _fenced_text(kwargs['messages'][0]['content'])
        self.texts.append(text)
        payload = json.dumps({
            "enriched_text": f"{text} [read more]({NEWS_LINK})",
            "links": [{"phrase": "read more", "item_id": NEWS_ITEM_ID,
                       "category": "news"}],
        })
        response = SimpleNamespace(content=payload, stop_reason="end_turn")
        # Leave the same traces a real client leaves, so the checkpoint the
        # repair writes carries this process's own calls as well as the
        # restored ones.
        recorder = get_recorder()
        call_id = recorder.start_call(None, {"caller": caller})
        recorder.finish_call(call_id, response=SimpleNamespace(
            stop_reason="end_turn", usage=None))
        get_tracker().record_call(
            caller=caller, usage={"input_tokens": 40, "output_tokens": 20},
            duration_seconds=0.01)
        return response


class _HeroMustNotRegenerate:
    """A hero generator that fails the test if the repair path calls it."""

    def __init__(self):
        self.called = False

    async def generate(self, **kwargs):
        self.called = True
        raise AssertionError("hero must not regenerate")


class _HeroRecorder:
    """A hero generator that records the call and returns a plausible result."""

    def __init__(self):
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"path": REGEN_HERO_URL, "prompt": REGEN_HERO_PROMPT,
                "usage": {"image_tokens": 1290}}


class _EcosystemStub:
    async def initialize(self, report_date):
        return ""

    async def enrich_from_news(self, news_items, async_client):
        return {"updates_made": 0}


def _collected(item_id: str, title: str, source_type: str) -> CollectedItem:
    return CollectedItem(
        id=item_id, title=title, content="", url=f"https://example.test/{item_id}",
        author="", published=f"{DATE}T09:00:00", source="example",
        source_type=source_type,
    )


def _analyzed(item: CollectedItem) -> AnalyzedItem:
    return AnalyzedItem(
        item=item, summary=f"{item.title}.", importance_score=80.0,
        reasoning="", themes=[],
    )


def _report(category: str, item: CollectedItem, summary: str) -> CategoryReport:
    analyzed = _analyzed(item)
    return CategoryReport(
        category=category, top_items=[analyzed], all_items=[analyzed],
        category_summary=summary, themes=[], cross_signals=[], total_collected=1,
    )


def _topics():
    return [
        TopTopic(name=TOPIC_LINKED_NAME, description=TOPIC_LINKED_DESC,
                 description_html="", category_breakdown={"research": 1},
                 representative_items=[], importance=90),
        TopTopic(name=TOPIC_UNLINKED_NAME, description=TOPIC_UNLINKED_DESC,
                 description_html="", category_breakdown={"news": 1},
                 representative_items=[], importance=80),
    ]


def _write_checkpoints(data_dir: str):
    """Lay down the checkpoints a completed 2026-09-04 run would have left."""
    ckpt_dir = os.path.join(data_dir, "checkpoints", DATE)
    os.makedirs(ckpt_dir, exist_ok=True)

    news_item = _collected(NEWS_ITEM_ID, "A lab ships an agent framework", "rss")
    research_item = _collected(RESEARCH_ITEM_ID, "Preprint disputes a benchmark", "arxiv")

    payloads = {
        "gathering.json": {
            "collection_status": {
                "news": {"status": "success", "items": 1},
                "research": {"status": "success", "items": 1},
            },
            "categories": {
                "news": [news_item.to_dict()],
                "research": [research_item.to_dict()],
            },
        },
        "analysis.json": {
            "category_reports": {
                "news": _report("news", news_item, NEWS_SUMMARY_UNLINKED).to_dict(),
                "research": _report(
                    "research", research_item, RESEARCH_SUMMARY_LINKED).to_dict(),
            },
        },
        "topics.json": {
            "top_topics": [asdict(t) for t in _topics()],
            "thinking": "topic thinking",
        },
        "summary.json": {
            "executive_summary": EXEC_UNLINKED,
            "thinking": "summary thinking",
            "enriched_category_summaries": {
                "news": NEWS_SUMMARY_UNLINKED,
                "research": RESEARCH_SUMMARY_LINKED,
            },
            "enriched_topics": [asdict(t) for t in _topics()],
            "_phase_timings": SUMMARY_TIMINGS,
            "_replay": ORIGINAL_REPLAY,
        },
        "hero.json": {
            "hero_image_url": HERO_URL,
            "hero_image_prompt": HERO_PROMPT,
            "hero_image_usage": None,
            "_phase_timings": HERO_TIMINGS,
        },
    }
    for name, payload in payloads.items():
        with open(os.path.join(ckpt_dir, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)


def _build_orchestrator(data_dir, web_dir, config_dir, client, hero_generator):
    """A MainOrchestrator with exactly the attributes run() touches."""
    orch = MainOrchestrator.__new__(MainOrchestrator)
    orch.target_date = DATE
    orch.config_dir = config_dir
    orch.data_dir = data_dir
    orch.web_dir = web_dir
    orch.provider_config = None
    orch.prompt_accessor = None
    orch.grounding_context = None
    orch.ecosystem_manager = _EcosystemStub()
    orch.gatherers = {"news": SimpleNamespace(
        coverage_date="2026-09-03", start_time=None, end_time=None)}
    orch.hero_generator = hero_generator
    orch.async_client = client
    orch.degradations = []
    orch._restored_replay = None
    return orch


def _tmp_pipeline_dirs(case):
    """A throwaway data/web/config trio pre-loaded with the day's checkpoints."""
    tmp = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, tmp, True)
    dirs = [os.path.join(tmp, name) for name in ("data", "web", "config")]
    for path in dirs:
        os.makedirs(path, exist_ok=True)
    _write_checkpoints(dirs[0])
    return dirs


def _phase(result, name):
    for record in result.phase_status:
        if record["name"] == name:
            return record
    raise AssertionError(f"phase {name!r} missing from {[p['name'] for p in result.phase_status]}")


class EnrichmentRepairResumeTest(unittest.TestCase):
    """`--resume-from 4.5` end to end, from checkpoints on disk."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data_dir = os.path.join(cls.tmp, "data")
        cls.web_dir = os.path.join(cls.tmp, "web")
        cls.config_dir = os.path.join(cls.tmp, "config")
        for path in (cls.data_dir, cls.web_dir, cls.config_dir):
            os.makedirs(path, exist_ok=True)
        _write_checkpoints(cls.data_dir)

        cls.client = _FakeEnrichClient()
        cls.hero = _HeroMustNotRegenerate()

        orch = _build_orchestrator(
            cls.data_dir, cls.web_dir, cls.config_dir, cls.client, cls.hero)

        # A live recorder, so the repair's own spans exist to be merged with the
        # restored ones. Forced on: an environment with capture disabled would
        # otherwise make the merge assertions vacuous.
        with mock.patch.dict(os.environ, {"LLM_REPLAY_CAPTURE": "true"}):
            reset_recorder()

        # Phase 4 re-running is the whole failure this mode exists to prevent.
        original_generate = MainOrchestrator._generate_executive_summary

        async def _must_not_run(self, *args, **kwargs):
            raise AssertionError("Phase 4 must not re-run")

        MainOrchestrator._generate_executive_summary = _must_not_run
        try:
            # run() prints the phase and cost summaries; keep the test output clean.
            with contextlib.redirect_stdout(io.StringIO()):
                cls.result = asyncio.run(orch.run(resume_from=4.5))
        finally:
            MainOrchestrator._generate_executive_summary = original_generate

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        # The recorder and tracker are process globals; hand them back clean.
        reset_recorder()
        reset_tracker()

    def _saved_summary(self):
        path = os.path.join(self.data_dir, "checkpoints", DATE, "summary.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_unlinked_texts_are_re_enriched(self):
        self.assertIn("link_enricher.executive summary", self.client.callers)
        self.assertIn("link_enricher.news summary", self.client.callers)
        self.assertIn(f"link_enricher.topic: {TOPIC_UNLINKED_NAME}", self.client.callers)

        self.assertIn(INTERNAL_LINK_MARKER, self.result.executive_summary)
        self.assertIn(
            INTERNAL_LINK_MARKER,
            self.result.category_reports["news"].category_summary)
        unlinked_topic = next(
            t for t in self.result.top_topics if t.name == TOPIC_UNLINKED_NAME)
        self.assertIn(INTERNAL_LINK_MARKER, unlinked_topic.description)
        # The checkpointed prose is kept, not regenerated -- only links are added.
        self.assertTrue(self.result.executive_summary.startswith(EXEC_UNLINKED))

    def test_already_linked_texts_are_untouched_and_never_asked_for(self):
        self.assertNotIn("link_enricher.research summary", self.client.callers)
        self.assertNotIn(
            f"link_enricher.topic: {TOPIC_LINKED_NAME}", self.client.callers)

        self.assertEqual(
            self.result.category_reports["research"].category_summary,
            RESEARCH_SUMMARY_LINKED,
            "an already-linked summary must survive the repair byte-identical")
        linked_topic = next(
            t for t in self.result.top_topics if t.name == TOPIC_LINKED_NAME)
        self.assertEqual(linked_topic.description, TOPIC_LINKED_DESC)

    def test_exactly_the_unlinked_texts_reached_the_model(self):
        self.assertEqual(len(self.client.callers), 3, self.client.callers)
        self.assertEqual(
            sorted(self.client.texts),
            sorted([EXEC_UNLINKED, NEWS_SUMMARY_UNLINKED, TOPIC_UNLINKED_DESC]))

    def test_hero_is_reused_from_its_checkpoint(self):
        self.assertFalse(self.hero.called, "the repair must not pay for a second hero")
        self.assertEqual(self.result.hero_image_url, HERO_URL)
        self.assertEqual(self.result.hero_image_prompt, HERO_PROMPT)
        self.assertIsNone(self.result.hero_image_usage)

    def test_phase_records_distinguish_restored_from_re_run(self):
        summary_phase = _phase(self.result, "Phase 4: Executive Summary")
        self.assertEqual(summary_phase["status"], "success")
        self.assertEqual(summary_phase["details"], "loaded from checkpoint (enrichment repair)")
        self.assertAlmostEqual(summary_phase["duration"], 90.0, places=3)

        hero_phase = _phase(self.result, "Phase 4.7: Hero Image")
        self.assertEqual(hero_phase["status"], "success")
        self.assertEqual(hero_phase["details"], "loaded from checkpoint")
        self.assertAlmostEqual(hero_phase["duration"], 60.0, places=3)

        enrichment_phase = _phase(self.result, "Phase 4.5: Link Enrichment")
        self.assertEqual(enrichment_phase["status"], "success")
        self.assertIsNone(enrichment_phase["details"])
        self.assertEqual(self.result.degradations, [])

    def test_summary_checkpoint_is_rewritten_with_the_repaired_text(self):
        saved = self._saved_summary()
        self.assertIn(INTERNAL_LINK_MARKER, saved["executive_summary"])
        self.assertIn(
            INTERNAL_LINK_MARKER, saved["enriched_category_summaries"]["news"])
        self.assertEqual(
            saved["enriched_category_summaries"]["research"], RESEARCH_SUMMARY_LINKED)
        self.assertEqual(saved["thinking"], "summary thinking")

    def test_the_original_runs_agents_survive_the_rewritten_checkpoint(self):
        """The whole point: re-saving must widen the bundle, never narrow it.

        The repair's own recorder holds three enrichment calls and nothing else.
        If the saved bundle were just that snapshot, the next --resume of this
        date would restore it and publish a replay with no analyzers at all.
        """
        bundle = self._saved_summary()["_replay"]

        callers = [span.get("caller") for span in bundle["spans"]]
        self.assertIn("news_analyzer.batch_1", callers,
                      "the original run's analyzer span was dropped")
        for caller in ("link_enricher.executive summary",
                       "link_enricher.news summary",
                       f"link_enricher.topic: {TOPIC_UNLINKED_NAME}"):
            self.assertIn(caller, callers, "the repair's own calls are missing")

        row_callers = [row.get("caller") for row in bundle["cost_calls"]]
        self.assertIn("news_analyzer.batch_1", row_callers)
        self.assertEqual(row_callers.count("news_analyzer.batch_1"), 1)
        self.assertIn(ORIGINAL_COST_ROW, bundle["cost_calls"],
                      "a restored cost row must survive byte-identical")
        self.assertEqual(
            sum(1 for c in row_callers if c.startswith("link_enricher.")), 3)

    def test_the_rewritten_bundle_has_no_duplicate_span_ids(self):
        """Span ids restart at c001 per process, so a merge must re-key.

        The generator keys the stream artifact by span id (a dict), so a
        collision silently drops one call's typewriter text and emits two
        calls with the same id in the index.
        """
        spans = self._saved_summary()["_replay"]["spans"]
        ids = [span.get("id") for span in spans]
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertEqual(len(spans), 4, "one restored span plus three repair spans")

    def test_restored_span_offsets_still_name_their_original_instant(self):
        """A bundle carries ONE t0_epoch, and the generator rebases against it.

        Keeping this process's origin means the restored offsets must move with
        it; what has to survive is the absolute instant, not the number.
        """
        bundle = self._saved_summary()["_replay"]
        restored = next(s for s in bundle["spans"]
                        if s.get("caller") == "news_analyzer.batch_1")
        for field in ("queued_ms", "start_ms", "first_token_ms", "end_ms"):
            self.assertAlmostEqual(
                bundle["t0_epoch"] + restored[field] / 1000.0,
                ORIGINAL_T0 + ORIGINAL_SPAN[field] / 1000.0,
                places=3, msg=field)


_KEEP = object()  # sentinel: "use the default", so None can mean "no origin"


class _StubRecorder:
    def __init__(self, t0_epoch, calls):
        self._snapshot = {"t0_epoch": t0_epoch, "calls": calls}

    def snapshot(self):
        return self._snapshot


class _StubTracker:
    def __init__(self, calls):
        self._calls = calls

    def get_json_report(self):
        return {"calls": self._calls}


class ExportReplayBundleTest(unittest.TestCase):
    """`_export_replay_bundle` on its own: cumulative, de-duplicated, re-keyed."""

    LIVE_T0 = 5_000.0
    LIVE_SPAN = {
        "id": "c001", "caller": "link_enricher.executive summary",
        "queued_ms": 10, "start_ms": 12, "first_token_ms": 30, "end_ms": 900,
        "outcome": "ok", "deltas": {"t": [], "kind": [], "text": []},
    }
    LIVE_ROW = {
        "timestamp": "2026-09-04T09:15:00.000001",
        "caller": "link_enricher.executive summary",
        "model": "z-ai/GLM-5.3-Flash", "input_tokens": 40, "output_tokens": 20,
    }

    def _export(self, restored, live_spans=None, live_rows=None, live_t0=_KEEP):
        orch = MainOrchestrator.__new__(MainOrchestrator)
        orch._restored_replay = restored
        recorder = _StubRecorder(
            self.LIVE_T0 if live_t0 is _KEEP else live_t0,
            [dict(s) for s in (live_spans if live_spans is not None else [self.LIVE_SPAN])])
        tracker = _StubTracker(
            [dict(r) for r in (live_rows if live_rows is not None else [self.LIVE_ROW])])
        with mock.patch.object(orchestrator_module, "get_recorder", lambda: recorder), \
                mock.patch.object(orchestrator_module, "get_tracker", lambda: tracker):
            return orch._export_replay_bundle()

    def test_a_fresh_run_exports_only_its_own_snapshot(self):
        bundle = self._export(None)
        self.assertEqual(bundle["t0_epoch"], self.LIVE_T0)
        self.assertEqual(bundle["spans"], [self.LIVE_SPAN])
        self.assertEqual(bundle["cost_calls"], [self.LIVE_ROW])

    def test_a_restored_bundle_is_folded_back_in(self):
        bundle = self._export(ORIGINAL_REPLAY)
        self.assertEqual(
            [s["caller"] for s in bundle["spans"]],
            ["news_analyzer.batch_1", "link_enricher.executive summary"],
            "restored calls come first: they happened first")
        self.assertEqual(bundle["cost_calls"], [ORIGINAL_COST_ROW, self.LIVE_ROW])
        # The bundle keeps ONE origin, and it is this process's.
        self.assertEqual(bundle["t0_epoch"], self.LIVE_T0)

    def test_a_colliding_span_id_is_re_keyed_not_dropped(self):
        """Both spans are `c001`, and they are different calls.

        De-duplicating by id alone would delete the very history the merge
        exists to keep.
        """
        bundle = self._export(ORIGINAL_REPLAY)
        ids = [s["id"] for s in bundle["spans"]]
        self.assertEqual(len(ids), len(set(ids)), ids)
        restored = next(s for s in bundle["spans"]
                        if s["caller"] == "news_analyzer.batch_1")
        self.assertNotEqual(restored["id"], "c001")
        self.assertEqual(
            next(s for s in bundle["spans"]
                 if s["caller"] == "link_enricher.executive summary")["id"],
            "c001", "the live span keeps the id the recorder assigned it")

    def test_restored_offsets_are_rebased_onto_this_runs_origin(self):
        bundle = self._export(ORIGINAL_REPLAY)
        restored = next(s for s in bundle["spans"]
                        if s["caller"] == "news_analyzer.batch_1")
        for field in ("queued_ms", "start_ms", "first_token_ms", "end_ms"):
            self.assertAlmostEqual(
                bundle["t0_epoch"] + restored[field] / 1000.0,
                ORIGINAL_T0 + ORIGINAL_SPAN[field] / 1000.0,
                places=3, msg=field)

    def test_the_same_call_is_never_duplicated(self):
        """A bundle absorbed twice must not double every agent on the stage."""
        restored = {
            "t0_epoch": self.LIVE_T0,
            "spans": [dict(self.LIVE_SPAN), ORIGINAL_SPAN],
            "cost_calls": [dict(self.LIVE_ROW), ORIGINAL_COST_ROW],
        }
        bundle = self._export(restored)
        self.assertEqual(
            [s["caller"] for s in bundle["spans"]],
            ["news_analyzer.batch_1", "link_enricher.executive summary"])
        self.assertEqual(bundle["cost_calls"], [ORIGINAL_COST_ROW, self.LIVE_ROW])

    def test_a_process_that_recorded_nothing_keeps_the_restored_frame(self):
        bundle = self._export(ORIGINAL_REPLAY, live_spans=[], live_rows=[],
                              live_t0=None)
        self.assertEqual(bundle["t0_epoch"], ORIGINAL_T0)
        self.assertEqual(bundle["spans"], [ORIGINAL_SPAN])
        self.assertEqual(bundle["cost_calls"], [ORIGINAL_COST_ROW])


class HeroResumePointTest(unittest.TestCase):
    """Which resume points reuse the checkpointed hero, and which regenerate.

    `--resume-from 4.7` names Phase 4.7, so it must RUN Phase 4.7. Restoring the
    checkpoint there too would leave no resume point at all that regenerates a
    hero once hero.json exists -- the opposite of what the flag's help text says.
    """

    def _run(self, resume_from, hero_generator):
        data_dir, web_dir, config_dir = _tmp_pipeline_dirs(self)
        client = _FakeEnrichClient()
        orch = _build_orchestrator(
            data_dir, web_dir, config_dir, client, hero_generator)
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(orch.run(resume_from=resume_from))

    def test_4_7_regenerates_the_hero(self):
        hero = _HeroRecorder()
        result = self._run(4.7, hero)
        self.assertEqual(len(hero.calls), 1, "--resume-from 4.7 must run Phase 4.7")
        self.assertEqual(result.hero_image_url, REGEN_HERO_URL)
        self.assertEqual(result.hero_image_prompt, REGEN_HERO_PROMPT)
        self.assertEqual(result.hero_image_usage, {"image_tokens": 1290})
        self.assertEqual(_phase(result, "Phase 4.7: Hero Image")["status"], "success")

    def test_4_6_reuses_the_checkpointed_hero(self):
        hero = _HeroMustNotRegenerate()
        result = self._run(4.6, hero)
        self.assertFalse(hero.called, "4.6 must not pay for a second hero")
        self.assertEqual(result.hero_image_url, HERO_URL)
        self.assertEqual(result.hero_image_prompt, HERO_PROMPT)
        self.assertIsNone(result.hero_image_usage)
        hero_phase = _phase(result, "Phase 4.7: Hero Image")
        self.assertEqual(hero_phase["details"], "loaded from checkpoint")


class OnlyUnlinkedEnrichmentTest(unittest.TestCase):
    """`enrich_all(only_unlinked=True)` on its own."""

    def _run(self, only_unlinked):
        client = _FakeEnrichClient()
        enricher = LinkEnricher(client, DATE)
        category_reports = {
            "research": {
                "category_summary": RESEARCH_SUMMARY_LINKED,
                "all_items": [{
                    "id": RESEARCH_ITEM_ID,
                    "title": "Preprint disputes a benchmark",
                    "summary": "Posted the same day.",
                }],
            },
        }
        topics = _topics()
        exec_summary, categories, topics = asyncio.run(enricher.enrich_all(
            EXEC_UNLINKED, category_reports, topics, only_unlinked=only_unlinked))
        return client, enricher, exec_summary, categories, topics

    def test_linked_texts_are_skipped_and_are_not_degradations(self):
        client, enricher, exec_summary, categories, topics = self._run(True)

        self.assertEqual(
            sorted(client.callers),
            sorted(["link_enricher.executive summary",
                    f"link_enricher.topic: {TOPIC_UNLINKED_NAME}"]))
        self.assertNotIn("research", categories,
                         "a skipped summary must not be reported as re-enriched")
        self.assertIn(INTERNAL_LINK_MARKER, exec_summary)
        self.assertEqual(topics[0].description, TOPIC_LINKED_DESC)
        self.assertIn(INTERNAL_LINK_MARKER, topics[1].description)
        self.assertEqual(
            enricher.degradations, [],
            "skipping an already-linked text is a no-op, not a lost enrichment")

    def test_default_mode_still_enriches_everything(self):
        client, enricher, _exec, categories, _topics_out = self._run(False)

        self.assertEqual(len(client.callers), 4)
        self.assertIn("link_enricher.research summary", client.callers)
        self.assertIn(INTERNAL_LINK_MARKER, categories["research"])
        self.assertEqual(enricher.degradations, [])


class DetectResumePointTest(unittest.TestCase):
    """A run that died before the hero must still be resumed into Phase 4.7."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ckpt_dir = os.path.join(self.tmp, "checkpoints", DATE)
        os.makedirs(self.ckpt_dir, exist_ok=True)
        self.orch = MainOrchestrator.__new__(MainOrchestrator)
        self.orch.data_dir = self.tmp
        self.orch.target_date = DATE

    def _touch(self, name):
        with open(os.path.join(self.ckpt_dir, name), "w", encoding="utf-8") as f:
            json.dump({}, f)

    def test_summary_without_hero_resumes_at_ecosystem_enrichment(self):
        self._touch("summary.json")
        self.assertEqual(self.orch._detect_resume_point(), 4.6)

    def test_hero_checkpoint_resumes_after_hero_generation(self):
        self._touch("summary.json")
        self._touch("hero.json")
        self.assertEqual(self.orch._detect_resume_point(), 5.0)

    def test_earlier_checkpoints_are_unchanged(self):
        self._touch("gathering.json")
        self.assertEqual(self.orch._detect_resume_point(), 2.0)
        self._touch("analysis.json")
        self.assertEqual(self.orch._detect_resume_point(), 3.0)
        self._touch("topics.json")
        self.assertEqual(self.orch._detect_resume_point(), 4.0)


if __name__ == "__main__":
    unittest.main()
