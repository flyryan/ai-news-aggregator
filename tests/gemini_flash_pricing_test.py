"""Gemini 3.8 Flash rate row in the cost tracker.

Why this exists
---------------
2026-09-04: google/gemini-3.8-flash became the production model and had no
row in the tracker's static table, so every call fell through to the
unknown-model branch and was priced at Opus 5 rates flagged as an ESTIMATE.
The day published $28.31 for roughly $4.23 of real spend -- a 6.7x
overstatement on the number the run reports about itself.

Rates verified live on 2026-09-04 against
/api/v1/models/google/gemini-3.8-flash/endpoints: every Google / Google AI
Studio endpoint lists the same schedule, so this single row is the whole
schedule.

Locks in:
  1. The slug prices at $0.75 / $3.75 / $0.075 per MTok (prompt / completion /
     cache read) and is a measurement, not an estimate.
  2. Matching is case-insensitive on the OpenRouter slug, as for GLM and
     Muse Spark.
  3. Reasoning tokens are billed as completion tokens: internal_reasoning
     arrives folded into output_tokens, so a call whose output is all
     reasoning costs exactly output_tokens x the completion rate.
  4. A cached prefix bills the prompt rate on input and the much cheaper
     cache-read rate on the re-read, rather than one blended number.

Stdlib-only unittest (no network, no pipeline):

  python3 -m unittest tests.gemini_flash_pricing_test -v
"""

import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

cost_tracker = importlib.import_module("agents.cost_tracker")
CostTracker = cost_tracker.CostTracker


class GeminiFlashRatesTest(unittest.TestCase):
    def test_rates_are_a_measurement(self):
        tracker = CostTracker(model="google/gemini-3.8-flash")
        self.assertEqual(tracker.input_price, 0.75)
        self.assertEqual(tracker.output_price, 3.75)
        self.assertEqual(tracker.cache_hit_price, 0.075)
        # The explicit-cache write rate OpenRouter lists is never triggered by
        # this transport, so writes bill at the prompt rate.
        self.assertEqual(tracker.cache_write_price, tracker.input_price)
        self.assertFalse(tracker.pricing_is_estimate)

    def test_slug_match_is_case_insensitive(self):
        tracker = CostTracker(model="GOOGLE/Gemini-3.8-Flash")
        self.assertEqual(tracker.output_price, 3.75)
        self.assertFalse(tracker.pricing_is_estimate)

    def test_reasoning_is_billed_as_completion(self):
        # 1M completion tokens of pure reasoning: the transport folds
        # internal_reasoning into output_tokens, so nothing else moves.
        tracker = CostTracker(model="google/gemini-3.8-flash")
        tracker.record_call(
            caller="news_analyzer.reduce_rank",
            usage={"input_tokens": 0, "output_tokens": 1_000_000},
            thinking_level="DEEP",
        )
        self.assertAlmostEqual(tracker.get_total_cost().total_cost, 3.75, places=9)

    def test_cached_prefix_bills_at_the_cache_rate(self):
        tracker = CostTracker(model="google/gemini-3.8-flash")
        tracker.record_call(
            caller="social_analyzer.batch_1",
            usage={
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
            },
        )
        cost = tracker.get_total_cost()
        self.assertAlmostEqual(cost.input_cost, 0.75, places=9)
        self.assertAlmostEqual(cost.cache_hit_cost, 0.075, places=9)


if __name__ == "__main__":
    unittest.main()
