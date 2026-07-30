"""FastAPI application for the admin panel.

Auth is a dependency on every route except /api/health, which exists so
systemd and a human with SSH can tell the process is alive without holding a
token. Health returns liveness only -- never configuration or version detail
that would help someone probing the origin.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from datetime import datetime

from .actions import ACTIONS, ActionError, ActionRunner
from .auth import CF_JWT_HEADER, AccessVerifier, AuthError, NotAuthorized, Principal
from .balances import fetch_balances
from .config import AdminSettings
from .dashboard import cost_series, health_series, latest_report, source_day_detail
from .github import GitHubClient, GitHubError
from .preview import PreviewError, PreviewManager
from .store import AdminStore

logger = logging.getLogger("admin_service")


def create_app(
    settings: AdminSettings | None = None,
    verifier: AccessVerifier | None = None,
    store: AdminStore | None = None,
) -> FastAPI:
    settings = settings or AdminSettings.from_env()
    verifier = verifier or AccessVerifier(settings)
    store = store or AdminStore(settings.state_db)

    runner = ActionRunner(store)
    previews = PreviewManager(settings, store)
    github = GitHubClient(settings.github_repo, settings.github_token)

    def _reap_previews() -> None:
        # The previous admin service left 2.4 GB of orphaned worktrees behind;
        # retention is load-bearing, not tidiness. Never let it hurt the app.
        try:
            removed = previews.reap()
            if removed:
                logger.info("reaped %d expired preview(s)", removed)
        except Exception:  # noqa: BLE001 - retention must never take the panel down
            logger.exception("preview reaping failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _reap_previews()

        async def daily() -> None:
            while True:
                await asyncio.sleep(24 * 3600)
                _reap_previews()

        task = asyncio.create_task(daily())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(
        title="AATF admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.verifier = verifier
    app.state.store = store
    app.state.runner = runner
    app.state.previews = previews
    app.state.github = github

    def require_principal(request: Request) -> Principal:
        # Local development only. ADMIN_DEV is never set on the host: the
        # provisioned env file does not define it, the systemd unit does not
        # pass it, and a guard test fails if it is set while the suite runs.
        # Kept explicit and noisy rather than clever, because an auth bypass
        # that is easy to enable by accident is the bug this whole service was
        # designed to avoid.
        if os.environ.get("ADMIN_DEV") == "1":
            logger.warning("ADMIN_DEV=1: bypassing Access verification (dev only)")
            return Principal(email="dev@localhost", subject="dev", kind="user")

        token = request.headers.get(CF_JWT_HEADER, "")
        try:
            return request.app.state.verifier.verify(token)
        except NotAuthorized as exc:
            logger.warning("authorization refused: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not an authorized operator.",
            ) from exc
        except AuthError as exc:
            logger.warning("authentication refused: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cloudflare Access authentication required.",
            ) from exc

    # Exposed so later modules can reuse the same dependency without rebuilding it.
    app.state.require_principal = require_principal

    @app.get("/api/health")
    def health() -> JSONResponse:
        # Unauthenticated on purpose, and deliberately uninformative.
        return JSONResponse({"status": "ok"})

    @app.get("/api/me")
    def me(principal: Principal = Depends(require_principal)) -> dict:
        return {
            "email": principal.email,
            "kind": principal.kind,
            "subject": principal.subject,
        }

    @app.get("/api/audit")
    def audit(
        limit: int = 50, principal: Principal = Depends(require_principal)
    ) -> dict:
        limit = max(1, min(limit, 500))
        return {"actions": store.recent_actions(limit)}

    # --- dashboard ----------------------------------------------------------

    @app.get("/api/dashboard/latest")
    def dashboard_latest(principal: Principal = Depends(require_principal)) -> dict:
        return {"latest": latest_report(settings.repo_dir / "web")}

    @app.get("/api/dashboard/health")
    def dashboard_health(
        days: int = 90, principal: Principal = Depends(require_principal)
    ) -> dict:
        return health_series(settings.repo_dir / "web", days=max(7, min(days, 365)))

    @app.get("/api/dashboard/source-day")
    def dashboard_source_day(
        source: str, date: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        # `source` and `date` are used to build filenames, so validate their
        # shape rather than trusting them into a path join.
        if not re.fullmatch(r"[a-z_]{1,32}", source):
            raise HTTPException(status_code=400, detail="Invalid source.")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(status_code=400, detail="Invalid date.")
        return source_day_detail(settings.repo_dir / "web", source, date)

    @app.get("/api/dashboard/cost")
    def dashboard_cost(
        days: int = 90, principal: Principal = Depends(require_principal)
    ) -> dict:
        return {"runs": cost_series(settings.repo_dir / "web", days=max(7, min(days, 400)))}

    @app.get("/api/dashboard/balances")
    def dashboard_balances(principal: Principal = Depends(require_principal)) -> dict:
        return {
            "balances": fetch_balances(
                store,
                scrapecreators_key=os.environ.get("SCRAPECREATORS_API_KEY", ""),
                twitter_key=os.environ.get("TWITTERAPI_IO_KEY", ""),
            )
        }

    @app.get("/api/dashboard/runs")
    def dashboard_runs(
        limit: int = 30, principal: Principal = Depends(require_principal)
    ) -> dict:
        try:
            runs = github.list_runs(limit=max(1, min(limit, 100)))
        except GitHubError as exc:
            # A GitHub outage or missing token must degrade this panel, not the page.
            return {"runs": [], "error": str(exc)}

        for run in runs:
            duration = 0
            if run.get("created_at") and run.get("updated_at"):
                try:
                    started = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                    ended = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
                    duration = int((ended - started).total_seconds())
                except ValueError:
                    duration = 0
            run["duration_seconds"] = duration
            # 13 of 50 "successful" runs are 15-second schedule-gate no-ops.
            # Counting them halves the apparent success rate and wrecks duration
            # averages, so mark them rather than filtering silently -- a hidden
            # filter is its own kind of distortion.
            run["did_real_work"] = duration >= 120

        return {"runs": runs}

    # --- privileged actions -------------------------------------------------

    @app.get("/api/actions")
    def list_actions(principal: Principal = Depends(require_principal)) -> dict:
        return {
            "actions": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "needs_arg": spec.needs_arg,
                    "danger": spec.danger,
                }
                for spec in ACTIONS.values()
                # promote is driven by the Preview panel with its own confirm
                # flow; listing it here would offer a raw job-id text box.
                if not spec.internal
            ]
        }

    @app.post("/api/actions/{action}")
    def run_action(
        action: str,
        arg: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        try:
            unit = runner.start(action, principal.email, arg)
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"unit": unit, "started": True}

    @app.get("/api/actions/status/{unit}")
    def action_status(
        unit: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        state = runner.status(unit)
        return {
            "unit": state.unit,
            "active_state": state.active_state,
            "result": state.result,
            "exit_code": state.exit_code,
            "finished": state.finished,
            "succeeded": state.succeeded,
        }

    @app.get("/api/actions/logs/{unit}")
    def action_logs(
        unit: str, lines: int = 200, principal: Principal = Depends(require_principal)
    ) -> dict:
        return {"unit": unit, "lines": runner.logs(unit, lines)}

    @app.post("/api/pipeline/dispatch")
    def dispatch_pipeline(
        target_date: str | None = None,
        resume_from: str | None = None,
        commit_outputs: bool = False,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        # Dispatching while a run is active CANCELS it: daily-pipeline.yml sets
        # cancel-in-progress for workflow_dispatch. Refuse rather than silently
        # killing a run that may be 30 minutes into an expensive pipeline.
        try:
            active = github.in_flight()
        except GitHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if active:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A pipeline run is already {active['status']} "
                    f"({active['html_url']}). Dispatching now would cancel it."
                ),
            )

        inputs: dict[str, str] = {"commit_outputs": str(bool(commit_outputs)).lower()}
        if target_date:
            inputs["target_date"] = target_date
        if resume_from:
            inputs["resume_from"] = resume_from

        try:
            github.dispatch("daily-pipeline.yml", inputs)
        except GitHubError as exc:
            store.record_action(principal.email, "pipeline-dispatch", target_date,
                                "error", str(exc)[:500])
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        store.record_action(principal.email, "pipeline-dispatch", target_date,
                            "started", f"commit_outputs={commit_outputs}")
        return {"dispatched": True, "inputs": inputs}

    # --- previews -----------------------------------------------------------

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
        """Start the publish unit for a preview.

        Publishing is asynchronous: this returns the systemd unit, the panel
        polls /api/actions/status/{unit}, and scripts/promote_preview.sh (as
        ubuntu) copies, signs, pushes, and deletes the preview on success.
        """
        try:
            files = previews.publishable_files(job_id)
        except PreviewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not files:
            store.record_action(
                principal.email, "preview-promote", job_id, "refused",
                "no publishable files",
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "This preview has no publishable files yet -- generation "
                    "may still be running, or it may have failed."
                ),
            )
        try:
            unit = runner.start("promote", principal.email, arg=job_id)
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"started": True, "unit": unit, "files": files}

    @app.delete("/api/previews/{job_id}")
    def discard_preview(
        job_id: str, principal: Principal = Depends(require_principal)
    ) -> dict:
        try:
            previews.discard(job_id)
        except PreviewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        store.record_action(principal.email, "preview-discard", job_id, "success", "")
        return {"discarded": True}

    @app.get("/preview/{job_id}/{path:path}")
    def serve_preview(
        job_id: str, path: str = "", principal: Principal = Depends(require_principal)
    ):
        """Serve a preview's data files, and route its page to the SPA.

        Only `data/...` is served from the preview tree. The page itself lives
        at `/?preview=<job>&date=<date>`: the SvelteKit router has no
        /preview/... route, so serving the bundle at this path would hydrate
        straight into the 404 page instead of the report. The redirect keeps
        old-style preview URLs working.
        """
        preview = previews.get(job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="No such preview.")

        # Data requests resolve against the preview tree.
        if path.startswith("data/"):
            root = previews.web_dir(job_id).resolve()
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise HTTPException(status_code=400, detail="Invalid path.")
            if candidate.is_file():
                return FileResponse(candidate)
            raise HTTPException(status_code=404, detail="Not in this preview.")

        if path in ("", "index.html"):
            return RedirectResponse(
                f"/?preview={preview.job_id}&date={preview.date}", status_code=307
            )
        raise HTTPException(status_code=404, detail="Previews serve only their data files.")

    # --- the site itself ------------------------------------------------------
    #
    # The panel is the same built SPA the public site serves; the admin origin
    # must serve it because adminApi fetches relative paths on this origin. The
    # host has no node and web/_app is gitignored, so the bundle comes from
    # deploy/export_web_bundle.sh (extracted from the web Docker image); the
    # checkout fallback exists for local dev, where npm run build populates web/.
    # Registered last: every /api and /preview route above wins first.

    def _bundle_root() -> Path:
        exported = settings.site_dir
        if (exported / "index.html").is_file():
            return exported.resolve()
        return (settings.repo_dir / "web").resolve()

    _MEDIA_TYPES = {".webp": "image/webp", ".gz": "application/gzip", ".xml": "application/xml"}

    _PREVIEW_ID = re.compile(r"^(hero|report)-\d{4}-\d{2}-\d{2}$")

    @app.get("/{path:path}")
    def serve_site(
        request: Request, path: str = "", principal: Principal = Depends(require_principal)
    ):
        if path.startswith(("api/", "preview/")):
            raise HTTPException(status_code=404, detail="Not found.")

        # Typing the bare hostname should land on the panel -- that is what
        # this origin is for. Only the truly bare root redirects: query-param
        # roots are real pages here (`/?preview=` renders a draft, `/?date=`
        # browses a report) and must not bounce to /admin.
        if path == "" and not request.query_params:
            return RedirectResponse("/admin", status_code=307)

        # Live data and assets come from the checkout, which the git sync keeps
        # current; the exported bundle deliberately excludes both.
        if path.startswith(("data/", "assets/")):
            root = (settings.repo_dir / "web").resolve()
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise HTTPException(status_code=400, detail="Invalid path.")
            if not candidate.is_file():
                raise HTTPException(status_code=404, detail="Not found.")
            return FileResponse(candidate, media_type=_MEDIA_TYPES.get(candidate.suffix))

        root = _bundle_root()
        target = (root / (path or "index.html")).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Invalid path.")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA fallback: /admin, /archive?date=..., /replay all hydrate from
            # the shell, same as nginx's try_files on the public origin.
            target = root / "index.html"
        if not target.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    "No site bundle. On the host run deploy/export_web_bundle.sh; "
                    "locally run `npm run build` in frontend/."
                ),
            )

        if target.suffix != ".html":
            return FileResponse(target, media_type=_MEDIA_TYPES.get(target.suffix))

        html = target.read_text()

        # ?preview=<job> renders the SPA against that preview's data tree. The
        # base travels as a data-* attribute on the existing <body> tag -- an
        # injected inline <script> would be blocked by the page's CSP hash. An
        # unknown or malformed job 404s: silently serving live data under a
        # preview URL is the exact failure this design exists to prevent.
        job_id = request.query_params.get("preview")
        if job_id is not None:
            if not _PREVIEW_ID.fullmatch(job_id):
                raise HTTPException(status_code=404, detail="Invalid preview id.")
            preview = previews.get(job_id)
            if preview is None:
                raise HTTPException(status_code=404, detail="No such preview.")
            label = f"{preview.kind} preview for {preview.date}"
            # One targeted attribute insertion on the existing <body> tag; the
            # inline hydration script -- and its CSP hash -- stay untouched.
            html = html.replace(
                "<body ",
                f'<body data-aatf-data-base="/preview/{preview.job_id}" '
                f'data-aatf-preview-label="{label}" ',
                1,
            )
        return HTMLResponse(html)

    return app
