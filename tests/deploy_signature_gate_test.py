"""Security guard: no shipped script may hard-reset to a remote ref unverified.

Context / why this exists
-------------------------
`scripts/deploy.sh` enforces a CWE-345 signed-commit gate: it runs
`git verify-commit` on the tip of origin/main against an allow-list and
refuses to deploy an unsigned commit. But `scripts/post_pipeline_verify.sh`
operated on the SAME host checkout and ran

    git fetch origin && git reset --hard origin/main && docker compose ... --build

with no verification at all, then built and ran the result. Anyone able to
move `flyryan/main` got code execution on the origin host via the *verification*
path, even though the deploy path would have refused the same commit.

This guard locks in the fix: any script that resets to a remote-tracking ref
must route through `scripts/verified_sync.sh`, which performs the
`git verify-commit` check and resets to the exact verified hash.

Stdlib-only (re + unittest), matching the repo's other guard tests:

  python3 -m unittest tests.deploy_signature_gate_test -v
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

# `git reset --hard <remote>/<branch>` — resetting to a remote ref means adopting
# whatever a push put there, which is exactly what must be gated.
RESET_TO_REMOTE = re.compile(r"git\s+reset\s+--hard\s+[\"']?(origin|upstream)/", re.I)

# The one script allowed to contain it, because it is the thing doing the verifying.
VERIFIER = "verified_sync.sh"


def _shell_scripts():
    return sorted(p for p in SCRIPTS.glob("*.sh") if p.is_file())


class DeploySignatureGateTest(unittest.TestCase):
    def test_verifier_script_exists(self):
        self.assertTrue(
            (SCRIPTS / VERIFIER).is_file(),
            f"scripts/{VERIFIER} must exist; it is the single implementation of the "
            "signed-commit gate that every sync path routes through.",
        )

    def test_verifier_actually_verifies(self):
        body = (SCRIPTS / VERIFIER).read_text()
        self.assertIn(
            "git verify-commit",
            body,
            f"scripts/{VERIFIER} must call `git verify-commit`; without it the "
            "shared helper is a rename of the bug, not a fix.",
        )
        self.assertIn(
            "allowedSignersFile",
            body,
            f"scripts/{VERIFIER} must configure gpg.ssh.allowedSignersFile, or "
            "`git verify-commit` has no trust anchor and cannot reject anything.",
        )

    def test_verifier_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS / VERIFIER)], capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_no_script_resets_to_remote_without_the_verifier(self):
        offenders = []
        for script in _shell_scripts():
            if script.name == VERIFIER:
                continue
            text = script.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if RESET_TO_REMOTE.search(line):
                    offenders.append(f"{script.name}:{lineno}: {stripped[:90]}")
        self.assertEqual(
            [],
            offenders,
            "These lines reset the working tree to a remote ref without the signature "
            "gate. Route them through scripts/verified_sync.sh instead:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
