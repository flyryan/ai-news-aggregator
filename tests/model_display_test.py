"""Per-day model attribution: config label, summary/index stamping, feed subtitle.

Why this exists
---------------
The site hard-coded "Powered by Claude Opus 5" in four places while models
rotate underneath (RDSec Opus 5 -> OpenRouter ox-alpha, ...). Reports must
self-describe which LLM produced them, the current model must be discoverable
by the SPA and the RSS feeds, and a "NEW" indicator should mark a fresh model
for a few days after each switch.

These tests lock in:

  1. providers.yaml accepts an optional llm.display_name human label.
  2. JSONGenerator stamps llm_model / llm_model_display into summary.json
     when configured, and omits both when not (offline regen stays clean).
  3. index.json carries current_model {id, display_name, since}; `since`
     survives same-model reruns and rolls forward only on a real switch,
     dated by the report date (never wall-clock, so offline regens agree).
  4. The feed subtitle resolves the display name leniently from
     providers.yaml (display_name -> model -> None on anything unresolvable)
     so manual/offline feed regeneration never hard-fails on env vars.

Stdlib-only (unittest), matching the repo's other tests:

  python3 -m unittest tests.model_display_test -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.config.schema import LLMProviderConfig  # noqa: E402
from generators.feed_generator import (  # noqa: E402
    build_feed_subtitle,
    resolve_llm_display_name,
)
from generators.json_generator import JSONGenerator  # noqa: E402


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _minimal_result(date="2026-08-22"):
    """Smallest OrchestratorResult dict _generate_summary_json tolerates."""
    return {
        "date": date,
        "coverage_date": "2026-08-21",
        "coverage_start": "2026-08-21T00:00:00",
        "coverage_end": "2026-08-21T23:59:59.999999",
        "executive_summary": "",
        "category_reports": {},
        "total_items_collected": 0,
        "total_items_analyzed": 0,
        "collection_status": {},
        "hero_image_url": None,
        "hero_image_prompt": None,
        "hero_image_usage": None,
        "generated_at": "2026-08-22T03:00:00",
        "top_topics": [],
    }


class SchemaDisplayNameTest(unittest.TestCase):
    def test_display_name_optional_and_preserved(self):
        cfg = LLMProviderConfig(
            api_key="k", base_url="https://openrouter.ai/api",
            model="stealth/ox-alpha", display_name="Ox Alpha",
        )
        self.assertEqual(cfg.display_name, "Ox Alpha")

    def test_display_name_defaults_to_none(self):
        cfg = LLMProviderConfig(api_key="k")
        self.assertIsNone(cfg.display_name)


class SummaryStampingTest(unittest.TestCase):
    def test_stamps_model_fields_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = JSONGenerator(tmp, llm_model="stealth/ox-alpha",
                                llm_model_display="Ox Alpha")
            date_dir = os.path.join(gen.data_dir, "2026-08-22")
            os.makedirs(date_dir)
            gen._generate_summary_json(date_dir, _minimal_result())

            summary = _load_json(os.path.join(date_dir, "summary.json"))
            self.assertEqual(summary["llm_model"], "stealth/ox-alpha")
            self.assertEqual(summary["llm_model_display"], "Ox Alpha")

    def test_omits_model_fields_when_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = JSONGenerator(tmp)
            date_dir = os.path.join(gen.data_dir, "2026-08-22")
            os.makedirs(date_dir)
            gen._generate_summary_json(date_dir, _minimal_result())

            summary = _load_json(os.path.join(date_dir, "summary.json"))
            self.assertNotIn("llm_model", summary)
            self.assertNotIn("llm_model_display", summary)


class IndexCurrentModelTest(unittest.TestCase):
    def test_fresh_index_gets_since_report_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = JSONGenerator(tmp, llm_model="stealth/ox-alpha",
                                llm_model_display="Ox Alpha")
            gen._update_date_index(_minimal_result("2026-08-22"))

            idx = json.load(open(os.path.join(gen.data_dir, "index.json")))
            self.assertEqual(idx["current_model"]["id"], "stealth/ox-alpha")
            self.assertEqual(idx["current_model"]["display_name"], "Ox Alpha")
            # Dated by the report date, not wall-clock: offline regens of old
            # days must not move the switch point.
            self.assertEqual(idx["current_model"]["since"], "2026-08-22")

    def test_same_model_keeps_original_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "data"))
            index_path = os.path.join(tmp, "data", "index.json")
            json.dump({
                "version": "1.0", "dates": [],
                "current_model": {"id": "stealth/ox-alpha",
                                  "display_name": "Ox Alpha",
                                  "since": "2026-08-22"},
            }, open(index_path, "w"))

            gen = JSONGenerator(tmp, llm_model="stealth/ox-alpha",
                                llm_model_display="Ox Alpha")
            gen._update_date_index(_minimal_result("2026-08-25"))

            idx = _load_json(index_path)
            self.assertEqual(idx["current_model"]["since"], "2026-08-22",
                             "a same-model rerun must not reset the clock")

    def test_new_model_rolls_since_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "data"))
            index_path = os.path.join(tmp, "data", "index.json")
            json.dump({
                "version": "1.0", "dates": [],
                "current_model": {"id": "claude-5-opus-aws",
                                  "display_name": "Claude Opus 5",
                                  "since": "2026-06-11"},
            }, open(index_path, "w"))

            gen = JSONGenerator(tmp, llm_model="stealth/ox-alpha",
                                llm_model_display="Ox Alpha")
            gen._update_date_index(_minimal_result("2026-08-22"))

            idx = _load_json(index_path)
            self.assertEqual(idx["current_model"]["since"], "2026-08-22")

    def test_unconfigured_leaves_index_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "data"))
            index_path = os.path.join(tmp, "data", "index.json")
            json.dump({"version": "1.0", "dates": [], "latestDate": None},
                      open(index_path, "w"))

            gen = JSONGenerator(tmp)
            gen._update_date_index(_minimal_result("2026-08-22"))

            idx = _load_json(index_path)
            self.assertNotIn("current_model", idx)


class FeedSubtitleTest(unittest.TestCase):
    def _write_config(self, config_dir, body):
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, "providers.yaml")
        open(path, "w").write(body)

    def test_prefers_display_name_then_model(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 'llm:\n  model: stealth/ox-alpha\n'
                                  '  display_name: Ox Alpha\n')
            self.assertEqual(resolve_llm_display_name(d), "Ox Alpha")

        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 'llm:\n  model: claude-5-opus-aws\n')
            self.assertEqual(resolve_llm_display_name(d), "claude-5-opus-aws")

    def test_lenient_on_missing_or_unresolved(self):
        self.assertIsNone(resolve_llm_display_name("/nonexistent-dir"))
        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 'llm:\n  model: "${UNSET_VAR}"\n')
            self.assertIsNone(resolve_llm_display_name(d))

    def test_subtitle_builder(self):
        self.assertEqual(build_feed_subtitle("Ox Alpha"),
                         "Daily AI/ML news powered by Ox Alpha")
        self.assertIsNone(build_feed_subtitle(None))


if __name__ == "__main__":
    unittest.main()
