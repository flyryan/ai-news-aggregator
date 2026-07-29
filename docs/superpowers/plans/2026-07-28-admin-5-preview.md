# Preview and Promote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator review a regenerated hero or a dry-run pipeline report exactly as readers would see it, on the admin origin, and promote it to the live site with one explicit, signed, auditable step.

**Architecture:** Preview data lives in gitignored `data/admin/previews/<job>/web/data/`, so `git reset --hard` and `git clean -fd` cannot destroy it and the public origin never serves an unapproved byte. The admin service mounts the real built SPA bundle and serves each preview at `/preview/<job>/`, injecting a data base path through a `data-*` attribute on `<body>` — not an inline script, which the per-page CSP hash would block. The frontend reads that attribute and prefixes every data fetch; when the attribute is absent it uses `/data` as it does today, and when it is *malformed* it throws rather than falling back.

**Tech Stack:** Python 3.11+, FastAPI, SvelteKit 2, Svelte 5, stdlib `unittest`, git.

## Global Constraints

- **Preview must never silently render live data.** The abandoned branch's `preview.ts` fell back to `/data` when its injected global was missing, so a preview would show the live report while labelled a draft, and an operator could approve something they never saw. Absent attribute → normal live browsing. Present-but-invalid → hard error.
- **No inline `<script>` injection.** Each built page carries its own CSP hash (`web/index.html` has `sha256-CXSEFFaI1aXvpFRAue+e+fnImVPL2oqjZkossFtyluU=`, `archive.html` a different one), so an added inline script is blocked and the global would never be set. Use a `data-*` attribute on the existing `<body>` tag.
- **Preview is served only from the admin origin**, which is behind Cloudflare Access. CSP `frame-ancestors 'self'` (`nginx.conf:66`) means same-origin is also the only way the preview can be framed.
- **Promotion writes an SSH-signed commit.** `scripts/deploy.sh:27-35` refuses an unsigned tip, so an unsigned promotion silently freezes the site on stale content. Fail at approval time instead.
- **Preview banner on every preview view.** The `/replay` demo banner (`replay/+page.svelte:301-305`) is the precedent; this one must be louder, because the cost of confusion is publishing the wrong thing.
- Retention: previews are reaped after 7 days and on promotion. The prior attempt left **2.4 GB** of orphaned git worktrees with no cleanup.
- Commits must be SSH-signed.

---

## Prerequisites

Plans 2 (service, store, auth), 3 (actions, `aatf-hero-regen@.service`), and 4 (panel shell, `adminApi`) must be complete.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/lib/services/dataBase.ts` (create) | Resolve the data base path once, fail loudly on a bad value. |
| `frontend/src/lib/services/dataLoader.ts` (modify `:21,48,67`) | Use the resolved base. |
| `frontend/src/lib/services/replayLoader.ts` (modify `:15,19`) | Use the resolved base. |
| `frontend/src/lib/services/searchIndex.ts` (modify `:61`) | Use the resolved base. |
| `frontend/src/lib/services/searchWorker.ts` (modify `:43`) | Use the resolved base. |
| `frontend/src/routes/+page.svelte` (modify) | Rewrite the hero URL through the base. |
| `admin_service/preview.py` (create) | Create, list, serve, promote, and reap previews. |
| `admin_service/app.py` (modify) | Preview endpoints and the static preview mount. |
| `frontend/src/lib/components/admin/PreviewPanel.svelte` (create) | Preview list, compare, approve. |
| `tests/preview_base_test.py` (create) | Guard: no service file may hardcode `/data`, and the base never silently falls back. |
| `scripts/deploy.sh` (modify) | Take the shared `flock` (carried from plans 3 and 4). |

---

### Task 1: The data base resolver

**Files:**
- Create: `frontend/src/lib/services/dataBase.ts`
- Test: `tests/preview_base_test.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `dataBase(): string` (no trailing slash, `''` when live), `dataUrl(path: string): string`, `isPreview(): boolean`, `previewLabel(): string | null`.

- [ ] **Step 1: Write the failing guard test**

Create `tests/preview_base_test.py`:

