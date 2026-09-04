"""Link enrichment must not parse a reply the model never finished writing.

What happened
-------------
2026-09-04: the production run was on google/gemini-3.8-flash via OpenRouter,
whose 65,536-token completion cap is SHARED between reasoning and visible
output. The reddit category summary's enrichment spent roughly 80k characters
reasoning, so the JSON answer was cut off mid-object and the response came back
with ``stop_reason == "max_tokens"``. ``LinkEnricher._enrich_text`` never looked
at ``stop_reason``: it handed the clipped object straight to the JSON extractor
and published whatever fell out. The analyzers have handled exactly this shape
of failure since 2026-08 (``BaseAnalyzer._handle_truncated_batch``); the
enricher was the last caller that did not.

Locks in the bounded escalation:

  1. A first attempt always runs at STANDARD, and a clean reply ends it there --
     the escalation must not cost a second call on the happy path.
  2. ``stop_reason == "max_tokens"`` is NOT parsed, however well-formed the
     clipped text happens to look, and escalates to QUICK (less reasoning under
     the same shared cap, so more of it reaches the answer).
  3. Truncation on every attempt degrades to the original text and SAYS SO in
     ``degradations``. Silence is what made 2026-08-24 look healthy.
  4. An unparseable reply is offered to the pre-existing validated regex
     fallback IMMEDIATELY, on the attempt that produced it. If it validates,
     that is the answer and there is no second call -- exactly what the
     single-call code on ``main`` did, at exactly its cost. Only an unparseable
     reply the fallback rejects escalates -- including a reply that parses to an
     array, which `.get` would otherwise throw on. Deferring the fallback to the
     end of the loop was strictly worse than ``main``: a recoverable first reply
     was discarded whenever the second attempt came back truncated.
  4b. A truncated reply is never handed to the fallback. It is clipped by
     definition, and recovering a known-clipped text is the 2026-09-04 incident.
  5. An exception out of ``call_with_thinking`` degrades immediately with no
     second attempt: transport retries already happened underneath it
     (2026-09-04, in-band OpenRouter stream errors), so re-asking here would
     just multiply a known-dead provider.
  6. The ``caller`` tag is identical on every attempt -- the replay taxonomy
     keys on it, and attempts are already independent calls there.

Stdlib-only unittest (no network, no LLM):

  python3 -m unittest tests.link_enricher_truncation_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from agents.link_enricher import LinkEnricher  # noqa: E402
from agents.llm_client import ThinkingLevel  # noqa: E402


# Every degradation path logs a warning or an error on purpose. Silence them
# here so they don't leak into test/CI output via logging's lastResort handler.
# Set once at import, not per run: assertLogs raises this logger's level for the
# duration of its own block, and a per-run reset would clobber that.
logging.getLogger("agents.link_enricher").setLevel(logging.CRITICAL)

DATE = "2026-09-04"
CATEGORY = "news"
ITEM_ID = "abc123def456"
CONTEXT = f"{CATEGORY} summary"

ORIGINAL_TEXT = (
    "Two labs shipped competing agent frameworks and the thread argued "
    "about benchmarks all day."
)
ENRICHED_TEXT = (
    "Two labs [shipped competing agent frameworks]"
    f"(/?date={DATE}&category={CATEGORY}#item-{ITEM_ID}) and the thread "
    "argued about benchmarks all day."
)
VALID_PAYLOAD = json.dumps({
    "enriched_text": ENRICHED_TEXT,
    "links": [{
        "phrase": "shipped competing agent frameworks",
        "item_id": ITEM_ID,
        "category": CATEGORY,
    }],
})

# The trap: a max_tokens reply whose visible text happens to be complete,
# balanced JSON -- just half the answer. Nothing about the CONTENT reveals the
# clip, which is why stop_reason has to be the signal.
TRUNCATED_BUT_PARSEABLE = json.dumps({
    "enriched_text": "Two labs [shipped competing agent frameworks](/?date=",
    "links": [],
})

# No object at all: extract_json_str finds nothing to walk, json.loads raises,
# and the regex fallback has no "enriched_text" key to match.
UNPARSEABLE_PROSE = "I was unable to enrich this text."

# Regex-visible but regex-unrecoverable: the fallback pattern matches
# "enriched_text", and validation then rejects it for the unbalanced bracket
# and the length collapse.
UNPARSEABLE_CLIPPED = '{"enriched_text": "Two labs [shipped", "links": [{"phrase"'

# Regex-recoverable: json.loads chokes on the trailing garbage, but the
# extracted text is balanced and full length, so the pre-existing validated
# fallback accepts it. This is the shape `main` rescued for free with a single
# call; pinned so the escalation can neither drop it nor bill a second call for
# it.
REGEX_RECOVERABLE = (
    '{"enriched_text": ' + json.dumps(ENRICHED_TEXT) + ', "links": [ , ] }'
)


class _FakeAsyncClient:
    """Records every call and replays a scripted response (or raises)."""

    def __init__(self, script):
        self._script = list(script)
        self.profiles = []
        self.callers = []

    async def call_with_thinking(self, **kwargs):
        self.profiles.append(kwargs.get('profile'))
        self.callers.append(kwargs.get('caller'))
        if not self._script:
            raise AssertionError(
                f"enricher made more LLM calls than the test scripted "
                f"({len(self.profiles)})"
            )
        nxt = self._script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    @property
    def calls(self):
        return len(self.profiles)


def _response(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def _run(script):
    """Drive one category-summary enrichment through the scripted responses.

    The executive summary is empty on purpose so it short-circuits before any
    LLM call: every recorded call then belongs to the single category summary,
    which is the text the 2026-09-04 incident lost.
    """
    client = _FakeAsyncClient(script)
    enricher = LinkEnricher(client, DATE)
    category_reports = {
        CATEGORY: {
            'category_summary': ORIGINAL_TEXT,
            # Plain-dict items: the _build_item_list dict path.
            'all_items': [{
                'id': ITEM_ID,
                'title': 'Two labs ship competing agent frameworks',
                'summary': 'Both landed on the same afternoon.',
            }],
        }
    }
    _exec, categories, _topics = asyncio.run(
        enricher.enrich_all("", category_reports, [])
    )
    return client, enricher, categories[CATEGORY]


class LinkEnricherEscalationTest(unittest.TestCase):
    """One text, every way the model can answer."""

    def test_clean_first_reply_costs_exactly_one_standard_call(self):
        client, enricher, text = _run([_response(VALID_PAYLOAD)])

        self.assertEqual(client.calls, 1)
        self.assertEqual(client.profiles, [ThinkingLevel.STANDARD])
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_truncated_then_clean_escalates_to_quick_and_recovers(self):
        client, enricher, text = _run([
            _response(TRUNCATED_BUT_PARSEABLE, stop_reason="max_tokens"),
            _response(VALID_PAYLOAD),
        ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            client.profiles, [ThinkingLevel.STANDARD, ThinkingLevel.QUICK]
        )
        # The clipped half-answer was parseable JSON. Returning it would have
        # published "(/?date=" as the reader's link.
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_truncated_everywhere_degrades_instead_of_parsing(self):
        with self.assertLogs("agents.link_enricher", level="WARNING") as logs:
            client, enricher, text = _run([
                _response(TRUNCATED_BUT_PARSEABLE, stop_reason="max_tokens"),
                _response(TRUNCATED_BUT_PARSEABLE, stop_reason="max_tokens"),
            ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            client.profiles, [ThinkingLevel.STANDARD, ThinkingLevel.QUICK]
        )
        self.assertEqual(text, ORIGINAL_TEXT)
        self.assertEqual(len(enricher.degradations), 1)
        self.assertIn("truncated at max_tokens", enricher.degradations[0])
        self.assertIn(CONTEXT, enricher.degradations[0])

        # Each attempt's warning has to be actionable on its own: which text,
        # which profile, and how much output the shared cap actually left us.
        per_attempt = [
            line for line in logs.output
            if "output_chars=" in line and CONTEXT in line
        ]
        self.assertEqual(len(per_attempt), 2)
        self.assertIn("STANDARD", per_attempt[0])
        self.assertIn("QUICK", per_attempt[1])
        for line in per_attempt:
            self.assertIn("max_tokens", line)
            self.assertIn(str(len(TRUNCATED_BUT_PARSEABLE)), line)

    def test_unparseable_then_clean_escalates_and_recovers(self):
        # UNPARSEABLE_PROSE is deliberately regex-UNrecoverable (no
        # "enriched_text" key at all), so the fallback declines it and the
        # escalation is genuinely reached. A recoverable first reply would --
        # correctly -- stop after one call; that case is its own test below.
        client, enricher, text = _run([
            _response(UNPARSEABLE_PROSE),
            _response(VALID_PAYLOAD),
        ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            client.profiles, [ThinkingLevel.STANDARD, ThinkingLevel.QUICK]
        )
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_a_json_array_reply_is_unparseable_rather_than_a_crash(self):
        """`.get` on a list is an AttributeError, and the class contract says
        every failure path here returns readable prose."""
        client, enricher, text = _run([
            _response('["not", "an", "object"]'),
            _response(VALID_PAYLOAD),
        ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_a_regex_recoverable_reply_costs_exactly_one_call(self):
        """`main` rescued this shape with one call and no degradation. The
        escalation must not turn a free recovery into a second billed attempt:
        the fallback runs on the attempt that produced the reply, not after the
        profiles are exhausted."""
        with self.assertLogs("agents.link_enricher", level="INFO") as logs:
            client, enricher, text = _run([_response(REGEX_RECOVERABLE)])

        self.assertEqual(client.calls, 1)
        self.assertEqual(client.profiles, [ThinkingLevel.STANDARD])
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])
        self.assertTrue(
            any("validated regex fallback" in line and CONTEXT in line
                for line in logs.output),
            f"recovery must say so; got {logs.output}",
        )

    def test_a_recoverable_reply_is_not_lost_to_a_later_truncation(self):
        """The regression this ordering exists to prevent.

        Deferring the fallback to the end of the loop meant only the LAST
        attempt's content ever reached it: a recoverable first reply was
        overwritten by a second attempt that came back at max_tokens, and the
        text was degraded away -- strictly worse than the single-call code on
        `main`, and paid for with an extra call. The scripted second response
        must never be requested.
        """
        client, enricher, text = _run([
            _response(REGEX_RECOVERABLE),
            _response(TRUNCATED_BUT_PARSEABLE, stop_reason="max_tokens"),
        ])

        self.assertEqual(client.calls, 1)
        self.assertEqual(client.profiles, [ThinkingLevel.STANDARD])
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_unparseable_everywhere_beyond_regex_repair_degrades(self):
        client, enricher, text = _run([
            _response(UNPARSEABLE_PROSE),
            _response(UNPARSEABLE_CLIPPED),
        ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            client.profiles, [ThinkingLevel.STANDARD, ThinkingLevel.QUICK]
        )
        self.assertEqual(text, ORIGINAL_TEXT)
        self.assertEqual(
            enricher.degradations, [f"{CONTEXT}: unparseable enrichment response"]
        )

    def test_last_attempt_still_gets_the_validated_regex_fallback(self):
        """Pre-existing recovery path; the escalation must not shadow it.

        The first reply is regex-UNrecoverable on purpose, so the run really
        reaches the final profile -- and the fallback still applies there, on
        the attempt that produced the reply.
        """
        client, enricher, text = _run([
            _response(UNPARSEABLE_PROSE),
            _response(REGEX_RECOVERABLE),
        ])

        self.assertEqual(client.calls, 2)
        self.assertEqual(text, ENRICHED_TEXT)
        self.assertEqual(enricher.degradations, [])

    def test_exception_degrades_immediately_without_a_second_attempt(self):
        client, enricher, text = _run([RuntimeError("provider gone")])

        # The transport layer owns retries. A second profile here would only
        # re-ask a provider that already exhausted its backoff window.
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.profiles, [ThinkingLevel.STANDARD])
        self.assertEqual(text, ORIGINAL_TEXT)
        self.assertEqual(enricher.degradations, [f"{CONTEXT}: RuntimeError"])


class LinkEnricherCallerTagTest(unittest.TestCase):
    """The replay taxonomy keys on `caller`; escalation must not fork it."""

    def test_caller_is_identical_on_every_attempt(self):
        client, _enricher, _text = _run([
            _response(TRUNCATED_BUT_PARSEABLE, stop_reason="max_tokens"),
            _response(VALID_PAYLOAD),
        ])

        self.assertEqual(
            client.callers,
            [f"link_enricher.{CONTEXT}"] * 2,
        )


class EscalationBoundTest(unittest.TestCase):
    """The escalation is bounded by a declared list, not by ad-hoc retries."""

    def test_profiles_are_standard_then_quick_and_nothing_more(self):
        self.assertEqual(
            tuple(LinkEnricher.ENRICH_PROFILES),
            (ThinkingLevel.STANDARD, ThinkingLevel.QUICK),
        )


if __name__ == "__main__":
    unittest.main()
