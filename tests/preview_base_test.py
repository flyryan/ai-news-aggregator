"""Guard: preview must not be able to silently render live data.

Context / why this exists
-------------------------
An earlier admin service had a `preview.ts` that read an injected global and,
when it was missing, fell back to '/data'. Two things made that dangerous:

1. The built pages carry per-page CSP hashes, so the inline <script> that was
   supposed to set the global was blocked outright -- the global was ALWAYS
   missing.
2. The fallback meant the preview then rendered the LIVE report while the UI
   labelled it a draft.

An operator could approve a report they had never actually seen. This guard
pins the two rules that prevent it: every data fetch goes through one resolver,
and that resolver throws on a malformed base rather than guessing.

  python3 -m unittest tests.preview_base_test -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES = REPO_ROOT / "frontend" / "src" / "lib" / "services"
RESOLVER = SERVICES / "dataBase.ts"

# A '/data/' literal that is NOT wrapped in dataUrl(...). Wrapping is the whole
# point -- `fetch(dataUrl('/data/x'))` is correct and must not be flagged, while
# `fetch('/data/x')` pins that call to the live tree even inside a preview.
HARDCODED = re.compile(r"""(?<!dataUrl\()(['"`])/data/""")

# searchWorker runs without a `document`, so it cannot read the attribute. It
# receives the base in its init message and interpolates it, which is correct.
WORKER_EXEMPT = "searchWorker.ts"


class ResolverTest(unittest.TestCase):
    def test_resolver_exists(self):
        self.assertTrue(RESOLVER.is_file(), "frontend/src/lib/services/dataBase.ts must exist")

    def test_resolver_throws_on_a_malformed_base(self):
        body = RESOLVER.read_text()
        self.assertIn(
            "throw", body,
            "the resolver must throw on an invalid base. Falling back to /data is "
            "exactly the bug this guard exists to prevent: it renders live data "
            "under a draft label.",
        )

    def test_resolver_reads_a_data_attribute_not_a_global(self):
        body = RESOLVER.read_text()
        self.assertIn(
            "dataset", body,
            "the base must arrive via a data-* attribute; an injected inline "
            "script is blocked by the per-page CSP hash and would never run",
        )

    def test_resolver_rejects_cross_origin_bases(self):
        body = RESOLVER.read_text()
        self.assertIn(
            "startsWith('//')", body,
            "a protocol-relative base would point the page at another origin's data",
        )


class NoHardcodedPathsTest(unittest.TestCase):
    def test_no_service_hardcodes_the_data_root(self):
        offenders = []
        for path in sorted(SERVICES.glob("*.ts")):
            if path.name in ("dataBase.ts", WORKER_EXEMPT):
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("*") or stripped.startswith("//"):
                    continue  # prose in comments is fine
                if HARDCODED.search(line):
                    offenders.append(f"{path.name}:{lineno}: {stripped[:80]}")
        self.assertEqual(
            [], offenders,
            "these fetches bypass the data-base resolver, so they would load LIVE "
            "data inside a preview:\n  " + "\n  ".join(offenders),
        )

    def test_worker_receives_the_base_instead_of_hardcoding_it(self):
        body = (SERVICES / WORKER_EXEMPT).read_text()
        self.assertIn(
            "dataBase", body,
            "searchWorker has no document, so it must take the base from its init "
            "message rather than assuming /data",
        )
        self.assertNotIn(
            "fetch('/data/", body,
            "searchWorker still hardcodes the live corpus path",
        )


if __name__ == "__main__":
    unittest.main()
