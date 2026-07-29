"""FastAPI application for the admin panel.

Auth is a dependency on every route except /api/health, which exists so
systemd and a human with SSH can tell the process is alive without holding a
token. Health returns liveness only -- never configuration or version detail
that would help someone probing the origin.
"""

from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from datetime import datetime

from .actions import ACTIONS, ActionError, ActionRunner
from .auth import CF_JWT_HEADER, AccessVerifier, AuthError, NotAuthorized, Principal
from .balances import fetch_balances
from .config import AdminSettings
from .dashboard import cost_series, health_series, latest_report
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

    app = FastAPI(title="AATF admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.verifier = verifier
    app.state.store = store

    runner = ActionRunner(store)
    previews = PreviewManager(settings, store)
    github = GitHubClient(settings.github_repo, settings.github_token)
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
        """Serve the real built bundle against a preview's data.

        HTML is served byte-for-byte apart from two attributes added to the
        existing <body> tag. Rewriting more would change the bytes the page's
        CSP hash covers and blank the page.
        """
        preview = previews.get(job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="No such preview.")

        # Data requests resolve against the preview tree.
        if path.startswith("data/"):
            root = previews.web_dir(job_id).resolve()
            candidate = (root / path).resolve()
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
        if not target.is_file():
            raise HTTPException(
                status_code=503,
                detail="The site bundle is not built. Run `npm run build` in frontend/.",
            )

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

    return app
