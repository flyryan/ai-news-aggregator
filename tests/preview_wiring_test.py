"""Guard: the three layers of the preview pipeline must derive the same paths.

Context / why this exists
-------------------------
Preview generation crosses three components that never import each other:

  1. PreviewManager (admin_service/preview.py) creates previews/<job>/web/
  2. aatf-hero-regen@<date>.service hardcodes --web-dir previews/hero-<date>/web
  3. scripts/promote_preview.sh publishes previews/<job>/web/data/<date>

The first version of this feature drifted exactly here: job ids carried a
random uuid suffix while the unit's path did not, so hero output landed in a
sibling directory no preview ever listed. The operator saw the seeded LIVE
data under a draft banner, and promote reported "nothing to publish". Nothing
crashed; the wiring was simply not connected.

The same shape of drift applies to the publishable-file allowlist, which
exists twice on purpose (the promote script must not trust the panel process)
and therefore must be pinned equal.

  python3 -m unittest tests.preview_wiring_test -v
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UNITS_DIR = REPO_ROOT / "deploy" / "units"
HERO_UNIT = UNITS_DIR / "aatf-hero-regen@.service"
PROMOTE_UNIT = UNITS_DIR / "aatf-promote@.service"
PROMOTE_SCRIPT = REPO_ROOT / "scripts" / "promote_preview.sh"
WRAPPER = REPO_ROOT / "deploy" / "aatf-admin-trigger"
SUDOERS = REPO_ROOT / "deploy" / "aatf-admin.sudoers"


def _manager(tmp: str):
    from admin_service.config import AdminSettings
    from admin_service.preview import PreviewManager
    from admin_service.store import AdminStore

    settings = AdminSettings(
        cf_team_domain="t.cloudflareaccess.com",
        cf_aud="aud",
        allowed_emails=frozenset({"op@example.com"}),
        allowed_service_tokens=frozenset(),
        host="127.0.0.1",
        port=8200,
        state_db=Path(tmp) / "admin.sqlite3",
        repo_dir=REPO_ROOT,
        site_dir=Path(tmp) / "site",
        github_token="",
        github_repo="example/example",
    )
    return PreviewManager(settings, AdminStore(settings.state_db))


class JobIdMatchesHeroUnitTest(unittest.TestCase):
    def test_manager_layout_matches_the_unit_template(self):
        # Non-comment lines only: the unit's comments also mention --web-dir.
        exec_body = "\n".join(
            line for line in HERO_UNIT.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        match = re.search(r"--web-dir\s+(\S+)", exec_body)
        self.assertIsNotNone(match, "hero unit must redirect output with --web-dir")
        unit_path = match.group(1)  # .../previews/hero-%i/web

        date = "2026-01-02"
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            preview = manager.create("hero", date)
            web_dir = manager.web_dir(preview.job_id)

        expected_suffix = unit_path.replace("%i", date).split("/previews/", 1)[1]
        actual_suffix = str(web_dir).split("/previews/", 1)[1]
        self.assertEqual(
            expected_suffix, actual_suffix,
            "aatf-hero-regen@.service and PreviewManager derive DIFFERENT paths "
            "from the same date. The unit writes where no preview looks: the "
            "operator then previews the seeded live data and approves something "
            "that was never generated. Job ids must be exactly <kind>-<date>.",
        )

    def test_recreating_a_preview_replaces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            first = manager.create("hero", "2026-01-02")
            marker = manager.web_dir(first.job_id) / "data" / "2026-01-02" / "hero.webp"
            marker.write_bytes(b"old draft")
            second = manager.create("hero", "2026-01-02")
            self.assertEqual(first.job_id, second.job_id)
            self.assertFalse(
                marker.exists(),
                "create() must replace an existing draft, not accumulate stale "
                "files a later promote would publish",
            )


class AllowlistLockstepTest(unittest.TestCase):
    def test_promote_script_allowlist_equals_publishable(self):
        from admin_service.preview import PUBLISHABLE

        body = PROMOTE_SCRIPT.read_text()
        block = re.search(r"# BEGIN PUBLISHABLE\n(.*?)# END PUBLISHABLE", body, re.S)
        self.assertIsNotNone(block, "promote script must mark its allowlist block")
        names = re.findall(r"^\s{4}([\w.\-]+)$", block.group(1), re.M)
        self.assertEqual(
            list(PUBLISHABLE), names,
            "admin_service/preview.py PUBLISHABLE and scripts/promote_preview.sh "
            "define different allowlists. They are duplicated on purpose -- the "
            "script cannot trust the panel process -- so they must be identical, "
            "or a file seeds into previews that promote silently drops.",
        )


class PromoteActionWiringTest(unittest.TestCase):
    def test_promote_unit_exists_and_is_shaped_right(self):
        body = PROMOTE_UNIT.read_text()
        self.assertIn("User=ubuntu", body, "promotion signs and pushes as ubuntu")
        self.assertIn("flock", body, "promotion must serialise on the privileged lock")
        self.assertIn("promote_preview.sh %i", body)

    def test_wrapper_validates_promote_job_ids(self):
        for args, expected in (
            (["promote", "hero-2026-01-01"], None),  # only sudo/systemctl missing here
            (["promote", "hero-2026-01-01; id"], 3),
            (["promote", "../../etc"], 3),
            (["promote", "hero-notadate"], 3),
            (["promote"], 3),
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(WRAPPER), *args], capture_output=True, text=True
                )
                if expected is None:
                    # Valid input proceeds past validation; on a dev machine the
                    # systemctl call then fails, which is fine -- what matters
                    # is that it was NOT rejected as malformed (exit 3).
                    self.assertNotIn(result.returncode, (2, 3), result.stderr)
                else:
                    self.assertEqual(expected, result.returncode, result.stderr)

    def test_sudoers_constrains_promote_arguments(self):
        lines = [
            line for line in SUDOERS.read_text().splitlines()
            if "promote" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(2, len(lines), "expected one promote rule per preview kind")
        for line in lines:
            self.assertIn(
                "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]", line,
                "the promote job id must be constrained by a character-class glob "
                "so sudo refuses injection before the wrapper runs",
            )

    def test_promote_script_refuses_bad_job_ids(self):
        for job in ("hero-2026-01-01; id", "../../etc", "hero-notadate", ""):
            with self.subTest(job=job):
                result = subprocess.run(
                    ["bash", str(PROMOTE_SCRIPT), job],
                    capture_output=True, text=True,
                    env={"PATH": "/usr/bin:/bin", "PROMOTE_REPO_DIR": "/nonexistent",
                         "PROMOTE_PREVIEWS_ROOT": "/nonexistent"},
                )
                self.assertEqual(3, result.returncode, f"{job!r}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
