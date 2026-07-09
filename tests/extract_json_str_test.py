"""Regression tests for agents.base.extract_json_str.

Context / why this exists
-------------------------
- 2026-07-09: the daily pipeline went RED at the publish gate with
  `top_topics is empty (topic detection failed)`. Root cause: the
  topic-detection LLM call SUCCEEDED (claude-4.8-opus-aws, stop_reason=end_turn,
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


if __name__ == "__main__":
    unittest.main()
