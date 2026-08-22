"""Pricing truthfulness for OpenRouter-era models.

The 2026-08-22 run billed stealth/ox-alpha at Opus 5 rates ($11.63 of
fiction) because the cost tracker never learns the real model name and
defaults unknown models to Opus pricing. ox-alpha is $0/$0 on OpenRouter
-- confirmed live via /api/v1/models/stealth/ox-alpha/endpoints -- so a
zero-rate entry is exact, not an estimate. Separately, OpenRouter's image
responses carry an authoritative ``usage.cost``; re-pricing tokens from
our Gemini table ignored it.

Locks in:
  1. CostTracker('stealth/ox-alpha') prices everything at zero.
  2. reset_tracker(model) propagates the model so reports/replay stop
     claiming the tracker default ('claude-5-opus-aws').
  3. Unknown models still fall back to Opus rates (worst case) -- but
     now flagged, via the fallback_reason attribute the summary prints.
  4. price_image_usage prefers provider-reported usage.cost over the
     token-class table.

Stdlib-only unittest:

  python3 -m unittest tests.cost_pricing_test -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.cost_tracker import (  # noqa: E402
    CostTracker,
    price_image_usage,
    reset_tracker,
)


class OxAlphaPricingTest(unittest.TestCase):
    def test_ox_alpha_costs_zero(self):
        tracker = CostTracker(model="stealth/ox-alpha")
        self.assertEqual(tracker.input_price, 0.0)
        self.assertEqual(tracker.output_price, 0.0)

        tracker.record_call("news_analyzer.reduce_rank", {
            "input_tokens": 300_000,
            "output_tokens": 400_000,
        }, "DEEP")
        breakdown = tracker.get_total_cost()
        self.assertEqual(breakdown.input_cost, 0.0)
        self.assertEqual(breakdown.output_cost, 0.0)

    def test_reset_tracker_propagates_model(self):
        tracker = reset_tracker(model="stealth/ox-alpha")
        try:
            self.assertEqual(tracker.model, "stealth/ox-alpha")
            self.assertEqual(tracker.input_price, 0.0)
        finally:
            reset_tracker()  # leave the global clean for other tests

    def test_unknown_model_falls_back_to_opus_and_says_so(self):
        tracker = CostTracker(model="some/future-model")
        self.assertGreater(tracker.input_price, 0.0)
        self.assertTrue(tracker.pricing_is_estimate)


class ProviderReportedImageCostTest(unittest.TestCase):
    def test_provider_reported_cost_wins_over_token_table(self):
        # Actual shape reported by OpenRouter's image endpoint on 2026-08-22.
        usage = {
            "prompt_tokens": 1362,
            "completion_tokens": 1707,
            "total_tokens": 3069,
            "cost": 0.144168,
            "completion_tokens_details": {"image_tokens": 1120},
        }
        result = price_image_usage(usage)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.total_cost, 0.144168, places=6)

    def test_without_reported_cost_falls_back_to_table(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 1000,
            "completion_tokens_details": {"image_tokens": 900},
        }
        result = price_image_usage(usage)
        self.assertIsNotNone(result)
        self.assertGreater(result.total_cost, 0.0)

    def test_none_usage_still_returns_none(self):
        self.assertIsNone(price_image_usage(None))


if __name__ == "__main__":
    unittest.main()
