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

# Only the files the pipeline itself publishes. Never sweep the whole tree: a
# stray file in a preview should not become a published artifact.
PUBLISHABLE = (
    "summary.json",
    "hero.webp",
    "news.json",
    "research.json",
    "social.json",
    "reddit.json",
    "replay-index.json",
    "replay-stream.json.gz",
)


class PreviewError(RuntimeError):
    pass


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


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
        for name in PUBLISHABLE:
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
            except (PreviewError, KeyError, json.JSONDecodeError):
                continue
        return sorted(previews, key=lambda p: p.created_at, reverse=True)

    def get(self, job_id: str) -> Preview | None:
        try:
            return self._load(job_id)
        except (PreviewError, KeyError, json.JSONDecodeError):
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

    def discard(self, job_id: str) -> None:
        preview = self.get(job_id)
        if preview is None:
            raise PreviewError(f"no such preview: {job_id}")
        shutil.rmtree(preview.path, ignore_errors=True)

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

        copied: list[str] = []
        for name in PUBLISHABLE:
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
