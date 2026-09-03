"""Muse Spark rate rows in the cost tracker.

Why this exists
---------------
2026-09-03: first test run on meta/muse-spark-1.3-contributor, Meta's
data-sharing tier of Muse Spark 1.3 on OpenRouter. The tracker prices LLM
calls from a static table keyed on the model id, and an unknown model falls
back to Opus 5 rates flagged as an ESTIMATE -- 50x the contributor tier's
real completion price. On a model this cheap that fallback does not merely
overstate the bill, it inverts the comparison the test run exists to make.

Rates verified live on 2026-09-03 against
/api/v1/models/meta/muse-spark-1.3-contributor-20260902/endpoints (one
Meta-served endpoint, so the row is the whole schedule) and the standard
tier's sibling endpoint.

Locks in:
  1. The contributor slug prices at $0.10 / $0.20 / $0.002 per MTok
     (prompt / completion / cache read) and is a measurement, not an estimate.
  2. The standard tier prices at $1.25 / $4.25 / $0.15 and never inherits
     the contributor discount from a substring match.
  3. Matching is case-insensitive on the OpenRouter slug, as for GLM.
  4. Reasoning tokens are billed as completion tokens: a call whose output is
     all reasoning costs exactly output_tokens x the completion rate.

Stdlib-only unittest (no network, no pipeline):

  python3 -m unittest tests.muse_spark_pricing_test -v
"""

import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

cost_tracker = importlib.import_module("agents.cost_tracker")
CostTracker = cost_tracker.CostTracker


class MuseSparkContributorRatesTest(unittest.TestCase):
    def test_contributor_tier_rates_are_a_measurement(self):
        tracker = CostTracker(model="meta/muse-spark-1.3-contributor")
        self.assertEqual(tracker.input_price, 0.10)
        self.assertEqual(tracker.output_price, 0.20)
        self.assertEqual(tracker.cache_hit_price, 0.002)
        # No published cache-write premium: writes bill at the prompt rate.
        self.assertEqual(tracker.cache_write_price, tracker.input_price)
        self.assertFalse(tracker.pricing_is_estimate)

    def test_slug_match_is_case_insensitive(self):
        tracker = CostTracker(model="META/Muse-Spark-1.3-Contributor")
        self.assertEqual(tracker.output_price, 0.20)
        self.assertFalse(tracker.pricing_is_estimate)

    def test_reasoning_is_billed_as_completion(self):
        # 1M completion tokens of pure reasoning at the contributor rate: the
        # transport folds reasoning into output_tokens, so nothing else moves.
        tracker = CostTracker(model="meta/muse-spark-1.3-contributor")
        tracker.record_call(
            caller="news_analyzer.reduce_rank",
            usage={"input_tokens": 0, "output_tokens": 1_000_000},
            thinking_level="DEEP",
        )
        self.assertAlmostEqual(tracker.get_total_cost().total_cost, 0.20, places=9)

    def test_cached_prefix_bills_at_the_cache_rate(self):
        tracker = CostTracker(model="meta/muse-spark-1.3-contributor")
        tracker.record_call(
            caller="social_analyzer.batch_1",
            usage={
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
            },
        )
        cost = tracker.get_total_cost()
        self.assertAlmostEqual(cost.input_cost, 0.10, places=9)
        self.assertAlmostEqual(cost.cache_hit_cost, 0.002, places=9)


class MuseSparkStandardRatesTest(unittest.TestCase):
    def test_standard_tier_does_not_inherit_the_discount(self):
        tracker = CostTracker(model="meta/muse-spark-1.3")
        self.assertEqual(tracker.input_price, 1.25)
        self.assertEqual(tracker.output_price, 4.25)
        self.assertEqual(tracker.cache_hit_price, 0.15)
        self.assertEqual(tracker.cache_write_price, tracker.input_price)
        self.assertFalse(tracker.pricing_is_estimate)

    def test_older_point_releases_share_the_standard_row(self):
        for slug in ("meta/muse-spark-1.1", "meta/muse-spark-1.2"):
            with self.subTest(slug=slug):
                tracker = CostTracker(model=slug)
                self.assertEqual(tracker.output_price, 4.25)
                self.assertFalse(tracker.pricing_is_estimate)


if __name__ == "__main__":
    unittest.main()
