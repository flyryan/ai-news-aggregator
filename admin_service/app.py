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

from .auth import CF_JWT_HEADER, AccessVerifier, AuthError, NotAuthorized, Principal
from .config import AdminSettings
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

    return app
