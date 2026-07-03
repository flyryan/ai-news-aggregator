"""Security guard: the shipped deploy-webhook example must authenticate callers.

Context / why this exists
-------------------------
Finding #1247 (CWE-306, HIGH): `webhook/hooks.example.json` shipped the
`adnanh/webhook` `deploy` hook with NO `trigger-rule`. adnanh/webhook treats a
missing trigger-rule as match-all, so ANY unauthenticated request to
`:9000/hooks/deploy` executed `scripts/deploy.sh` on the host (git reset --hard,
git clean -fd, force-push to the corporate mirror -> RCE-adjacent). The README
told operators to copy this file verbatim and edit paths only, so the documented
production config was unauthenticated by default.

This guard locks in the fix: every hook in the example config must be gated by a
`payload-hmac-sha256` match against the GitHub `X-Hub-Signature-256` header, so a
caller who does not hold the shared secret cannot trigger the deploy command.

Stdlib-only (json + unittest), matching the repo's other guard tests so it runs
in CI without pytest or any extra deps:

  python3 -m unittest tests.webhook_hook_auth_test -v
"""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_EXAMPLE = REPO_ROOT / "webhook" / "hooks.example.json"

# The header GitHub signs push payloads with; the hook must verify it.
SIGNATURE_HEADER = "X-Hub-Signature-256"


def _iter_matches(rule):
    """Yield every `match` object found anywhere inside a trigger-rule tree.

    adnanh/webhook trigger-rules nest via `and` / `or` / `not` combinators, each
    wrapping child rules; leaves are `{"match": {...}}`.
    """
    if not isinstance(rule, dict):
        return
    if "match" in rule and isinstance(rule["match"], dict):
        yield rule["match"]
    for combinator in ("and", "or"):
        children = rule.get(combinator)
        if isinstance(children, list):
            for child in children:
                yield from _iter_matches(child)
    if isinstance(rule.get("not"), dict):
        yield from _iter_matches(rule["not"])


def _has_hmac_signature_match(hook):
    """True iff the hook's trigger-rule verifies the HMAC-SHA256 request signature."""
    rule = hook.get("trigger-rule")
    if not isinstance(rule, dict):
        return False
    for match in _iter_matches(rule):
        if match.get("type") != "payload-hmac-sha256":
            continue
        if not match.get("secret"):
            continue
        parameter = match.get("parameter") or {}
        if (
            parameter.get("source") == "header"
            and str(parameter.get("name", "")).lower() == SIGNATURE_HEADER.lower()
        ):
            return True
    return False


class WebhookHookAuthTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            HOOKS_EXAMPLE.exists(),
            f"expected example hook config at {HOOKS_EXAMPLE}",
        )
        self.hooks = json.loads(HOOKS_EXAMPLE.read_text())
        self.assertIsInstance(self.hooks, list)
        self.assertTrue(self.hooks, "hooks.example.json must define at least one hook")

    def test_every_hook_requires_hmac_signature(self):
        """No hook may execute a command without verifying the caller's signature."""
        for hook in self.hooks:
            hook_id = hook.get("id", "<unnamed>")
            self.assertTrue(
                _has_hmac_signature_match(hook),
                f"hook {hook_id!r} has no payload-hmac-sha256 trigger-rule against "
                f"{SIGNATURE_HEADER}; an unauthenticated request would trigger "
                f"{hook.get('execute-command')!r} (CWE-306, finding #1247).",
            )

    def test_secret_is_not_left_blank(self):
        """A present-but-empty secret would still authenticate every caller."""
        for hook in self.hooks:
            rule = hook.get("trigger-rule") or {}
            for match in _iter_matches(rule):
                if match.get("type") == "payload-hmac-sha256":
                    self.assertTrue(
                        str(match.get("secret", "")).strip(),
                        f"hook {hook.get('id')!r} has an empty HMAC secret",
                    )


if __name__ == "__main__":
    unittest.main()
