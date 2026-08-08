"""Regression tests for content rendering: HTML→text and markdown→HTML.

Context / why this exists
-------------------------
2026-08-08: a LessWrong research card rendered as an unreadable wall of text
with two distinct defects, both visible on the live site.

1. STRUCTURE LOST AT COLLECTION. Both content paths in research_gatherer used

       re.sub(r'<[^>]+>', '', html)

   which deletes tags without replacing them, so `</p><p>` closes up and the
   last word of a block welds onto the first word of the next: "TL;DRHow can we
   study...", "EnvironmentsEnvironments are re-introduced...". The stored
   `content` for the affected item had 96,856 characters and ZERO newlines, so
   no downstream renderer could recover the paragraphs — the structure was gone
   before it was ever written to disk. The same pass dropped `<code>` spans, so
   inline code read as bare prose.

2. STATISTICAL MARKERS EATEN AS BOLD. `\\*\\*(.+?)\\*\\*` treats any two
   asterisk pairs on a line as bold delimiters, so the significance marker in

       "there is a significant (***) correlation across many models"

   rendered as "(<strong>*)". The marker is data, not formatting.

The bold and inline-code regexes are duplicated in the frontend
(frontend/src/lib/services/markdown.ts) because the client re-renders when
`*_html` is absent. PatternLockstepTest pins them equal — change one, change
both.

stdlib only (unittest), matching the other guards in .github/workflows/tests.yml.
bs4 is a declared project dependency; html_text falls back to a regex path
without it, and the assertions here hold either way.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    """Load a module by path, bypassing its package __init__.

    `import agents.html_text` executes agents/__init__.py, which pulls in the
    whole pipeline (llm_client -> httpx, gatherers, analyzers). This test only
    needs one pure function, so it should not require the pipeline's dependency
    tree to be installed in CI.
    """
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


html_to_text = _load("_html_text", "agents/html_text.py").html_to_text

_json_generator = _load("_json_generator", "generators/json_generator.py")
_apply_bold = _json_generator._apply_bold
_apply_italic = _json_generator._apply_italic
_apply_code = _json_generator._apply_code


def render_inline(text: str) -> str:
    """Same order as JSONGenerator.convert_inline: bold, then italic, then code."""
    return _apply_code(_apply_italic(_apply_bold(text)))


class BlockStructureTest(unittest.TestCase):
    """Block boundaries must survive the HTML → text pass."""

    def test_heading_does_not_weld_to_next_block(self):
        text = html_to_text("<h1>TL;DR</h1><p>How can we study misalignment?</p>")
        self.assertNotIn("TL;DRHow", text)
        self.assertIn("TL;DR", text)
        self.assertIn("How can we study misalignment?", text)

    def test_consecutive_paragraphs_stay_separate(self):
        text = html_to_text("<p>Environments</p><p>Environments are re-introduced.</p>")
        self.assertNotIn("EnvironmentsEnvironments", text)

    def test_divs_also_break(self):
        text = html_to_text("<div>first block</div><div>second block</div>")
        self.assertNotIn("blocksecond", text)

    def test_inline_code_survives_as_backticks(self):
        text = html_to_text("<p>Call <code>end_task</code> when finished.</p>")
        self.assertIn("`end_task`", text)

    def test_list_items_become_bullets(self):
        text = html_to_text("<ul><li>first</li><li>second</li></ul>")
        self.assertIn("- first", text)
        self.assertIn("- second", text)

    def test_punctuation_after_a_tag_is_not_spaced_off(self):
        """Joining inline children with a space left "`end_task` : end the session"."""
        text = html_to_text("<ul><li><code>end_task</code>: end the session.</li></ul>")
        self.assertIn("`end_task`: end", text)

    def test_script_and_style_content_is_dropped(self):
        text = html_to_text("<p>real</p><script>var x=1;</script><style>p{color:red}</style>")
        self.assertNotIn("var x", text)
        self.assertNotIn("color:red", text)
        self.assertIn("real", text)

    def test_truncation_lands_on_a_word_boundary(self):
        text = html_to_text("<p>" + " ".join(["alpha"] * 400) + "</p>", max_length=100)
        self.assertTrue(text.endswith("..."))
        self.assertLessEqual(len(text), 104)
        self.assertNotIn("alph...", text)

    def test_empty_input_is_empty_output(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text(None), "")


class BoldTest(unittest.TestCase):
    """Bold only where the delimiters look deliberate."""

    INCIDENTAL = [
        "there is a significant (***) correlation across many models",
        "p < 0.01 (**) and p < 0.05 (*)",
        "asterisks * scattered ** oddly *** here",
        "math: 3 * 4 ** 2",
        "a ** b",  # spaced pair is not an opener
    ]

    REAL = [
        ("this is **actually bold** here", "this is <strong>actually bold</strong> here"),
        ("**at the start** of a line", "<strong>at the start</strong> of a line"),
        ("**one** and **two**", "<strong>one</strong> and <strong>two</strong>"),
        ("**a**", "<strong>a</strong>"),  # single-character content
    ]

    def test_incidental_asterisks_are_left_alone(self):
        for text in self.INCIDENTAL:
            with self.subTest(text=text):
                self.assertNotIn("<strong>", _apply_bold(text))

    def test_real_bold_still_renders(self):
        for text, expected in self.REAL:
            with self.subTest(text=text):
                self.assertEqual(_apply_bold(text), expected)


class ItalicTest(unittest.TestCase):
    """Single-asterisk italics, without eating incidental asterisks."""

    def test_italic_renders(self):
        self.assertEqual(
            _apply_italic("infer *who* they are talking to"),
            "infer <em>who</em> they are talking to",
        )

    def test_significance_markers_are_not_italic(self):
        for text in ["a significant (***) correlation", "p < 0.05 (*)", "(**)"]:
            with self.subTest(text=text):
                self.assertNotIn("<em>", _apply_italic(text))

    def test_multiplication_and_intraword_asterisks_are_left_alone(self):
        for text in ["math: 3 * 4 and 5 * 6", "a*b*c", "a footnote[1] * bullet-ish"]:
            with self.subTest(text=text):
                self.assertNotIn("<em>", _apply_italic(text))

    def test_bold_wins_over_italic(self):
        """Bold runs first, so ** is consumed before a lone * is considered."""
        self.assertEqual(
            render_inline("**bold** and *italic* together"),
            "<strong>bold</strong> and <em>italic</em> together",
        )


class InlineCodeTest(unittest.TestCase):
    def test_backticks_become_code_tags(self):
        self.assertEqual(_apply_code("call `end_task` now"), "call <code>end_task</code> now")

    def test_unpaired_backtick_is_left_alone(self):
        self.assertNotIn("<code>", _apply_code("a lone ` backtick"))

    def test_bold_and_code_compose(self):
        self.assertEqual(
            _apply_code(_apply_bold("**bold** with `code`")),
            "<strong>bold</strong> with <code>code</code>",
        )


class PatternLockstepTest(unittest.TestCase):
    """The frontend re-implements these; a silent drift means two renderings."""

    def setUp(self):
        self.backend = (REPO_ROOT / "generators/json_generator.py").read_text()
        self.markdown_ts = (REPO_ROOT / "frontend/src/lib/services/markdown.ts").read_text()
        self.sanitize_ts = (REPO_ROOT / "frontend/src/lib/services/sanitize.ts").read_text()

    def test_bold_pattern_matches(self):
        backend = re.search(r"_BOLD_RE = re\.compile\(r'(.+)'\)", self.backend).group(1)
        frontend = re.search(r"const BOLD_RE = /(.+)/g;", self.markdown_ts).group(1)
        self.assertEqual(frontend, backend, "BOLD_RE drifted between backend and frontend")

    def test_italic_pattern_matches(self):
        backend = re.search(r"_ITALIC_RE = re\.compile\(r'(.+)'\)", self.backend).group(1)
        frontend = re.search(r"const ITALIC_RE = /(.+)/g;", self.markdown_ts).group(1)
        self.assertEqual(frontend, backend, "ITALIC_RE drifted between backend and frontend")

    def test_code_pattern_matches(self):
        backend = re.search(r"_CODE_RE = re\.compile\(r'(.+)'\)", self.backend).group(1)
        frontend = re.search(r"const CODE_RE = /(.+)/g;", self.markdown_ts).group(1)
        self.assertEqual(frontend, backend, "CODE_RE drifted between backend and frontend")

    def test_code_is_allowed_by_both_sanitizers(self):
        """A <code> the renderer emits but the sanitizer strips renders as nothing."""
        allowed_py = re.search(r"ALLOWED_TAGS = \{([^}]+)\}", self.backend).group(1)
        allowed_ts = re.search(r"const ALLOWED_TAGS = \[([^\]]+)\]", self.sanitize_ts).group(1)
        self.assertIn("'code'", allowed_py)
        self.assertIn("'code'", allowed_ts)


class EndToEndTest(unittest.TestCase):
    def test_lesswrong_shaped_post_renders_readably(self):
        html = (
            "<h1>TL;DR</h1><p>How can we study misalignment?</p>"
            "<h2>Environments</h2><p>Environments are re-introduced throughout.</p>"
            "<ul><li><code>execute_command</code>: run shell commands.</li></ul>"
            "<p>there is a significant (***) correlation across models</p>"
        )
        rendered = render_inline(html_to_text(html))

        self.assertNotIn("TL;DRHow", rendered)
        self.assertNotIn("EnvironmentsEnvironments", rendered)
        self.assertIn("(***)", rendered)          # marker survives verbatim
        self.assertNotIn("<strong>", rendered)    # and was never mistaken for bold
        self.assertIn("<code>execute_command</code>", rendered)


if __name__ == "__main__":
    unittest.main()
