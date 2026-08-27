"""Model-name matching must not conflate a release with its successors.

On 2026-08-27 the pipeline logged:

  STALE RELEASE: [news] "Google announces Gemini 3.5 Transcribe for
  AI-powered speech-to-text" - model GA was 2025-11-18 (281d before
  coverage), score capped at 40

Gemini 3.5 Transcribe was announced that day. It was demoted because
_find_stale_release_in_text tested `variant in text_lower`, and the
variant "gemini 3" is a substring of "gemini 3.5 transcribe" -- so the
new model inherited Gemini 3's November 2025 GA date. The cap is not the
whole cost: _mark_freshness also sets exclude_from_top and
exclude_from_summaries, so a launch-day story disappears from the report.

model_releases.yaml held 99 such shadowing pairs; "gpt 5" alone is a
substring of 26 newer names.

Locks in:
  1. A version continuation (".5", "-5") blocks the shorter match.
  2. The exact reported headline is not flagged stale.
  3. The plain name still matches (the fix must not disable the check).
  4. The most specific variant wins, so a matched release carries its own
     GA date rather than an ancestor's.
  5. Title prominence uses the same boundary rule.

Stdlib-only unittest:

  python3 -m unittest tests.staleness_version_boundary_test -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agents.staleness_checker import StalenessChecker  # noqa: E402


RELEASES_YAML = """\
google:
  Gemini-3:
    ga_date: "2025-11-18"
    api_date: "2025-11-18"
  Gemini-3-Flash:
    ga_date: "2025-12-17"
    api_date: "2025-12-17"
openai:
  GPT-5:
    ga_date: "2025-08-07"
    api_date: "2025-08-07"
  GPT-5.6:
    ga_date: "2026-06-25"
    api_date: "2026-06-25"
"""


class VersionBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        (Path(self.config_dir) / "model_releases.yaml").write_text(
            RELEASES_YAML, encoding="utf-8"
        )
        self.checker = StalenessChecker(
            config_dir=self.config_dir,
            target_date="2026-08-27",
            web_dir=os.path.join(self.config_dir, "web"),
        )
        self.addCleanup(shutil.rmtree, self.config_dir, ignore_errors=True)

    def _match(self, text):
        return self.checker._find_stale_release_in_text(text.lower())

    def test_reported_headline_is_not_stale(self):
        # The exact 2026-08-27 regression.
        self.assertIsNone(self._match(
            "Google announces Gemini 3.5 Transcribe for "
            "AI-powered speech-to-text"
        ))

    def test_dashed_version_continuation_also_blocked(self):
        self.assertIsNone(self._match("Google announces Gemini-3.5 Transcribe"))

    def test_successor_version_does_not_match_ancestor(self):
        for text in ("GPT 5.6 is here", "GPT 5.2 is here"):
            match = self._match(text)
            self.assertNotEqual(
                match[0] if match else None, "gpt 5",
                f"{text!r} must not resolve to the GPT-5 entry",
            )

    def test_plain_name_still_matches(self):
        # The fix must narrow false positives, not disable the check.
        match = self._match("Google announces Gemini 3 for everyone")
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "gemini 3")
        self.assertEqual(match[1], "2025-11-18")

    def test_most_specific_variant_wins(self):
        match = self._match("OpenAI ships GPT 5.6 today")
        self.assertIsNotNone(match)
        self.assertEqual(match[1], "2026-06-25",
                         "should carry GPT-5.6's own GA date, not GPT-5's")

    def test_substring_of_larger_word_does_not_match(self):
        self.assertIsNone(self._match("the ingemini 3000 project"))

    def test_title_prominence_uses_the_same_boundary(self):
        class _Item:
            def __init__(self, title):
                self.title = title

        class _Analyzed:
            def __init__(self, title):
                self.item = _Item(title)
                self.summary = "the company announces a release today"

        stale = _Analyzed("Gemini 3.5 Transcribe launches")
        self.assertFalse(
            self.checker._is_primarily_about_release(stale, "gemini 3"),
            "a Gemini 3.5 title must not count as Gemini 3 prominence",
        )
        real = _Analyzed("Gemini 3 launches")
        self.assertTrue(
            self.checker._is_primarily_about_release(real, "gemini 3")
        )


if __name__ == "__main__":
    unittest.main()
