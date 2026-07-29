"""Security guard: the privileged-action path must not be widenable.

Context / why this exists
-------------------------
The admin panel runs as `aatfadmin`, a user deliberately kept out of the
`docker` group -- on this host `ubuntu` is in both `sudo` and `docker`, which
is effective root, and the panel holds a GitHub token that can spend money.
The only privilege `aatfadmin` has is one sudo entry pointing at one wrapper
script.

That makes two things load-bearing:

1. The sudoers entry must name exact commands. A rule like
   `systemctl start aatf-*` lets a caller start any unit whose name they can
   arrange to exist.
2. The wrapper must validate its action against a fixed allowlist and must not
   interpolate caller input into a shell. The date argument for hero-regen
   reaches a systemd instance name; `2026-01-01; rm -rf /` must be rejected by
   pattern, not escaped and hoped for.

Both layers were verified against real sudo on the host: the glob denies
injection, traversal, non-dates, and extra arguments before the wrapper runs.

  python3 -m unittest tests.admin_actions_test -v
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "deploy" / "aatf-admin-trigger"
SUDOERS = REPO_ROOT / "deploy" / "aatf-admin.sudoers"
UNITS_DIR = REPO_ROOT / "deploy" / "units"

EXPECTED_ACTIONS = {"rebuild-web", "git-sync", "hero-regen", "admin-redeploy"}


def _sudoers_command_lines():
    lines = []
    for line in SUDOERS.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


class SudoersTest(unittest.TestCase):
    def test_sudoers_exists(self):
        self.assertTrue(SUDOERS.is_file(), "deploy/aatf-admin.sudoers must exist")

    def test_sudoers_grants_only_the_wrapper_and_readonly_status(self):
        for line in _sudoers_command_lines():
            command = line.split("NOPASSWD:", 1)[-1].strip()
            allowed = command.startswith("/usr/local/sbin/aatf-admin-trigger") or (
                command.startswith("/usr/bin/systemctl show ")
            )
            self.assertTrue(
                allowed,
                f"sudoers grants something beyond the wrapper and read-only status: "
                f"{command!r}. Every privileged path must funnel through the one "
                "validated script.",
            )

    def test_sudoers_has_no_star_wildcards(self):
        # `[0-9]` constrains; `*` does not. A star in the command turns an exact
        # allowlist into a pattern match over anything the caller can name.
        for line in _sudoers_command_lines():
            self.assertNotIn(
                "*", line,
                f"wildcard in sudoers command: {line!r}. Enumerate the permitted "
                "invocations, or constrain with character classes.",
            )

    def test_sudoers_does_not_grant_all(self):
        self.assertNotRegex(
            SUDOERS.read_text(), r"NOPASSWD:\s*ALL",
            "NOPASSWD: ALL is unrestricted root for the panel user",
        )

    def test_hero_regen_argument_is_shaped_like_a_date(self):
        hero_lines = [l for l in _sudoers_command_lines() if "hero-regen" in l]
        self.assertEqual(1, len(hero_lines), "expected exactly one hero-regen rule")
        self.assertIn(
            "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]", hero_lines[0],
            "the hero-regen date must be constrained by a character-class glob so "
            "sudo refuses injection before the wrapper runs",
        )


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.body = WRAPPER.read_text()

    def test_wrapper_is_executable_bash_with_strict_mode(self):
        self.assertTrue(self.body.startswith("#!/bin/bash"))
        self.assertIn("set -euo pipefail", self.body)

    def test_wrapper_enumerates_exactly_the_known_actions(self):
        for action in EXPECTED_ACTIONS:
            self.assertIn(action, self.body, f"wrapper does not handle {action!r}")

    def test_wrapper_validates_the_date_argument(self):
        self.assertIn(
            "[0-9]{4}-[0-9]{2}-[0-9]{2}", self.body,
            "wrapper must validate the hero date against a YYYY-MM-DD pattern",
        )

    def test_wrapper_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(WRAPPER)], capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_wrapper_rejects_unknown_actions_before_touching_systemctl(self):
        case_pos = self.body.find("case")
        systemctl_pos = self.body.find("systemctl")
        self.assertNotEqual(-1, case_pos, "wrapper must dispatch via case")
        self.assertLess(
            case_pos, systemctl_pos,
            "the action allowlist must be evaluated before any systemctl call",
        )

    def test_wrapper_actually_refuses_bad_input(self):
        for args, expected in (
            (["bogus"], 2),
            (["hero-regen", "2026-01-01; rm -rf /"], 3),
            (["hero-regen", "../../etc"], 3),
            (["hero-regen", "notadate"], 3),
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(WRAPPER), *args], capture_output=True, text=True
                )
                self.assertEqual(
                    expected, result.returncode,
                    f"{args} should exit {expected}, got {result.returncode}",
                )


class UnitsTest(unittest.TestCase):
    def test_every_action_has_a_unit(self):
        names = {p.name for p in UNITS_DIR.glob("*.service")}
        expected = {
            "aatf-rebuild-web.service",
            "aatf-git-sync.service",
            "aatf-hero-regen@.service",
            "aatf-admin-redeploy.service",
        }
        self.assertEqual(expected, names)

    def test_privileged_units_take_the_lock(self):
        # admin-redeploy is exempt: it restarts the admin service and would
        # deadlock against a lock that service is waiting on.
        for name in ("aatf-rebuild-web.service", "aatf-git-sync.service",
                     "aatf-hero-regen@.service"):
            body = (UNITS_DIR / name).read_text()
            self.assertIn(
                "flock", body,
                f"{name} must serialise via flock or two actions can race on the "
                "same checkout -- a git reset under a running build",
            )

    def test_rebuild_is_scoped_to_the_service_name(self):
        # Check ExecStart lines only. The comments deliberately discuss
        # `up -d --build` to explain why it is not used, and matching prose
        # would fail the test for saying the right thing.
        exec_lines = [
            line for line in (UNITS_DIR / "aatf-rebuild-web.service").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        body = "\n".join(exec_lines)
        self.assertIn("ai-news-aggregator", body)
        self.assertNotIn(
            "up -d --build", body,
            "unscoped `up -d --build` rebuilds every service in the project and "
            "can tear down a working container before failing to replace it",
        )
        self.assertIn(
            "build ai-news-aggregator", body,
            "the build step must name the service, or a second compose service "
            "added later gets rebuilt silently",
        )

    def test_hero_unit_writes_to_the_preview_tree(self):
        body = (UNITS_DIR / "aatf-hero-regen@.service").read_text()
        self.assertIn(
            "--web-dir", body,
            "hero regeneration must be redirected to a preview tree; hero.webp is "
            "git-tracked and a direct write is reverted by the next deploy",
        )
        self.assertIn("previews/", body)
        self.assertIn(
            " -y", body,
            "regenerate_hero.py prompts for confirmation; without -y the unit hangs",
        )


if __name__ == "__main__":
    unittest.main()
