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
from fastapi.responses import JSONResponse

from .actions import ACTIONS, ActionError, ActionRunner
from .auth import CF_JWT_HEADER, AccessVerifier, AuthError, NotAuthorized, Principal
from .config import AdminSettings
from .github import GitHubClient, GitHubError
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
    github = GitHubClient(settings.github_repo, settings.github_token)
    app.state.runner = runner
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

    return app
