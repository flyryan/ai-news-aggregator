"""Regression tests for agents.base.extract_json_str.

Context / why this exists
-------------------------
- 2026-07-09: the daily pipeline went RED at the publish gate with
  `top_topics is empty (topic detection failed)`. Root cause: the
  topic-detection LLM call SUCCEEDED (claude-5-opus-aws, stop_reason=end_turn,
  10086 output tokens) but the orchestrator parsed the response with a fragile
  hand-rolled chain:

      json.loads(response.content.strip().strip('```json').strip('```').strip())

  `str.strip('```json')` strips a CHARACTER SET (the chars ` ` j s o n), not the
  substring, and cannot recover a JSON object when the model prepends any prose
  preamble. Opus emitted a preamble/fence variant that day, so json.loads got a
  non-JSON first char -> "Expecting value: line 1 column 1 (char 0)" -> zero
  topics -> publish gate (correctly) blocked the commit and the site went stale.

  Fix: reuse the same robust extraction the rest of the pipeline already uses
  (BaseAnalyzer._parse_json_response), lifted into the module-level helper
  extract_json_str() so the orchestrator (which is not a BaseAnalyzer) can call
  it.

This test loads ONLY the pure extract_json_str function via stdlib `ast` (no
project imports, no httpx, no network, no API key) so it runs anywhere and can't
be defeated by an import-time failure -- same pattern as
call_with_thinking_signature_test.py.

It asserts the extractor survives every formatting variant a model realistically
emits: raw JSON, ```json fences, plain ``` fences, a prose preamble, a preamble
plus fence, and trailing commentary after the object.
"""

import ast
import json
import os
import unittest


