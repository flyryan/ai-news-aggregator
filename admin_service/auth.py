"""Cloudflare Access JWT verification.

The tunnel is configured to require Access, but this module does not rely on
that. It verifies the RS256 signature against the team JWKS and checks aud,
iss, and expiry itself, because the edge check and the origin check fail
independently: a Bypass policy, a path rule that does not cover what you think,
or a future direct-to-port exposure all defeat the edge alone.

Cloudflare's guidance is explicit that validating the identity header alone is
insufficient and the signature must be confirmed. An earlier version of this
service checked only that a header was present; see tests/admin_auth_test.py.

No FastAPI import here on purpose -- this is pure and directly testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from .config import AdminSettings

__all__ = [
    "AccessVerifier",
    "Principal",
    "AuthError",
    "TokenInvalid",
    "NotAuthorized",
    "CF_JWT_HEADER",
]

# Verify this, not the CF_Authorization cookie: the cookie is not guaranteed to
# be forwarded to the origin, the header is.
CF_JWT_HEADER = "cf-access-jwt-assertion"


class AuthError(Exception):
    """Base class for authentication and authorization failures."""


class TokenInvalid(AuthError):
    """The token is missing, malformed, unsigned, mis-scoped, or expired."""


class NotAuthorized(AuthError):
    """The token is valid but the principal is not on the allowlist."""


@dataclass(frozen=True)
class Principal:
    email: str
    subject: str
    kind: str  # "user" | "service_token"


class _JWKClientLike(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class AccessVerifier:
    """Verifies Access JWTs and authorizes the resulting principal."""

    def __init__(
        self,
        settings: AdminSettings,
        *,
        jwks_client: _JWKClientLike | None = None,
        leeway: float = 30.0,
    ) -> None:
        self._settings = settings
        self._leeway = leeway
        if jwks_client is not None:
            self._jwks = jwks_client
        else:
            # lifespan bounds how long a rotated-out key stays usable. Leave
            # cache_keys False: that tier is an unbounded LRU with no time
            # expiry, which would pin a revoked key indefinitely.
            self._jwks = jwt.PyJWKClient(
                settings.jwks_url(),
                cache_jwk_set=True,
                lifespan=600,
                cache_keys=False,
                timeout=5,
            )

    def verify(self, token: str) -> Principal:
        if not token or not token.strip():
            raise TokenInvalid("no Access token presented")

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception as exc:  # noqa: BLE001 - any JWKS failure is untrusted input
            raise TokenInvalid(f"could not resolve signing key: {exc}") from exc

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.cf_aud,
                issuer=self._settings.issuer_url(),
                leeway=self._leeway,
                # Requiring the claims is what makes their absence an error
                # rather than a silently skipped check. Note: do NOT pass
                # strict_aud -- on PyJWT 2.13 it is not a supported kwarg, it
                # warns, and it is ignored. Cloudflare's aud is a JSON array and
                # a string `audience=` already handles that correctly.
                options={"require": ["exp", "iat", "nbf", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenInvalid(f"token rejected: {type(exc).__name__}") from exc

        return self._authorize(claims)

    def _authorize(self, claims: dict[str, Any]) -> Principal:
        email = (claims.get("email") or "").strip().lower()
        if email:
            if email not in self._settings.allowed_emails:
                raise NotAuthorized(f"{email} is not an authorized operator")
            return Principal(email=email, subject=str(claims.get("sub") or ""), kind="user")

        common_name = (claims.get("common_name") or "").strip()
        if common_name:
            if common_name not in self._settings.allowed_service_tokens:
                raise NotAuthorized(f"service token {common_name} is not authorized")
            return Principal(email=common_name, subject="", kind="service_token")

        raise NotAuthorized("token carries neither an email nor a service-token name")
