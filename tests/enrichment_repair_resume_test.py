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

from agents.base import AnalyzedItem, CategoryReport, CollectedItem  # noqa: E402
from agents.link_enricher import INTERNAL_LINK_MARKER, LinkEnricher  # noqa: E402
from agents.orchestrator import MainOrchestrator, TopTopic  # noqa: E402

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
        self.callers.append(kwargs.get('caller'))
        text = _fenced_text(kwargs['messages'][0]['content'])
        self.texts.append(text)
        payload = json.dumps({
            "enriched_text": f"{text} [read more]({NEWS_LINK})",
            "links": [{"phrase": "read more", "item_id": NEWS_ITEM_ID,
                       "category": "news"}],
        })
        return SimpleNamespace(content=payload, stop_reason="end_turn")


class _HeroMustNotRegenerate:
    """A hero generator that fails the test if the repair path calls it."""

    def __init__(self):
        self.called = False

    async def generate(self, **kwargs):
        self.called = True
        raise AssertionError("hero must not regenerate")


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

        orch = MainOrchestrator.__new__(MainOrchestrator)
        orch.target_date = DATE
        orch.config_dir = cls.config_dir
        orch.data_dir = cls.data_dir
        orch.web_dir = cls.web_dir
        orch.provider_config = None
        orch.prompt_accessor = None
        orch.grounding_context = None
        orch.ecosystem_manager = _EcosystemStub()
        orch.gatherers = {"news": SimpleNamespace(
            coverage_date="2026-09-03", start_time=None, end_time=None)}
        orch.hero_generator = cls.hero
        orch.async_client = cls.client
        orch.degradations = []
        orch._restored_replay = None

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
        path = os.path.join(self.data_dir, "checkpoints", DATE, "summary.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn(INTERNAL_LINK_MARKER, saved["executive_summary"])
        self.assertIn(
            INTERNAL_LINK_MARKER, saved["enriched_category_summaries"]["news"])
        self.assertEqual(
            saved["enriched_category_summaries"]["research"], RESEARCH_SUMMARY_LINKED)
        self.assertEqual(saved["thinking"], "summary thinking")


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