def _load_extract_json_str():
    """Compile just the extract_json_str function out of agents/base.py."""
    base_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agents",
        "base.py",
    )
    with open(base_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "extract_json_str":
            import re

            class _NullLogger:
                def warning(self, *a, **k):
                    pass

                def error(self, *a, **k):
                    pass

            namespace = {"re": re, "logger": _NullLogger()}
            code = compile(ast.Module(body=[node], type_ignores=[]), base_path, "exec")
            exec(code, namespace)
            return namespace["extract_json_str"]

    raise AssertionError("extract_json_str not found in agents/base.py")


class ExtractJsonStrTest(unittest.TestCase):
    def setUp(self):
        self.extract_json_str = _load_extract_json_str()

    def _roundtrip(self, raw):
        return json.loads(self.extract_json_str(raw))

    def test_raw_object(self):
        self.assertEqual(
            self._roundtrip('{"topics": [{"name": "A"}]}'),
            {"topics": [{"name": "A"}]},
        )

    def test_json_fence(self):
        raw = '```json\n{"topics": [{"name": "B"}]}\n```'
        self.assertEqual(self._roundtrip(raw), {"topics": [{"name": "B"}]})

    def test_plain_fence(self):
        raw = '```\n{"topics": [{"name": "C"}]}\n```'
        self.assertEqual(self._roundtrip(raw), {"topics": [{"name": "C"}]})

    def test_prose_preamble(self):
        # The exact 2026-07-09 failure class: text before the JSON object.
        raw = 'Here is my analysis:\n\n{"topics": [{"name": "D"}]}'
        self.assertEqual(self._roundtrip(raw), {"topics": [{"name": "D"}]})

    def test_preamble_plus_fence_plus_trailer(self):
        raw = 'Sure! Here you go:\n```json\n{"topics": []}\n```\nHope that helps.'
        self.assertEqual(self._roundtrip(raw), {"topics": []})

    def test_trailing_commentary(self):
        raw = '{"topics": [{"name": "E"}]}\n\nLet me know if you need more.'
        self.assertEqual(self._roundtrip(raw), {"topics": [{"name": "E"}]})

    def test_nested_braces_and_strings(self):
        # Braces inside string values must not confuse the depth walk.
        raw = '{"topics": [{"name": "F {bracketed}", "categories": {"news": 5}}]}'
        self.assertEqual(
            self._roundtrip(raw),
            {"topics": [{"name": "F {bracketed}", "categories": {"news": 5}}]},
        )


class RawControlCharacterTest(unittest.TestCase):
    """ox-alpha emits literal newlines/tabs inside JSON string values.

    json.loads rejects raw control characters in strings ("Invalid control
    character"), which on 2026-08-22 stubbed two category summaries and sent
    link enrichment to its regex fallback -- all on responses whose content
    was otherwise fine. Extraction must escape raw control chars inside
    string bodies so the downstream parse succeeds.
    """

    def setUp(self):
        # Use the AST-compiled copy, not `from agents.base import ...`. This
        # module is deliberately stdlib-only (see the header) so it cannot be
        # defeated by an import-time failure -- and the real import is not
        # stdlib-only: `agents/__init__.py` eagerly imports the whole pipeline,
        # so it pulls httpx. In the dependency-light guard job that raised
        # ModuleNotFoundError, failing this step and skipping the nine guards
        # queued behind it, unnoticed from 2026-08-15.
        self.extract = _load_extract_json_str()

    def test_raw_newline_inside_string_is_escaped(self):
        raw = '{"summary": "first' + chr(10) + 'second line", "n": 1}'
        parsed = json.loads(self.extract(raw))
        self.assertEqual(parsed["summary"], "first\nsecond line")
        self.assertEqual(parsed["n"], 1)

    def test_raw_tab_and_cr_inside_strings_are_escaped(self):
        raw = (
            '{"a": "col1' + chr(9) + 'col2", "b": "x' + chr(13) + chr(10) + 'y"}'
        )
        parsed = json.loads(self.extract(raw))
        self.assertEqual(parsed["a"], "col1\tcol2")
        self.assertEqual(parsed["b"], "x\r\ny")

    def test_control_chars_outside_strings_still_fine(self):
        raw = '{\n\t"a": 1,\r\n"b": [1, 2]\n}'
        parsed = json.loads(self.extract(raw))
        self.assertEqual(parsed, {"a": 1, "b": [1, 2]})

    def test_control_chars_survive_wrapped_in_prose_and_fence(self):
        raw = (
            "Here is the result:\n```json\n"
            '{"summary": "bullet' + chr(10) + '- one", "top_10": ["a"]}\n'
            "```\nDone."
        )
        parsed = json.loads(self.extract(raw))
        self.assertEqual(parsed["summary"], "bullet\n- one")

    def test_reddit_reduce_failure_shape_parses(self):
        # Minimized shape of the 2026-08-22 social/reddit reduce failure:
        # markdown bullets with newlines inside the summary value.
        raw = (
            '{"category_summary": "Big themes:\\n\\n- **OpenAI** cut prices '
            '-- 2.6M views\\n- **NVIDIA** ramping", "top_10": ["a", "b"]}'
        ).replace("\\n", chr(10))
        parsed = json.loads(self.extract(raw))
        self.assertIn("OpenAI", parsed["category_summary"])
        self.assertEqual(parsed["top_10"], ["a", "b"])


class EmbeddedQuoteTest(unittest.TestCase):
    """Unescaped ASCII quotes inside string values must be repaired.

    The 2026-08-22 reddit reduce response contained **"be concise"** --
    literal 0x22 quotes mid-value. The parser ends the string there and the
    rest is garbage ("Expecting ',' delimiter"); no control-char fix can
    help. A string-close quote in valid JSON is ALWAYS followed by
    structural text (,: } ]), so any in-string quote that isn't is embedded
    and gets escaped. The transform is a no-op on already-valid JSON.
    """

    def setUp(self):
        # Use the AST-compiled copy, not `from agents.base import ...`. This
        # module is deliberately stdlib-only (see the header) so it cannot be
        # defeated by an import-time failure -- and the real import is not
        # stdlib-only: `agents/__init__.py` eagerly imports the whole pipeline,
        # so it pulls httpx. In the dependency-light guard job that raised
        # ModuleNotFoundError, failing this step and skipping the nine guards
        # queued behind it, unnoticed from 2026-08-15.
        self.extract = _load_extract_json_str()

    def test_reddit_reduce_shape_with_inner_quotes_parses(self):
        raw = (
            '```json\n{"top_10": ["a", "b"], "category_summary": '
            '"savings from **\\"be concise\\"** prompts and \\"quoted\\" words"}\n```'
        ).replace('\\"', '"')  # make the inner quotes REAL unescaped 0x22s
        parsed = json.loads(self.extract(raw))
        self.assertEqual(
            parsed["category_summary"],
            'savings from **"be concise"** prompts and "quoted" words',
        )
        self.assertEqual(parsed["top_10"], ["a", "b"])

    def test_valid_json_is_untouched(self):
        raw = '{"a": "ends with comma, then", "b": {"k": "v"}, "c": [1, 2]}'
        self.assertEqual(json.loads(self.extract(raw)), json.loads(raw))

    def test_escaped_quotes_still_work(self):
        # Python '\\\\"' would be TWO backslashes; a properly escaped JSON
        # quote is one backslash + quote ('\\\\"' at source -> \\" here).
        raw = '{"quote": "he said \\\"hi\\\" loudly"}'
        parsed = json.loads(self.extract(raw))
        self.assertEqual(parsed["quote"], 'he said "hi" loudly')


if __name__ == "__main__":
    unittest.main()