```python
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

# A literal '/data' inside a fetch template or string, which would bypass the
# resolver and pin that call to the live tree.
HARDCODED = re.compile(r"""(['"`])/data/""")


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


class NoHardcodedPathsTest(unittest.TestCase):
    def test_no_service_hardcodes_the_data_root(self):
        offenders = []
        for path in sorted(SERVICES.glob("*.ts")):
            if path.name == "dataBase.ts":
                continue  # the resolver is allowed to name the default
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.preview_base_test -v`

Expected: FAIL — the resolver does not exist, and `NoHardcodedPathsTest` lists exactly these seven call sites (verified against the current tree):

```
dataLoader.ts:21, dataLoader.ts:48, dataLoader.ts:67,
replayLoader.ts:15, replayLoader.ts:19,
searchIndex.ts:61, searchWorker.ts:43
```

`replayLoader.ts:5` mentions `/data/...` in a comment and is correctly skipped — the
guard ignores comment lines, because prose describing the convention is not a call site.

- [ ] **Step 3: Write the resolver**

Create `frontend/src/lib/services/dataBase.ts`:

```ts
/**
 * Where this page's data lives.
 *
 * Live browsing uses `/data`. A preview sets `data-aatf-data-base` on <body>,
 * and every data fetch is prefixed with it, so the same built bundle renders
 * draft content without a rebuild.
 *
 * The attribute carries the base rather than an injected inline script because
 * each built page has its own CSP hash (`script-src 'self' 'sha256-...'`) --
 * an added inline script is blocked, and a global it was meant to set would
 * never appear.
 *
 * A malformed base THROWS. The predecessor to this file fell back to `/data`,
 * which meant a preview quietly rendered the live report under a draft banner;
 * an operator could approve something they had never seen. Refusing to render
 * is the safe failure.
 */

const ATTRIBUTE = 'aatfDataBase'; // <body data-aatf-data-base="...">
const LABEL_ATTRIBUTE = 'aatfPreviewLabel';

let cached: string | null = null;

function readAttribute(name: string): string | undefined {
	if (typeof document === 'undefined') return undefined;
	return document.body?.dataset?.[name];
}

/** The data root for this page. `''` means live. Never has a trailing slash. */
export function dataBase(): string {
	if (cached !== null) return cached;

	const raw = readAttribute(ATTRIBUTE);
	if (raw === undefined || raw === '') {
		cached = '';
		return cached;
	}

	const value = raw.trim();

	// Same-origin absolute paths only. A preview base is a path on this origin;
	// anything else is either a mistake or an attempt to point the page at
	// someone else's data.
	if (!value.startsWith('/') || value.startsWith('//')) {
		throw new Error(
			`Invalid data base ${JSON.stringify(raw)}: expected an absolute same-origin ` +
				`path like "/preview/abc123". Refusing to load data rather than falling ` +
				`back to live content, which would show the live report inside a preview.`
		);
	}

	cached = value.replace(/\/+$/, '');
	return cached;
}

/** Absolute URL for a data path. Pass paths like `/data/2026-07-28/summary.json`. */
export function dataUrl(path: string): string {
	const base = dataBase();
	if (!base) return path;
	return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export function isPreview(): boolean {
	return dataBase() !== '';
}

export function previewLabel(): string | null {
	return readAttribute(LABEL_ATTRIBUTE) ?? null;
}

/** Testing only: clear the memoised value. */
export function resetDataBaseCache(): void {
	cached = null;
}
```

- [ ] **Step 4: Route every call site through it**

In `frontend/src/lib/services/dataLoader.ts`, add the import and replace the three fetches:

```ts
import { dataUrl } from './dataBase';
```

- Line 21: `const indexUrl = forceRefresh ? dataUrl(`/data/index.json?t=${Date.now()}`) : dataUrl('/data/index.json');`
- Line 48: `const response = await fetch(dataUrl(`/data/${date}/summary.json`));`
- Line 67: `const response = await fetch(dataUrl(`/data/${date}/${category}.json`));`

In `frontend/src/lib/services/replayLoader.ts`, add `import { dataUrl } from './dataBase';` and change the two builders to `return dataUrl(`/data/${date}/replay-index.json`);` and `return dataUrl(`/data/${date}/replay-stream.json.gz`);`.

In `frontend/src/lib/services/searchIndex.ts` line 61: `const response = await fetch(dataUrl('/data/search-corpus.json'));` with the import added.

`frontend/src/lib/services/searchWorker.ts` is a **Web Worker** — it has no `document`, so it cannot read the attribute. Change its fetch to accept the base from the message that starts it:

```ts
// The worker has no document, so the base is passed in with the init message
// rather than read from the DOM.
const response = await fetch(`${corpusBase ?? ''}/data/search-corpus.json`);
```

and add `corpusBase` to the worker's init payload, populated from `dataBase()` on the main thread in `searchIndex.ts`.

- [ ] **Step 5: Rewrite the hero URL**

The hero path comes from `summary.json` as `/data/<date>/hero.webp?v=<mtime>`, so it needs the same treatment. In `frontend/src/routes/+page.svelte`, where `hero_image_url` is passed to `HeroSection`, wrap it:

```svelte
<HeroSection heroImageUrl={summary.hero_image_url ? dataUrl(summary.hero_image_url) : null} ... />
```

with `import { dataUrl } from '$lib/services/dataBase';` added to the script block. Without this a preview shows the **live** hero next to draft text, which is precisely the confusion this feature exists to remove.

- [ ] **Step 6: Run the guard test to verify it passes**

Run: `python3 -m unittest tests.preview_base_test -v`

Expected: all PASS.

- [ ] **Step 7: Type-check**

Run: `cd frontend && npm run check 2>&1 | tail -15`

Expected: no new errors. The 4 pre-existing `vite.config.ts` errors from missing `@types/node` are the known clean baseline.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/services/ frontend/src/routes/+page.svelte tests/preview_base_test.py
git commit -m "frontend: resolve the data root through one place

Preview needs the same bundle to read draft data, so every fetch goes through
dataUrl() and the base arrives on a body data-* attribute. Not an inline
script: each built page carries its own CSP hash, so an injected script is
blocked and the global it sets never exists.

A malformed base throws. The predecessor fell back to /data, which rendered the
LIVE report under a draft banner -- an operator could approve something they
never saw. Refusing to render is the safe failure.

The hero URL comes from summary.json and needed the same treatment, or a
preview would show live art beside draft text. The search worker has no
document, so its base is passed in the init message."
```

---

### Task 2: Preview lifecycle on the server

**Files:**
- Create: `admin_service/preview.py`

**Interfaces:**
- Consumes: `AdminSettings`, `AdminStore`.
- Produces:
  - `@dataclass(frozen=True) Preview(job_id, kind, date, created_at, path, size_bytes)`
  - `class PreviewManager` with `create(kind, date) -> Preview`, `list() -> list[Preview]`, `get(job_id) -> Preview | None`, `promote(job_id, principal) -> list[str]`, `reap(max_age_days=7) -> int`, `seed_from_live(job_id, date)`
  - exception `PreviewError(RuntimeError)`

- [ ] **Step 1: Write the module**

Create `admin_service/preview.py`:

```python
"""Create, serve, and promote previews of unpublished content.

