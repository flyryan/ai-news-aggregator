"""Lockstep guard for the three `call_with_thinking` overloads.

Root cause of the 2026-06-21 RED pipeline run: commit 4b44a03 added the
`full_output_budget` kwarg to TWO of the three `call_with_thinking` definitions
in agents/llm_client.py (the concrete sync + async clients) but NOT the third
(the `AsyncLLMRouter` dispatcher that the call sites actually hit). The router
forwards a FIXED kwargs dict via `_call_with_failover`, so the extra kwarg blew
up every reduce/topic/exec-summary call and aborted the pipeline.

Nothing enforced that the three signatures stay in lockstep. This test does,
using only the stdlib `ast` module (no project imports, no network, no API key)
so it runs anywhere and can't be defeated by an import-time failure.

What it asserts:
  1. There are exactly three `call_with_thinking` definitions.
  2. Every keyword-style parameter that appears in ANY definition is accepted by
     ALL definitions (so a call site passing a shared kwarg can never hit a
     signature that rejects it).
  3. The `AsyncLLMRouter.call_with_thinking` body forwards every one of its own
     keyword params into the dict it hands to `_call_with_failover` (so a param
     can't be silently dropped on the way to the concrete client).
"""

import ast
import unittest
from pathlib import Path

LLM_CLIENT = Path(__file__).resolve().parent.parent / "agents" / "llm_client.py"

# Parameters that are intentionally local to a single overload and must NOT be
# required across all three. `self` is implicit; `routing_context` exists only on
# the concrete async client; `caller` exists on the async client + router but not
# the sync client. Keep this allowlist tight and documented.
PER_OVERLOAD_OK = {"self", "routing_context", "caller"}


def _keyword_params(func: ast.AST) -> set:
    """Return the set of parameter names usable as keywords for a function def."""
    args = func.args
    names = set()
    for a in args.args:           # positional-or-keyword
        names.add(a.arg)
    for a in args.kwonlyargs:     # keyword-only
        names.add(a.arg)
    return names


class CallWithThinkingSignatureLockstep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(LLM_CLIENT.read_text(encoding="utf-8"), filename=str(LLM_CLIENT))
        cls.defs = [
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "call_with_thinking"
        ]

    def test_exactly_three_overloads(self):
        self.assertEqual(
            len(self.defs),
            3,
            f"expected 3 call_with_thinking defs, found {len(self.defs)} "
            f"(lines {[d.lineno for d in self.defs]}). If you added/removed one, "
            "update this guard and keep all signatures in lockstep.",
        )

    def test_shared_kwargs_accepted_by_all_overloads(self):
        param_sets = [_keyword_params(d) for d in self.defs]
        # The union of every keyword param, minus the per-overload allowlist, is
        # the set of "shared" kwargs every overload must accept.
        shared = set().union(*param_sets) - PER_OVERLOAD_OK
        for d, params in zip(self.defs, param_sets):
            missing = shared - params
            self.assertEqual(
                missing,
                set(),
                f"call_with_thinking at line {d.lineno} is missing shared kwarg(s) "
                f"{sorted(missing)}. All three overloads (incl. the AsyncLLMRouter "
                "dispatcher) must accept the same keyword args, or call sites that "
                "pass them will crash on the overload that doesn't.",
            )

    def test_router_forwards_all_its_params(self):
        """The router must forward every keyword param it declares into the
        dict it passes to `_call_with_failover` — otherwise a param is accepted
        then silently dropped (the subtle half of the 4b44a03 bug class)."""
        # Identify the router overload: the one whose body calls _call_with_failover.
        router = None
        for d in self.defs:
            for node in ast.walk(d):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_call_with_failover"
                ):
                    router = d
                    router_call = node
                    break
            if router is not None:
                break

        self.assertIsNotNone(
            router,
            "could not find the call_with_thinking overload that calls "
            "_call_with_failover (the AsyncLLMRouter dispatcher).",
        )

        # The forwarded kwargs dict is the 2nd positional arg to _call_with_failover.
        self.assertGreaterEqual(
            len(router_call.args),
            2,
            "_call_with_failover should be called as (method_name, kwargs_dict).",
        )
        kwargs_dict = router_call.args[1]
        self.assertIsInstance(
            kwargs_dict,
            ast.Dict,
            "the kwargs forwarded to _call_with_failover must be a dict literal "
            "so this guard can verify each param is forwarded.",
        )
        forwarded_keys = {
            k.value for k in kwargs_dict.keys if isinstance(k, ast.Constant)
        }

        declared = _keyword_params(router) - {"self"}
        not_forwarded = declared - forwarded_keys
        self.assertEqual(
            not_forwarded,
            set(),
            f"AsyncLLMRouter.call_with_thinking declares param(s) {sorted(not_forwarded)} "
            "but does not forward them into the _call_with_failover kwargs dict. "
            "Add each to the forwarded dict so the concrete client actually receives them.",
        )


if __name__ == "__main__":
    unittest.main()
