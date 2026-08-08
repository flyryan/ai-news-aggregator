"""The replay publish gate must catch credentials without eating the news.

Regression test for 2026-07-31, when the whole day's replay vanished. Phase 6.2
raised out of ``build()``:

    ValueError: Replay prompt artifact contains disallowed content matching
    'sk-[A-Za-z0-9_\\-]{16,}': 'sk-orchestration-and-multi-robot-collabo'

The "credential" was a DeepMind blog URL slug. ``sk-`` had no left word boundary
and the body accepted hyphens, so ``ta|sk-orchestration-and-multi-robot-
collaboration`` matched. 120 of 218 published days (55%) carry a string that
trips the old pattern -- ``sk-hynix-plan-590-billion-chip-investment``,
``sk-xai-datacenters-trump-administration``, and so on. The gate only began
scanning prompts on 2026-07-29; 07-30 was clean by luck and 07-31 was not, so
every run since has been a coin flip.

Two properties are locked in here:

  * kebab-case prose and URLs are not credentials, and
  * a violation in the prompts drops *the prompts*, not the index and stream
    alongside them. ``build()`` returned all three together, so one bad slug
    discarded the entire artifact.

The security direction is the half that must not rot: every real key shape still
has to trip the gate. Weakening the pattern to fix false positives is only
correct if the true positives survive, so they are asserted explicitly.
"""

import gzip
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# replay_generator needs agents.cost_tracker and agents.replay_taxonomy, both
# stdlib-only. Importing them the normal way first executes agents/__init__.py,
# which eagerly pulls in llm_client -> httpx -> anthropic, none of which are
# present in the dependency-light CI guard job -- so this test ImportError'd
# there and the whole Signature-lockstep job went red on 2026-07-31 and stayed
# red, taking every other guard in that job down with it.
#
# Standing in a namespace-package shell for `agents` lets the two real submodules
# load without running the package __init__. Same intent as the path-loading in
# source_anomaly_test and content_rendering_test.
if "agents" not in sys.modules:
    _pkg = types.ModuleType("agents")
    _pkg.__path__ = [str(REPO_ROOT / "agents")]
    sys.modules["agents"] = _pkg
    importlib.import_module("agents.cost_tracker")
    importlib.import_module("agents.replay_taxonomy")

from generators.replay_generator import ReplayGenerator


# Verbatim from the 2026-07-31 run that lost its replay.
DEEPMIND_URL = (
    "https://deepmind.google/blog/gemini-robotics-er-2-powering-robotics-with-"
    "video-understanding-task-orchestration-and-multi-robot-collaboration/"
)

# Real slugs pulled from published web/data across the corpus. Every one of these
# tripped the old pattern.
INNOCENT_SLUGS = (
    DEEPMIND_URL,
    "multitask-machine-learning-for-protein-design",
    "sk-hynix-plan-590-billion-chip-investment-as-ai-demand-sends-memory-prices-soaring",
    "risk-thresholds-need-intermediate-warning-levels",
    "https://example.com/ai/task-orchestration-and-multi-robot-collaboration",
    "a disk-space-and-memory-bandwidth-bound workload",
)

# Shapes that are genuinely credentials and must never reach the public site.
REAL_KEYS = (
    "sk-ant-api03-" + "A" * 95,
    "sk-proj-" + "B" * 48,
    "sk-" + "C" * 48,
    "sk-or-v1-" + "d" * 64,
    "sk-test-51HxYzDemoSecret987",
)


class SecretPatternTests(unittest.TestCase):
    """The gate's precision, asserted from both directions."""

    def test_url_slug_does_not_trip_the_gate(self):
        """The exact string that killed the 2026-07-31 replay."""
        ReplayGenerator._assert_publishable({"prompt": DEEPMIND_URL}, what="prompt artifact")

    def test_kebab_case_prose_is_not_a_credential(self):
        for slug in INNOCENT_SLUGS:
            with self.subTest(slug=slug[:60]):
                ReplayGenerator._assert_publishable({"text": slug})

    def test_real_key_shapes_are_still_caught(self):
        for key in REAL_KEYS:
            with self.subTest(key=key[:24]):
                with self.assertRaises(ValueError):
                    ReplayGenerator._assert_publishable({"text": f"the key is {key} ok"})

    def test_other_credential_patterns_are_untouched(self):
        for blob in (
            {"h": "Authorization: Bearer abc123def456"},
            {"h": "api_key=super-secret-value"},
            {"h": "auth-token: hunter2hunter2"},
            {"u": "https://user:pass@internal.example.com/x"},
        ):
            with self.subTest(blob=blob):
                with self.assertRaises(ValueError):
                    ReplayGenerator._assert_publishable(blob)

    def test_private_host_is_still_caught(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_BASE": "https://secret.internal.corp"}):
            with self.assertRaises(ValueError) as caught:
                ReplayGenerator._assert_publishable({"note": "calling secret.internal.corp now"})
            self.assertIn("ANTHROPIC_API_BASE", str(caught.exception))

    def test_loopback_host_is_ignored(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_BASE": "http://localhost:8080"}):
            ReplayGenerator._assert_publishable({"note": "localhost is not a secret"})


class BlastRadiusTests(unittest.TestCase):
    """A tainted prompt artifact must not take the index and stream with it."""

    def test_prompt_violation_drops_only_the_prompts(self):
        generator = ReplayGenerator("/tmp/does-not-need-to-exist")
        index = {"run": {"date": "2026-07-31"}, "calls": []}
        stream = b"stream-bytes"
        tainted = gzip.compress(
            json.dumps({"prompts": [{"system": "key sk-ant-api03-" + "A" * 95}]}).encode("utf-8")
        )

        kept_index, kept_stream, kept_prompts = generator._gate_artifacts(index, stream, tainted)

        self.assertIs(kept_index, index, "the index must survive a prompt-only violation")
        self.assertEqual(kept_stream, stream, "the stream must survive a prompt-only violation")
        self.assertIsNone(kept_prompts, "the tainted prompt artifact must be dropped")

    def test_clean_prompts_are_kept(self):
        generator = ReplayGenerator("/tmp/does-not-need-to-exist")
        index = {"run": {"date": "2026-07-31"}, "calls": []}
        clean = gzip.compress(json.dumps({"prompts": [{"system": DEEPMIND_URL}]}).encode("utf-8"))

        _, _, kept_prompts = generator._gate_artifacts(index, b"s", clean)

        self.assertEqual(kept_prompts, clean)

    def test_tainted_index_still_aborts_everything(self):
        """An index violation is not survivable -- the index *is* the artifact."""
        generator = ReplayGenerator("/tmp/does-not-need-to-exist")
        with self.assertRaises(ValueError):
            generator._gate_artifacts({"leak": "sk-ant-api03-" + "A" * 95}, b"s", None)


if __name__ == "__main__":
    unittest.main()