Previews live under the state directory, which is outside the git checkout.
That is not tidiness: scripts/deploy.sh runs `git reset --hard` and
`git clean -fd`, so a preview staged anywhere under web/data is destroyed by
the next push. Keeping drafts off the public origin entirely also means an
unapproved report is not fetchable by URL.

Promotion copies approved files into the checkout and commits them SSH-signed,
because scripts/deploy.sh:27-35 refuses an unsigned tip -- an unsigned
promotion would leave the site frozen on stale content with only a log line to
say why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AdminSettings
from .store import AdminStore

__all__ = ["Preview", "PreviewManager", "PreviewError"]

VALID_KINDS = ("hero", "report")


class PreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class Preview:
    job_id: str
    kind: str
    date: str
    created_at: str
    path: Path
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "date": self.date,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "url": f"/preview/{self.job_id}/",
        }


class PreviewManager:
    def __init__(self, settings: AdminSettings, store: AdminStore) -> None:
        self._settings = settings
        self._store = store
        self._root = settings.state_db.parent / "previews"
        self._root.mkdir(parents=True, exist_ok=True)

    # --- paths ------------------------------------------------------------

    def _job_dir(self, job_id: str) -> Path:
        # job_id is generated here, never taken from a request, but resolve and
        # check anyway: a bad id must not become a path escape.
        candidate = (self._root / job_id).resolve()
        if not str(candidate).startswith(str(self._root.resolve())):
            raise PreviewError("invalid preview id")
        return candidate

    def web_dir(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "web"

    # --- lifecycle --------------------------------------------------------

    def create(self, kind: str, date: str) -> Preview:
        if kind not in VALID_KINDS:
            raise PreviewError(f"unknown preview kind: {kind}")
        if not _is_iso_date(date):
            raise PreviewError(f"invalid date: {date}")

        job_id = f"{kind}-{date}-{uuid.uuid4().hex[:8]}"
        job_dir = self._job_dir(job_id)
        (job_dir / "web" / "data" / date).mkdir(parents=True, exist_ok=True)

        meta = {
            "job_id": job_id,
            "kind": kind,
            "date": date,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (job_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return self._load(job_id)

    def seed_from_live(self, job_id: str, date: str) -> None:
        """Copy the live day into the preview so partial regeneration has context.

        regenerate_hero.py reads the existing summary.json to build its prompt
        (scripts/regenerate_hero.py:228-230), so without this the hero unit
        fails with a missing-summary error.
        """
        live = self._settings.repo_dir / "web" / "data" / date
        if not live.is_dir():
            raise PreviewError(f"no published data for {date}")

        target = self.web_dir(job_id) / "data" / date
        target.mkdir(parents=True, exist_ok=True)
        for name in ("summary.json", "hero.webp"):
            source = live / name
            if source.is_file():
                shutil.copy2(source, target / name)

        # index.json makes the preview navigable the same way the live site is.
        live_index = self._settings.repo_dir / "web" / "data" / "index.json"
        if live_index.is_file():
            shutil.copy2(live_index, self.web_dir(job_id) / "data" / "index.json")

    def list(self) -> list[Preview]:
        previews = []
        for meta_path in sorted(self._root.glob("*/meta.json")):
            try:
                previews.append(self._load(meta_path.parent.name))
            except PreviewError:
                continue
        return sorted(previews, key=lambda p: p.created_at, reverse=True)

    def get(self, job_id: str) -> Preview | None:
        try:
            return self._load(job_id)
        except PreviewError:
            return None

    def _load(self, job_id: str) -> Preview:
        job_dir = self._job_dir(job_id)
        meta_path = job_dir / "meta.json"
        if not meta_path.is_file():
            raise PreviewError(f"no such preview: {job_id}")
        meta = json.loads(meta_path.read_text())
        size = sum(f.stat().st_size for f in job_dir.rglob("*") if f.is_file())
        return Preview(
            job_id=meta["job_id"],
            kind=meta["kind"],
            date=meta["date"],
            created_at=meta["created_at"],
            path=job_dir,
            size_bytes=size,
        )

    # --- promotion --------------------------------------------------------

    def promote(self, job_id: str, principal: str) -> list[str]:
        """Copy approved files into the checkout and commit them, signed."""
        preview = self.get(job_id)
        if preview is None:
            raise PreviewError(f"no such preview: {job_id}")

        source_dir = self.web_dir(job_id) / "data" / preview.date
        if not source_dir.is_dir():
            raise PreviewError("preview contains no generated data")

        repo = self._settings.repo_dir
        target_dir = repo / "web" / "data" / preview.date
        target_dir.mkdir(parents=True, exist_ok=True)

        # Only files the pipeline itself publishes. Never sweep the whole tree:
        # a stray file in a preview should not become a published artifact.
        publishable = ("summary.json", "hero.webp", "news.json", "research.json",
                       "social.json", "reddit.json", "replay-index.json",
                       "replay-stream.json.gz")

        copied: list[str] = []
        for name in publishable:
            source = source_dir / name
            if source.is_file():
                shutil.copy2(source, target_dir / name)
                copied.append(f"web/data/{preview.date}/{name}")

        if not copied:
            raise PreviewError("preview contained no publishable files")

        self._commit(repo, copied, preview, principal)
        self._store.record_action(
            principal, "preview-promote", preview.job_id, "success",
            f"{len(copied)} file(s) for {preview.date}",
        )
        shutil.rmtree(preview.path, ignore_errors=True)
        return copied

    def _commit(self, repo: Path, paths: list[str], preview: Preview, principal: str) -> None:
        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, timeout=120, check=False,
            )

        # Check signing is configured BEFORE committing. An unsigned tip makes
        # deploy.sh abort, which freezes the site on stale content -- a failure
        # that surfaces far from its cause.
        signing_key = git("config", "--get", "user.signingkey").stdout.strip()
        gpg_sign = git("config", "--get", "commit.gpgsign").stdout.strip()
        if not signing_key or gpg_sign != "true":
            raise PreviewError(
                "commit signing is not configured on this host, so the promoted "
                "commit would be rejected by the deploy gate and the site would "
                "stay on the previous report. Configure user.signingkey and "
                "commit.gpgsign before promoting."
            )

        add = git("add", "--", *paths)
        if add.returncode != 0:
            raise PreviewError(f"git add failed: {add.stderr.strip()[:200]}")

        message = (
            f"data: promote {preview.kind} preview for {preview.date}\n\n"
            f"Approved via the admin panel by {principal}.\n"
            f"Preview job {preview.job_id}."
        )
        commit = git("commit", "-S", "-m", message)
        if commit.returncode != 0:
            if "nothing to commit" in (commit.stdout + commit.stderr).lower():
                raise PreviewError("promoted files were identical to what is published")
            raise PreviewError(f"git commit failed: {commit.stderr.strip()[:200]}")

        push = git("push", "origin", "HEAD:main")
        if push.returncode != 0:
            raise PreviewError(
                f"commit created but push failed: {push.stderr.strip()[:200]}. "
                "The change is committed locally; resolve and push manually."
            )

    # --- retention --------------------------------------------------------

    def reap(self, max_age_days: int = 7) -> int:
        """Delete previews older than the retention window.

        The previous admin service left 2.4 GB of orphaned worktrees behind. On
        a 29 GB host that eventually takes the site's own deploy target with it.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        for preview in self.list():
            try:
                created = datetime.fromisoformat(preview.created_at)
            except ValueError:
                continue
            if created < cutoff:
                shutil.rmtree(preview.path, ignore_errors=True)
                removed += 1
        return removed


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False
```

- [ ] **Step 2: Verify the lifecycle**

Run:
```bash
cd /Users/ryand/Code/AATF/ai-news-aggregator
./venv/bin/python3 -c "
import tempfile, pathlib
from admin_service.config import AdminSettings
from admin_service.store import AdminStore
from admin_service.preview import PreviewManager, PreviewError
d = pathlib.Path(tempfile.mkdtemp())
s = AdminSettings.from_env({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com','ADMIN_CF_AUD':'a',
    'ADMIN_ALLOWED_EMAILS':'op@x.com','ADMIN_STATE_DB':str(d/'admin.sqlite3'),'ADMIN_REPO_DIR':'$PWD'})
m = PreviewManager(s, AdminStore(s.state_db))
p = m.create('hero','2026-07-27')
print('created:', p.job_id, '->', p.to_dict()['url'])
m.seed_from_live(p.job_id, '2026-07-27')
seeded = sorted(x.name for x in (m.web_dir(p.job_id)/'data'/'2026-07-27').iterdir())
print('seeded:', seeded)
print('listed:', [x.job_id for x in m.list()])
for bad, label in [('nope','bad kind'), ('hero','bad date')]:
    try:
        m.create(bad, 'notadate' if bad=='hero' else '2026-07-27'); print(label,'ACCEPTED (BAD)')
    except PreviewError as e: print(f'{label}: refused ({e})')
try:
    m._job_dir('../../etc')
except PreviewError as e: print('path escape: refused')
print('reaped (0 expected, nothing old):', m.reap(7))
"
```

Expected: a job id and `/preview/<id>/` URL, seeded files `['hero.webp', 'summary.json']`, the job listed, both invalid inputs refused, the path escape refused, and `0` reaped.

- [ ] **Step 3: Commit**

```bash
git add admin_service/preview.py
git commit -m "admin: preview lifecycle and signed promotion

Previews live outside the checkout because deploy.sh runs git reset --hard and
git clean -fd -- anything staged under web/data is destroyed by the next push.
Keeping drafts off the public origin also means an unapproved report has no
URL.

Promotion checks signing config BEFORE committing: an unsigned tip makes the
deploy gate abort, which freezes the site on stale content and surfaces far
from its cause. It copies only the files the pipeline itself publishes rather
than sweeping the tree.

Retention is enforced. The previous attempt left 2.4 GB of orphaned worktrees
on a 29 GB host."
```

---

### Task 3: Preview endpoints and static serving

**Files:**
- Modify: `admin_service/app.py`

**Interfaces:**
- Consumes: `PreviewManager`.
- Produces: `GET /api/previews`, `POST /api/previews`, `POST /api/previews/{job_id}/promote`, `DELETE /api/previews/{job_id}`, and `GET /preview/{job_id}/{path}` serving the bundle with the base attribute injected.

- [ ] **Step 1: Add the endpoints**

In `admin_service/app.py`, add imports:

```python
from fastapi.responses import FileResponse, HTMLResponse
from .preview import PreviewError, PreviewManager
```

After the store is constructed in `create_app`:

```python
    previews = PreviewManager(settings, store)
    app.state.previews = previews
```

Then add before `return app`:

```python
    @app.get("/api/previews")
    def list_previews(principal: Principal = Depends(require_principal)) -> dict:
        return {"previews": [p.to_dict() for p in previews.list()]}

    @app.post("/api/previews")
    def create_preview(
        kind: str, date: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        try:
            preview = previews.create(kind, date)
            previews.seed_from_live(preview.job_id, date)
        except PreviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.record_action(principal.email, "preview-create", preview.job_id, "success", kind)
        return preview.to_dict()

    @app.post("/api/previews/{job_id}/promote")
    def promote_preview(
        job_id: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        try:
            copied = previews.promote(job_id, principal.email)
        except PreviewError as exc:
            store.record_action(principal.email, "preview-promote", job_id, "failed", str(exc)[:400])
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"promoted": True, "files": copied}

    @app.delete("/api/previews/{job_id}")
    def discard_preview(
        job_id: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        preview = previews.get(job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="No such preview.")
        import shutil as _shutil
        _shutil.rmtree(preview.path, ignore_errors=True)
        store.record_action(principal.email, "preview-discard", job_id, "success", "")
        return {"discarded": True}

    @app.get("/preview/{job_id}/{path:path}")
    def serve_preview(
        job_id: str, path: str = "", principal: Principal = Depends(require_principal)
    ):
        """Serve the real built bundle against a preview's data.

        HTML is served byte-for-byte apart from one attribute added to <body>.
        Rewriting more would invalidate the page's CSP hash and blank the page.
        """
        preview = previews.get(job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="No such preview.")

        # Data requests resolve against the preview tree.
        if path.startswith("data/"):
            candidate = (previews.web_dir(job_id) / path).resolve()
            root = previews.web_dir(job_id).resolve()
            if not str(candidate).startswith(str(root)):
                raise HTTPException(status_code=400, detail="Invalid path.")
            if candidate.is_file():
                return FileResponse(candidate)
            raise HTTPException(status_code=404, detail="Not in this preview.")

        # Everything else comes from the built site.
        site_root = (settings.repo_dir / "web").resolve()
        target = (site_root / (path or "index.html")).resolve()
        if not str(target).startswith(str(site_root)):
            raise HTTPException(status_code=400, detail="Invalid path.")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            target = site_root / "index.html"

        if target.suffix != ".html":
            return FileResponse(target)

        html = target.read_text()
        base = f"/preview/{job_id}"
        label = f"{preview.kind} preview for {preview.date}"
        # One targeted attribute insertion on the existing <body> tag. The page
        # already carries data-sveltekit-preload-data, so this is the same shape
        # of change and leaves the inline script -- and its CSP hash -- untouched.
        html = html.replace(
            "<body ",
            f'<body data-aatf-data-base="{base}" data-aatf-preview-label="{label}" ',
            1,
        )
        return HTMLResponse(html)
```

- [ ] **Step 2: Verify preview serving works**

Run:
```bash
cd /Users/ryand/Code/AATF/ai-news-aggregator
ADMIN_DEV=1 ADMIN_CF_TEAM_DOMAIN=dev.cloudflareaccess.com ADMIN_CF_AUD=dev \
ADMIN_ALLOWED_EMAILS=dev@localhost ADMIN_REPO_DIR="$PWD" \
ADMIN_STATE_DB=/tmp/preview-test/admin.sqlite3 \
./venv/bin/python3 -c "
from fastapi.testclient import TestClient
from admin_service.app import create_app
c = TestClient(create_app())
r = c.post('/api/previews?kind=hero&date=2026-07-27')
print('create:', r.status_code, r.json().get('job_id'))
job = r.json()['job_id']
print('list:', len(c.get('/api/previews').json()['previews']))
html = c.get(f'/preview/{job}/')
print('html:', html.status_code, '| base attr:', 'data-aatf-data-base' in html.text)
print('csp hash still intact:', 'sha256-' in html.text)
print('preview data:', c.get(f'/preview/{job}/data/2026-07-27/summary.json').status_code)
print('escape blocked:', c.get(f'/preview/{job}/data/../../../etc/passwd').status_code in (400, 404))
print('discard:', c.delete(f'/api/previews/{job}').status_code)
"
rm -rf /tmp/preview-test
```

Expected: create `200` with a job id; one preview listed; HTML `200` with the base attribute present **and** the CSP hash still intact; preview data `200`; the traversal attempt blocked; discard `200`.

The "csp hash still intact" line is the one that matters. If the hash is gone, the HTML was rewritten more than intended and the served page will be blank in a browser.

- [ ] **Step 3: Commit**

```bash
git add admin_service/app.py
git commit -m "admin: serve previews from the admin origin

The built bundle is served byte-for-byte apart from two data-* attributes added
to the existing <body> tag. Anything more would change the bytes the page's CSP
hash covers and blank the page.

Data requests under a preview resolve against that preview's tree; everything
else falls through to the real site. Both paths resolve() and check
containment, so a crafted path cannot read outside its preview or outside web/."
```

---

### Task 4: The preview panel

**Files:**
- Create: `frontend/src/lib/components/admin/PreviewPanel.svelte`
- Modify: `frontend/src/routes/admin/+page.svelte`, `frontend/src/lib/services/adminApi.ts`, `frontend/src/lib/types/admin.ts`

**Interfaces:**
- Consumes: the endpoints from Task 3.
- Produces: a `Preview` tab.

- [ ] **Step 1: Add the API bindings**

Append to `frontend/src/lib/types/admin.ts`:

```ts
export interface PreviewJob {
	job_id: string;
	kind: 'hero' | 'report';
	date: string;
	created_at: string;
	size_bytes: number;
	url: string;
}
```

Append to `frontend/src/lib/services/adminApi.ts`:

```ts
import type { PreviewJob } from '$lib/types/admin';

export const getPreviews = () => get<{ previews: PreviewJob[] }>('/api/previews');

export const createPreview = (kind: 'hero' | 'report', date: string) =>
	post<PreviewJob>(`/api/previews?kind=${kind}&date=${encodeURIComponent(date)}`);

export const promotePreview = (jobId: string) =>
	post<{ promoted: boolean; files: string[] }>(
		`/api/previews/${encodeURIComponent(jobId)}/promote`
	);

export const discardPreview = async (jobId: string): Promise<void> => {
	const response = await fetch(`/api/previews/${encodeURIComponent(jobId)}`, {
		method: 'DELETE',
		credentials: 'same-origin'
	});
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new AdminApiError(body?.detail ?? 'Could not discard preview.', response.status);
	}
};
```

- [ ] **Step 2: Write the panel**

Create `frontend/src/lib/components/admin/PreviewPanel.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import {
		createPreview,
		discardPreview,
		getPreviews,
		promotePreview,
		runAction
	} from '$lib/services/adminApi';
	import type { PreviewJob } from '$lib/types/admin';

	let previews = $state<PreviewJob[]>([]);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let newDate = $state('');
	let confirmingPromote = $state<PreviewJob | null>(null);

	async function refresh() {
		try {
			previews = (await getPreviews()).previews;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load previews.';
		}
	}

	onMount(refresh);

	async function startHero() {
		error = null;
		notice = null;
		busy = 'create';
		try {
			const job = await createPreview('hero', newDate);
			// Generation runs as a host unit and writes into the preview tree.
			await runAction('hero-regen', newDate);
			notice = `Generating a hero for ${newDate}. Reload in a minute to view it.`;
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not start hero generation.';
		} finally {
			busy = null;
		}
	}

	async function promote(job: PreviewJob) {
		confirmingPromote = null;
		error = null;
		notice = null;
		busy = job.job_id;
		try {
			const result = await promotePreview(job.job_id);
			notice = `Published ${result.files.length} file(s) for ${job.date}. The site updates on the next deploy.`;
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not publish this preview.';
		} finally {
			busy = null;
		}
	}

	async function discard(job: PreviewJob) {
		busy = job.job_id;
		try {
			await discardPreview(job.job_id);
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not discard this preview.';
		} finally {
			busy = null;
		}
	}

	function size(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Previews</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		Draft content, viewable exactly as readers would see it. Nothing here is public until you
		publish it.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red" role="alert">{error}</p>
	{/if}
	{#if notice}
		<p class="mt-3 text-sm text-trend-gray-700 dark:text-trend-gray-300" role="status">{notice}</p>
	{/if}

	<div class="mt-3 flex items-end gap-2 flex-wrap">
		<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
			Report date
			<input
				type="date"
				bind:value={newDate}
				class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
			/>
		</label>
		<button class="btn-primary text-sm" disabled={!newDate || busy === 'create'} onclick={startHero}>
			{busy === 'create' ? 'Starting…' : 'Regenerate hero'}
		</button>
	</div>

	{#if previews.length === 0}
		<p class="mt-4 text-sm text-trend-gray-500">
			No previews yet. Regenerate a hero to create one.
		</p>
	{:else}
		<ul class="mt-4 divide-y divide-trend-gray-200 dark:divide-trend-gray-700">
			{#each previews as job (job.job_id)}
				<li class="py-3">
					<div class="flex items-center gap-3 flex-wrap">
						<span class="badge bg-trend-gray-200 dark:bg-trend-gray-700">{job.kind}</span>
						<span class="text-sm font-medium text-trend-gray-800 dark:text-trend-gray-100">
							{job.date}
						</span>
						<span class="text-xs text-trend-gray-500">
							{job.created_at.slice(0, 16).replace('T', ' ')} · {size(job.size_bytes)}
						</span>
						<div class="ml-auto flex gap-2">
							<a
								href={job.url}
								target="_blank"
								rel="noopener noreferrer"
								class="btn-secondary text-xs"
							>
								View
							</a>
							<button
								class="btn-primary text-xs"
								disabled={busy === job.job_id}
								onclick={() => (confirmingPromote = job)}
							>
								Publish
							</button>
							<button
								class="btn-secondary text-xs"
								disabled={busy === job.job_id}
								onclick={() => discard(job)}
							>
								Discard
							</button>
						</div>
					</div>

					{#if confirmingPromote?.job_id === job.job_id}
						<div class="confirm">
							<p class="text-sm text-trend-gray-800 dark:text-trend-gray-100">
								Publish the {job.kind} for {job.date}?
							</p>
							<p class="text-xs text-trend-gray-600 dark:text-trend-gray-400 mt-1">
								This copies the draft into the site, commits it signed, and pushes. View it
								first if you have not.
							</p>
							<div class="mt-2 flex gap-2">
								<button class="btn-primary text-xs" onclick={() => promote(job)}>
									Publish {job.date}
								</button>
								<button class="btn-secondary text-xs" onclick={() => (confirmingPromote = null)}>
									Cancel
								</button>
							</div>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.confirm {
		margin-top: 0.6rem;
		padding: 0.7rem;
		border-radius: 0.5rem;
		background: rgb(230 57 70 / 0.06);
		border: 1px solid rgb(230 57 70 / 0.25);
	}
</style>
```

- [ ] **Step 3: Add the tab**

In `frontend/src/routes/admin/+page.svelte`: add `import PreviewPanel from '$lib/components/admin/PreviewPanel.svelte';`, extend the `View` type to `'health' | 'runs' | 'cost' | 'preview' | 'actions'`, add `{ id: 'preview', label: 'Preview' }` to `views` before Actions, and add the branch:

```svelte
		{:else if view === 'preview'}
			<PreviewPanel />
```

- [ ] **Step 4: Add the preview banner to the public layout**

A preview must be unmistakable from inside the rendered page, not just from the panel that launched it. In `frontend/src/routes/+layout.svelte`, add to the script block:

```ts
	import { isPreview, previewLabel } from '$lib/services/dataBase';
	let preview = $state(false);
	let label = $state<string | null>(null);
	onMount(() => {
		preview = isPreview();
		label = previewLabel();
	});
```

and immediately inside the layout's root element:

```svelte
{#if preview}
	<div class="preview-banner" role="status">
		<strong>Draft.</strong>
		{label ?? 'This is unpublished content'} — not visible to readers.
	</div>
{/if}
```

with:

```css
	.preview-banner {
		position: sticky;
		top: 0;
		z-index: 60;
		padding: 0.45rem 1rem;
		text-align: center;
		font-size: 0.8rem;
		color: #fff;
		background: #e63946;
		/* Deliberately loud and sticky. The failure this prevents is an operator
		   reading a draft, believing it is live, and "fixing" something that was
		   never broken -- or approving a page they never actually looked at. */
	}
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && npm run check 2>&1 | tail -15`

Expected: no new errors beyond the 4 known `vite.config.ts` baseline errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/components/admin/PreviewPanel.svelte \
        frontend/src/lib/services/adminApi.ts frontend/src/lib/types/admin.ts \
        frontend/src/routes/admin/+page.svelte frontend/src/routes/+layout.svelte
git commit -m "admin: preview panel and draft banner

Publishing asks for confirmation and says plainly what it does -- copies the
draft in, commits it signed, pushes -- rather than a generic 'are you sure'.

The banner is sticky and loud inside the rendered preview itself, because the
panel that launched it is a different tab. The failure it prevents is reading a
draft as if it were live, or approving a page never actually opened."
```

---

### Task 5: Close the deploy/rebuild race

**Files:**
- Modify: `scripts/deploy.sh`

Carried forward from plans 3 and 4: the admin actions take `/var/lib/aatf-admin/privileged.lock`, but the HMAC-gated deploy webhook fires independently. A push landing mid-rebuild runs `git reset --hard` and `git clean -fd` on the build context under a running build.

- [ ] **Step 1: Wrap the deploy body in the shared lock**

In `scripts/deploy.sh`, immediately after `set -e` and the `cd`, insert:

```bash
# Serialise against admin-panel actions, which take the same lock. Without this
# a push landing mid-rebuild runs `git reset --hard` and `git clean -fd` on the
# build context of a running `docker compose build`.
#
# Re-exec under flock rather than wrapping the body, so the lock covers every
# exit path including the abort in the signature gate below.
LOCK_FILE="/var/lib/aatf-admin/privileged.lock"
if [ -z "${DEPLOY_LOCK_HELD:-}" ] && [ -e "$LOCK_FILE" ]; then
    export DEPLOY_LOCK_HELD=1
    exec /usr/bin/flock --wait 1800 "$LOCK_FILE" "$0" "$@"
fi
```

The `[ -e "$LOCK_FILE" ]` guard keeps `deploy.sh` working on a host where the admin service was never provisioned — the lock only applies where it exists.

- [ ] **Step 2: Verify the script still parses and the guard is inert without the lock**

Run:
```bash
bash -n scripts/deploy.sh && echo "parses OK"
grep -n "DEPLOY_LOCK_HELD\|flock" scripts/deploy.sh
```

Expected: `parses OK` and the two new lines. On a machine with no `/var/lib/aatf-admin/privileged.lock`, the re-exec is skipped entirely.

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy.sh
git commit -m "deploy: serialise against admin-panel actions

The webhook fires independently of the panel, so a push landing mid-rebuild
could git reset --hard the build context under a running build.

Re-execs under flock rather than wrapping the body, so the lock covers every
exit path including the signature-gate abort. Skipped when the lock file does
not exist, so hosts without the admin service are unaffected."
```

---

### Task 6: End-to-end verification

**Files:** none.

- [ ] **Step 1: Run the full local loop**

Terminal one: `./scripts/admin_dev.sh`
Terminal two: `cd frontend && npm run dev`

Open `http://localhost:5173/admin`, go to **Preview**, pick a date that exists in `web/data`, and click **Regenerate hero**.

Expected locally: the preview is created and listed, and the hero action fails with a clear message because the systemd unit exists only on the host. That is the correct local behavior — the preview lifecycle is testable without the host, generation is not.

- [ ] **Step 2: Verify a preview renders draft data, not live**

This is the check the whole plan exists for.

```bash
cd /Users/ryand/Code/AATF/ai-news-aggregator
ADMIN_DEV=1 ADMIN_CF_TEAM_DOMAIN=dev.cloudflareaccess.com ADMIN_CF_AUD=dev \
ADMIN_ALLOWED_EMAILS=dev@localhost ADMIN_REPO_DIR="$PWD" \
ADMIN_STATE_DB=/tmp/e2e/admin.sqlite3 \
./venv/bin/python3 -c "
import json, pathlib
from fastapi.testclient import TestClient
from admin_service.app import create_app
app = create_app(); c = TestClient(app)
job = c.post('/api/previews?kind=report&date=2026-07-27').json()['job_id']

# Make the preview's data visibly different from live.
web = app.state.previews.web_dir(job) / 'data' / '2026-07-27'
s = json.loads((web / 'summary.json').read_text())
s['executive_summary'] = 'DRAFT-ONLY-MARKER'
(web / 'summary.json').write_text(json.dumps(s))

live = json.loads(pathlib.Path('web/data/2026-07-27/summary.json').read_text())
print('live is unchanged:', 'DRAFT-ONLY-MARKER' not in json.dumps(live))
served = c.get(f'/preview/{job}/data/2026-07-27/summary.json').json()
print('preview serves the draft:', served['executive_summary'] == 'DRAFT-ONLY-MARKER')
html = c.get(f'/preview/{job}/').text
print('page carries the base attr:', f'data-aatf-data-base=\"/preview/{job}\"' in html)
print('page keeps its CSP hash:', 'sha256-' in html)
c.delete(f'/api/previews/{job}')
"
rm -rf /tmp/e2e
```

Expected: all four `True`. If "preview serves the draft" is False, the preview is rendering live data — stop and fix before using the feature for anything.

- [ ] **Step 3: Run the full test suite**

Run:
```bash
cd /Users/ryand/Code/AATF/ai-news-aggregator
./venv/bin/python3 -m unittest \
  tests.source_anomaly_test tests.admin_auth_test tests.admin_actions_test \
  tests.admin_dashboard_test tests.preview_base_test tests.deploy_signature_gate_test -v 2>&1 | tail -20
cd frontend && npm run check 2>&1 | tail -6
```

Expected: all Python tests pass; `svelte-check` reports only the 4 known `vite.config.ts` baseline errors.

- [ ] **Step 4: Commit any fixes and push the branch**

```bash
git status --short
git push -u origin feat/admin-panel
```

---

## Self-Review

**Spec coverage.** Implements spec §3 (preview before publish, including the `data-*` mechanism, the fail-loudly requirement, signed promotion, hero cache-busting, and GC) and closes the `deploy.sh` lock gap carried forward from plans 3 and 4. With this, every section of the spec has an implementing task across the six plans.

**Placeholders.** None. Every step has literal content or a runnable command with expected output.

**Type/name consistency.** `dataBase`/`dataUrl`/`isPreview`/`previewLabel` are defined in Task 1 and imported under those names in Tasks 1, 4. The attribute names `data-aatf-data-base` and `data-aatf-preview-label` map to the `aatfDataBase`/`aatfPreviewLabel` dataset keys (correct camelCase conversion) and match exactly what Task 3's HTML injection writes. `Preview.to_dict()` keys in Task 2 match the `PreviewJob` interface in Task 4. `PreviewManager` method names match their call sites in Task 3's endpoints.

**One thing deliberately not built.** Report previews can be *created* and *served*, but nothing populates them with a dry-run pipeline result yet — that needs the `generated-pipeline-output` artifact downloaded from a `commit_outputs=false` dispatch and unpacked into the preview tree. The plumbing is all here (`create`, `seed_from_live`, `promote`); the artifact download is one endpoint away and belongs with a hosted-run cycle where it can actually be tested. Hero previews work end to end, which is the case with the real workflow need.

**One risk.** Promotion pushes directly to `main`. That is intentional — it is the same path CI uses, and the signed-commit gate is what makes it safe — but it means a mistaken publish is a real commit. The confirmation names the date and says exactly what will happen, and the audit log records who approved what.
