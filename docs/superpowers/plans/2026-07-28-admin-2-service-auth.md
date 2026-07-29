# Admin Service and Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the admin service — a FastAPI app on the origin host, gated by cryptographically verified Cloudflare Access JWTs — with a working health page, an audit log, and alert dedup state.

**Architecture:** A FastAPI app under `admin_service/`, run by systemd as a dedicated unprivileged `aatfadmin` user, bound to `127.0.0.1:8200`, reached only through the existing cloudflared tunnel at `admin.aatf.ai`. Authentication is two-layer: cloudflared refuses unauthenticated requests at the edge, and the app independently verifies the `Cf-Access-Jwt-Assertion` signature, `aud`, `iss`, and expiry against the team JWKS before authorizing the email against an allowlist. State (audit log, alert dedup, balance history) lives in SQLite **outside the git checkout** so `git reset --hard` and `git clean -fd` cannot touch it.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, PyJWT[crypto] (verified present at 2.13.0), httpx, SQLite (stdlib `sqlite3`), systemd, stdlib `unittest`.

## Global Constraints

- **The JWT signature is the only real authentication boundary.** Never authenticate on header presence. The abandoned `admin-service` branch (`42cfad0:admin_service/app.py:143-147`) did exactly that, and `curl -H 'cf-access-authenticated-user-email: x'` was a full admin session.
- Auth tests are stdlib `unittest` plus PyJWT, named `tests/<name>_test.py`.
- **Do not pass `strict_aud` to `jwt.decode()`.** Verified on PyJWT 2.13.0: it is no longer a supported kwarg, emits `RemovedInPyjwt3Warning`, and is silently ignored. Cloudflare's `aud` is a JSON **array**; a string `audience=` handles it correctly (verified).
- The service **refuses to start** if `ADMIN_CF_TEAM_DOMAIN`, `ADMIN_CF_AUD`, or `ADMIN_ALLOWED_EMAILS` is unset. A misconfigured deploy must fail loudly, never open.
- Bind `127.0.0.1` only. Never `0.0.0.0` — the abandoned branch defaulted to all interfaces, which turns any future port exposure into a full bypass.
- State path: `/var/lib/aatf-admin/admin.sqlite3`, outside `/home/ubuntu/ai-news-aggregator`.
- Run as `aatfadmin`, **not** in the `docker` group. Verified on the host: `ubuntu` is in `sudo(27)` and `docker(988)`, i.e. already effective root — which is precisely what this user must not inherit.
- Commits must be SSH-signed.

---

## Host facts (verified 2026-07-28, read-only)

| Fact | Value |
|---|---|
| `ubuntu` groups | `sudo(27)`, `docker(988)` — already effective host root |
| `webhook.service` | active, `User=ubuntu`, `/usr/lib/systemd/system/webhook.service` |
| `cloudflared.service` | active, **token-based**, no local config file — ingress is managed in the Cloudflare dashboard |
| Disk | 29 GB total, 18 GB free |
| Containers | `ai-news-aggregator` up, healthy, `0.0.0.0:7100->80` |
| Python | 3.12.3 |
| `aatfadmin` | does not exist yet |

**Owner actions required before Task 6 can be verified end to end** (dashboard, not scriptable): add tunnel ingress `admin.aatf.ai` → `http://127.0.0.1:8200`, create a Zero Trust Access application over that hostname with a policy for the operator's email, then supply the team domain and the application AUD tag. Everything before Task 6 is testable locally without these.

---

## File Structure

| File | Responsibility |
|---|---|
| `admin_service/__init__.py` (create) | Package marker. |
| `admin_service/config.py` (create) | Settings from environment, with fail-closed validation. |
| `admin_service/auth.py` (create) | Cloudflare Access JWT verification and email authorization. Pure, no FastAPI import. |
| `admin_service/store.py` (create) | SQLite schema and accessors: audit log, alert dedup, balance history. |
| `admin_service/app.py` (create) | FastAPI app, auth dependency, health and identity endpoints. |
| `tests/admin_auth_test.py` (create) | Guard test: the verifier rejects every forged/expired/mis-scoped token. |
| `admin_service/requirements.txt` (create) | Pinned service dependencies, separate from the pipeline's. |
| `deploy/aatf-admin.service.example` (create) | systemd unit template. |
| `deploy/setup_admin_service.sh` (create) | Idempotent host provisioning. |
| `.github/workflows/tests.yml` (modify) | Run the auth guard test. |

---

### Task 1: Configuration that fails closed

**Files:**
- Create: `admin_service/__init__.py`, `admin_service/config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `@dataclass(frozen=True) AdminSettings` with fields `cf_team_domain: str`, `cf_aud: str`, `allowed_emails: frozenset[str]`, `host: str`, `port: int`, `state_db: Path`, `repo_dir: Path`, `github_token: str`, `github_repo: str`; classmethod `AdminSettings.from_env(env: Mapping[str, str] | None = None) -> AdminSettings`; exception `ConfigError(RuntimeError)`; helper `issuer_url() -> str` and `jwks_url() -> str`.

- [ ] **Step 1: Write the package marker**

Create `admin_service/__init__.py`:

```python
"""Private operations panel for the AI news aggregator.

Runs as a systemd service on the origin host under an unprivileged user,
reachable only through the Cloudflare tunnel at admin.aatf.ai.
"""
```

- [ ] **Step 2: Write the config module**

Create `admin_service/config.py`:

```python
"""Settings for the admin service, validated at startup.

Every setting that gates access is required. A missing team domain or AUD tag
is a configuration error that stops the process, not a warning that starts an
unauthenticated service -- the failure mode this service must never have is
"came up, let everyone in".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

__all__ = ["AdminSettings", "ConfigError"]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _require(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        raise ConfigError(
            f"{key} is required. The admin service holds a GitHub token and can "
            f"trigger paid pipeline runs; it will not start without its access "
            f"configuration."
        )
    return value


@dataclass(frozen=True)
class AdminSettings:
    cf_team_domain: str
    cf_aud: str
    allowed_emails: frozenset[str]
    host: str
    port: int
    state_db: Path
    repo_dir: Path
    github_token: str
    github_repo: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AdminSettings":
        env = os.environ if env is None else env

        team = _require(env, "ADMIN_CF_TEAM_DOMAIN").rstrip("/")
        if team.startswith("https://"):
            team = team[len("https://"):]
        if not team.endswith(".cloudflareaccess.com"):
            raise ConfigError(
                "ADMIN_CF_TEAM_DOMAIN must be your Cloudflare Access team domain, "
                "e.g. 'yourteam.cloudflareaccess.com'."
            )

        emails = frozenset(
            part.strip().lower()
            for part in _require(env, "ADMIN_ALLOWED_EMAILS").split(",")
            if part.strip()
        )
        if not emails:
            raise ConfigError("ADMIN_ALLOWED_EMAILS contained no usable addresses.")

        host = (env.get("ADMIN_HOST") or "127.0.0.1").strip()
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ConfigError(
                f"ADMIN_HOST={host!r} is refused. The service must bind loopback and "
                "be reached through the Cloudflare tunnel; binding a routable "
                "interface makes the JWT check the only thing between the internet "
                "and a token that can spend money."
            )

        return cls(
            cf_team_domain=team,
            cf_aud=_require(env, "ADMIN_CF_AUD"),
            allowed_emails=emails,
            host=host,
            port=int(env.get("ADMIN_PORT") or 8200),
            state_db=Path(env.get("ADMIN_STATE_DB") or "/var/lib/aatf-admin/admin.sqlite3"),
            repo_dir=Path(env.get("ADMIN_REPO_DIR") or "/home/ubuntu/ai-news-aggregator"),
            # Optional: read-only views work without it; actions and logs do not.
            github_token=(env.get("ADMIN_GITHUB_TOKEN") or "").strip(),
            github_repo=(env.get("ADMIN_GITHUB_REPO") or "flyryan/ai-news-aggregator").strip(),
        )

    def issuer_url(self) -> str:
        return f"https://{self.cf_team_domain}"

    def jwks_url(self) -> str:
        return f"https://{self.cf_team_domain}/cdn-cgi/access/certs"
```

- [ ] **Step 3: Verify it fails closed**

Run:
```bash
./venv/bin/python3 -c "
from admin_service.config import AdminSettings, ConfigError
for env, label in [
    ({}, 'empty'),
    ({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com'}, 'no aud'),
    ({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com','ADMIN_CF_AUD':'a'}, 'no emails'),
    ({'ADMIN_CF_TEAM_DOMAIN':'evil.com','ADMIN_CF_AUD':'a','ADMIN_ALLOWED_EMAILS':'x@y.z'}, 'bad team domain'),
    ({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com','ADMIN_CF_AUD':'a','ADMIN_ALLOWED_EMAILS':'x@y.z','ADMIN_HOST':'0.0.0.0'}, 'public bind'),
]:
    try:
        AdminSettings.from_env(env); print(f'{label:18s} STARTED (BAD)')
    except ConfigError as e:
        print(f'{label:18s} refused: {str(e)[:60]}')
s = AdminSettings.from_env({'ADMIN_CF_TEAM_DOMAIN':'https://t.cloudflareaccess.com/','ADMIN_CF_AUD':'aud1','ADMIN_ALLOWED_EMAILS':'A@B.com, c@d.com'})
print('valid config ->', s.issuer_url(), '|', sorted(s.allowed_emails))
"
```

Expected: all five refused with an explanation, then
`valid config -> https://t.cloudflareaccess.com | ['a@b.com', 'c@d.com']`
(note the scheme and trailing slash are stripped and emails are lowercased).

- [ ] **Step 4: Commit**

```bash
git add admin_service/__init__.py admin_service/config.py
git commit -m "admin: configuration that fails closed

Every access-gating setting is required, and the process refuses to start
without one. This service holds a GitHub token and can trigger paid runs, so
'came up without auth configured' is the one startup outcome it must never
have.

ADMIN_HOST is validated to loopback: the abandoned attempt defaulted to
0.0.0.0, which makes any future port exposure a full bypass rather than a
misconfiguration."
```

---

### Task 2: Cloudflare Access JWT verification

**Files:**
- Create: `admin_service/auth.py`
- Test: `tests/admin_auth_test.py`

**Interfaces:**
- Consumes: `admin_service.config.AdminSettings` from Task 1.
- Produces:
  - `@dataclass(frozen=True) Principal(email: str, subject: str, kind: str)` where `kind` is `"user"` or `"service_token"`
  - `class AccessVerifier` with `__init__(settings, *, jwks_client=None, leeway=30.0)`, `verify(token: str) -> Principal`, and `CF_JWT_HEADER = "cf-access-jwt-assertion"`
  - exceptions `AuthError(Exception)`, `TokenInvalid(AuthError)`, `NotAuthorized(AuthError)`

- [ ] **Step 1: Write the failing guard test**

Create `tests/admin_auth_test.py`:

```python
"""Security guard: the admin panel must verify Access JWTs, not trust headers.

Context / why this exists
-------------------------
An earlier admin service (unmerged branch `admin-service`, commit 42cfad0)
authenticated like this:

    principal = request.headers.get("cf-access-authenticated-user-email") or \
                request.headers.get("cf-access-jwt-assertion")
    if principal:
        return await call_next(request)

That is a truthiness test on an attacker-controlled header. `curl -H
'cf-access-authenticated-user-email: x'` was a full admin session, the literal
string "x" became the audit-log identity, a correctly-signed token from any
OTHER Access application passed, and expiry was never checked.

The blast radius is not abstract: this service holds a GitHub PAT and can
dispatch pipeline runs that cost roughly $8-11 each, and it triggers container
rebuilds on the origin host.

This guard locks in real verification: RS256 signature against the team JWKS,
plus aud, iss, exp, and an email allowlist.

  python3 -m unittest tests.admin_auth_test -v
"""

import datetime
import unittest

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from admin_service.auth import AccessVerifier, NotAuthorized, TokenInvalid
from admin_service.config import AdminSettings

TEAM = "testteam.cloudflareaccess.com"
AUD = "test-aud-tag"
ISS = f"https://{TEAM}"
ALLOWED = "operator@example.com"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


class _StubJWKClient:
    """Stands in for PyJWKClient so tests never touch the network."""

    def __init__(self, public_pem):
        self._public_pem = public_pem

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002 - signature parity
        class _Key:
            def __init__(self, pem):
                self.key = pem

        return _Key(self._public_pem)


class AccessVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_pem, cls.public_pem = _keypair()
        cls.evil_private_pem, _ = _keypair()
        cls.settings = AdminSettings.from_env({
            "ADMIN_CF_TEAM_DOMAIN": TEAM,
            "ADMIN_CF_AUD": AUD,
            "ADMIN_ALLOWED_EMAILS": ALLOWED,
        })

    def _verifier(self):
        return AccessVerifier(
            self.settings, jwks_client=_StubJWKClient(self.public_pem)
        )

    def _claims(self, **overrides):
        now = datetime.datetime.now(datetime.timezone.utc)
        claims = {
            "aud": [AUD],           # Cloudflare sends aud as an ARRAY
            "iss": ISS,
            "sub": "user-uuid-1",
            "email": ALLOWED,
            "iat": now,
            "nbf": now,
            "exp": now + datetime.timedelta(minutes=5),
        }
        claims.update(overrides)
        return claims

    def _token(self, claims=None, key=None, algorithm="RS256"):
        return jwt.encode(
            claims if claims is not None else self._claims(),
            key if key is not None else self.private_pem,
            algorithm=algorithm,
        )

    # --- the one case that must succeed -------------------------------------

    def test_accepts_a_valid_token(self):
        principal = self._verifier().verify(self._token())
        self.assertEqual(ALLOWED, principal.email)
        self.assertEqual("user-uuid-1", principal.subject)
        self.assertEqual("user", principal.kind)

    def test_accepts_a_service_token(self):
        claims = self._claims(email=None, sub="", common_name="ci-runner.access")
        claims.pop("email")
        verifier = AccessVerifier(
            AdminSettings.from_env({
                "ADMIN_CF_TEAM_DOMAIN": TEAM,
                "ADMIN_CF_AUD": AUD,
                "ADMIN_ALLOWED_EMAILS": ALLOWED,
                "ADMIN_ALLOWED_SERVICE_TOKENS": "ci-runner.access",
            }),
            jwks_client=_StubJWKClient(self.public_pem),
        )
        principal = verifier.verify(self._token(claims))
        self.assertEqual("service_token", principal.kind)
        self.assertEqual("ci-runner.access", principal.email)

    # --- everything below must be rejected ----------------------------------

    def test_rejects_a_forged_signature(self):
        token = self._token(key=self.evil_private_pem)
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(token)

    def test_rejects_alg_none(self):
        token = jwt.encode(self._claims(), None, algorithm="none")
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(token)

    def test_rejects_a_token_for_another_access_application(self):
        # A real, correctly-signed token from a different app. This is what the
        # presence check could not distinguish.
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(self._claims(aud=["someone-elses-app"])))

    def test_rejects_a_token_from_another_team(self):
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(
                self._token(self._claims(iss="https://evil.cloudflareaccess.com"))
            )

    def test_rejects_an_expired_token(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        claims = self._claims(
            iat=now - datetime.timedelta(hours=2),
            nbf=now - datetime.timedelta(hours=2),
            exp=now - datetime.timedelta(hours=1),
        )
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(claims))

    def test_rejects_a_token_with_no_expiry(self):
        claims = self._claims()
        claims.pop("exp")
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(claims))

    def test_rejects_garbage_and_empty_input(self):
        for value in ("", "not.a.jwt", "x", "a.b.c"):
            with self.subTest(value=value), self.assertRaises(TokenInvalid):
                self._verifier().verify(value)

    def test_rejects_a_valid_token_for_an_unlisted_email(self):
        # Correctly signed and scoped, but not an operator. Authentication and
        # authorization are separate failures.
        token = self._token(self._claims(email="stranger@example.com"))
        with self.assertRaises(NotAuthorized):
            self._verifier().verify(token)

    def test_email_matching_is_case_insensitive(self):
        token = self._token(self._claims(email=ALLOWED.upper()))
        self.assertEqual(ALLOWED, self._verifier().verify(token).email)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./venv/bin/python3 -m unittest tests.admin_auth_test -v`

Expected: FAIL at import — `ModuleNotFoundError: No module named 'admin_service.auth'`.

- [ ] **Step 3: Write the verifier**

Create `admin_service/auth.py`:

```python
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
        allowed_service_tokens: frozenset[str] | None = None,
    ) -> None:
        self._settings = settings
        self._leeway = leeway
        self._allowed_service_tokens = allowed_service_tokens or getattr(
            settings, "allowed_service_tokens", frozenset()
        )
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
                # rather than a silently skipped check.
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
            if common_name not in self._allowed_service_tokens:
                raise NotAuthorized(f"service token {common_name} is not authorized")
            return Principal(email=common_name, subject="", kind="service_token")

        raise NotAuthorized("token carries neither an email nor a service-token name")
```

- [ ] **Step 4: Add service-token config support**

The test constructs settings with `ADMIN_ALLOWED_SERVICE_TOKENS`. Add to `AdminSettings` in `admin_service/config.py`: a field `allowed_service_tokens: frozenset[str]` (place it immediately after `allowed_emails`), and in `from_env`, before the `return`:

```python
        service_tokens = frozenset(
            part.strip()
            for part in (env.get("ADMIN_ALLOWED_SERVICE_TOKENS") or "").split(",")
            if part.strip()
        )
```

then pass `allowed_service_tokens=service_tokens,` in the constructor call, directly after `allowed_emails=emails,`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/bin/python3 -m unittest tests.admin_auth_test -v`

Expected: all 12 tests PASS — one valid user, one valid service token, and ten rejections.

- [ ] **Step 6: Commit**

```bash
git add admin_service/auth.py admin_service/config.py tests/admin_auth_test.py
git commit -m "admin: verify Access JWTs instead of trusting a header

Verifies the RS256 signature against the team JWKS plus aud, iss, and expiry,
then authorizes the email against an allowlist. Authentication and
authorization fail separately so the audit trail can tell 'forged' from 'not
an operator'.

The earlier attempt at this service accepted any truthy cf-access-* header, so
a single curl -H was an admin session and the audit log recorded whatever
identity the caller typed. The guard test pins all of it: forged signature,
alg=none, another app's valid token, another team's issuer, expired, no exp,
garbage, and an unlisted email.

Verified against PyJWT 2.13.0 rather than assumed: Cloudflare sends aud as an
array, a string audience= handles it, and strict_aud is no longer a supported
kwarg -- passing it warns and is ignored, so it is not used."
```

---

### Task 3: State store

**Files:**
- Create: `admin_service/store.py`

**Interfaces:**
- Consumes: nothing (takes a path).
- Produces: `class AdminStore` with `__init__(db_path: Path)`, `record_action(principal, action, target, outcome, detail="") -> int`, `recent_actions(limit=50) -> list[dict]`, `record_balance(vendor, balance, balance_usd=None) -> None`, `balance_history(vendor, limit=90) -> list[dict]`, `should_alert(source, fingerprint) -> bool`, `mark_alerted(source, fingerprint) -> None`, and `close()`.

- [ ] **Step 1: Write the store**

Create `admin_service/store.py`:

```python
"""Durable state for the admin service.

Lives outside the git checkout on purpose. `scripts/deploy.sh` runs
`git reset --hard` and `git clean -fd` on every deploy, so anything stored
inside the working tree is deleted the next time someone pushes -- including
the audit log, which is exactly the record you want to survive an incident.

SQLite because it is stdlib, single-file, and the write volume here is a few
rows per day.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["AdminStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    principal   TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    outcome     TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS balance_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    vendor      TEXT NOT NULL,
    balance     INTEGER NOT NULL,
    balance_usd REAL
);
CREATE INDEX IF NOT EXISTS balance_vendor_ts ON balance_history(vendor, ts DESC);

-- One row per open incident. `fingerprint` identifies the incident (not the
-- day), so a source that stays broken for three weeks alerts once rather than
-- twenty-one times.
CREATE TABLE IF NOT EXISTS alert_state (
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (source, fingerprint)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AdminStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- audit ------------------------------------------------------------

    def record_action(
        self,
        principal: str,
        action: str,
        target: str | None,
        outcome: str,
        detail: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO audit_log (ts, principal, action, target, outcome, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), principal, action, target, outcome, detail),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # --- balances ---------------------------------------------------------

    def record_balance(
        self, vendor: str, balance: int, balance_usd: float | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO balance_history (ts, vendor, balance, balance_usd)"
            " VALUES (?, ?, ?, ?)",
            (_now(), vendor, int(balance), balance_usd),
        )
        self._conn.commit()

    def balance_history(self, vendor: str, limit: int = 90) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, balance, balance_usd FROM balance_history"
            " WHERE vendor = ? ORDER BY ts DESC LIMIT ?",
            (vendor, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    # --- alert dedup ------------------------------------------------------

    def should_alert(self, source: str, fingerprint: str) -> bool:
        """True the first time an incident is seen; False while it persists."""
        row = self._conn.execute(
            "SELECT 1 FROM alert_state WHERE source = ? AND fingerprint = ?",
            (source, fingerprint),
        ).fetchone()
        return row is None

    def mark_alerted(self, source: str, fingerprint: str) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO alert_state (source, fingerprint, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(source, fingerprint) DO UPDATE SET last_seen = excluded.last_seen",
            (source, fingerprint, now, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 2: Verify the store works**

Run:
```bash
./venv/bin/python3 -c "
import tempfile, pathlib
from admin_service.store import AdminStore
with tempfile.TemporaryDirectory() as d:
    s = AdminStore(pathlib.Path(d)/'t.sqlite3')
    s.record_action('me@x.com','rebuild_web',None,'success','12s')
    print('audit:', s.recent_actions()[0]['action'], s.recent_actions()[0]['outcome'])
    s.record_balance('scrapecreators', 11668)
    s.record_balance('scrapecreators', 11494)
    print('balances:', [r['balance'] for r in s.balance_history('scrapecreators')])
    print('first alert? ', s.should_alert('research','arxiv-zero'))
    s.mark_alerted('research','arxiv-zero')
    print('second alert?', s.should_alert('research','arxiv-zero'))
    print('different incident?', s.should_alert('research','other'))
    s.close()
"
```

Expected:
```
audit: rebuild_web success
balances: [11494, 11668]
first alert?  True
second alert? False
different incident? True
```

- [ ] **Step 3: Commit**

```bash
git add admin_service/store.py
git commit -m "admin: SQLite state outside the checkout

Audit log, balance history, and alert dedup. The path is outside the repo
because deploy.sh runs git reset --hard and git clean -fd on every push, which
would delete the audit trail precisely when it matters.

Dedup keys on (source, fingerprint) rather than date so a source that stays
broken for three weeks alerts once instead of twenty-one times -- the arXiv
outage would have produced 21 identical pages under a per-day scheme."
```

---

### Task 4: The FastAPI app

**Files:**
- Create: `admin_service/app.py`, `admin_service/requirements.txt`

**Interfaces:**
- Consumes: `AdminSettings`, `AccessVerifier`, `Principal`, `AdminStore`, `CF_JWT_HEADER`.
- Produces: `create_app(settings=None, verifier=None, store=None) -> FastAPI` (a factory, **not** a module-level `app` instance — see the note after the code); dependency `require_principal(request) -> Principal` exposed as `app.state.require_principal` for later plans; endpoints `GET /api/health` (unauthenticated liveness), `GET /api/me` (authenticated identity echo), `GET /api/audit` (recent actions).

- [ ] **Step 1: Write the requirements file**

Create `admin_service/requirements.txt`:

```
# Admin service dependencies. Deliberately separate from the pipeline's
# requirements.txt: this runs as its own systemd unit under its own venv, and
# the pipeline should not grow a web framework.
fastapi>=0.115
uvicorn[standard]>=0.30
PyJWT[crypto]>=2.9
httpx>=0.27
```

- [ ] **Step 2: Write the app**

Create `admin_service/app.py`:

```python
"""FastAPI application for the admin panel.

Auth is a dependency on every route except /api/health, which exists so
systemd and a human with SSH can tell the process is alive without holding a
token. Health returns liveness only -- never configuration or version detail
that would help someone probing the origin.
"""

from __future__ import annotations

import logging

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

    # Exposed so later plans can reuse the same dependency without rebuilding it.
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


```

The module exports `create_app` and nothing else at import time. uvicorn is invoked as
`--factory admin_service.app:create_app`, so the app is constructed during startup and
`AdminSettings.from_env()` can raise and stop the unit. A module-level `app = create_app()`
would build the app at *import* time, where a config error becomes an import traceback in
whatever tooling happens to import the module — including tests.

- [ ] **Step 3: Verify the app wires up and enforces auth**

Run:
```bash
./venv/bin/python3 -c "
import datetime, jwt, tempfile, pathlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from admin_service.app import create_app
from admin_service.auth import AccessVerifier
from admin_service.config import AdminSettings
from admin_service.store import AdminStore

k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
class Stub:
    def get_signing_key_from_jwt(self, t):
        return type('K',(),{'key':pub})()

s = AdminSettings.from_env({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com','ADMIN_CF_AUD':'aud1','ADMIN_ALLOWED_EMAILS':'op@x.com'})
d = tempfile.mkdtemp()
app = create_app(s, AccessVerifier(s, jwks_client=Stub()), AdminStore(pathlib.Path(d)/'t.sqlite3'))
c = TestClient(app)
now = datetime.datetime.now(datetime.timezone.utc)
good = jwt.encode({'aud':['aud1'],'iss':'https://t.cloudflareaccess.com','sub':'u1','email':'op@x.com','iat':now,'nbf':now,'exp':now+datetime.timedelta(minutes=5)}, priv, algorithm='RS256')

print('health (no auth):     ', c.get('/api/health').status_code)
print('me (no header):       ', c.get('/api/me').status_code)
print('me (forged header):   ', c.get('/api/me', headers={'cf-access-authenticated-user-email':'op@x.com'}).status_code)
print('me (garbage jwt):     ', c.get('/api/me', headers={'cf-access-jwt-assertion':'x'}).status_code)
print('me (valid jwt):       ', c.get('/api/me', headers={'cf-access-jwt-assertion':good}).status_code, c.get('/api/me', headers={'cf-access-jwt-assertion':good}).json())
print('docs disabled:        ', c.get('/docs').status_code)
"
```

Expected:
```
health (no auth):      200
me (no header):        401
me (forged header):    401
me (garbage jwt):      401
me (valid jwt):        200 {'email': 'op@x.com', 'kind': 'user', 'subject': 'u1'}
docs disabled:         404
```

The forged-header case returning **401** is the whole point: that exact request was a full admin session in the previous implementation.

- [ ] **Step 4: Commit**

```bash
git add admin_service/app.py admin_service/requirements.txt
git commit -m "admin: FastAPI app with auth on every route but health

Health is unauthenticated so systemd can probe liveness, and returns nothing
but status -- no version or config to help someone mapping the origin. Docs and
OpenAPI are off.

uvicorn runs the factory form so AdminSettings.from_env() raises at startup and
the unit fails, rather than importing a half-configured module and serving.

Verified the case that mattered: a forged cf-access-authenticated-user-email
header now gets 401 where the previous implementation gave a full session."
```

---

### Task 5: systemd unit and host provisioning

**Files:**
- Create: `deploy/aatf-admin.service.example`, `deploy/setup_admin_service.sh`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: `admin_service/requirements.txt`, the app factory.
- Produces: host user `aatfadmin`, venv at `/opt/aatf-admin/venv`, state dir `/var/lib/aatf-admin`, unit `aatf-admin.service`.

- [ ] **Step 1: Write the unit template**

Create `deploy/aatf-admin.service.example`:

```ini
# Admin panel service. Runs as an unprivileged dedicated user, binds loopback,
# and is reached only through the Cloudflare tunnel at admin.aatf.ai.
#
# aatfadmin is deliberately NOT in the docker group. Privileged actions go
# through sudo-allowlisted oneshot units (see the actions plan), so a flaw here
# cannot rebuild containers or reach the docker socket directly.
[Unit]
Description=AATF admin panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=aatfadmin
Group=aatfadmin
WorkingDirectory=/opt/aatf-admin

# Secrets and access configuration. Root-owned, mode 0640, group aatfadmin.
EnvironmentFile=/etc/aatf-admin/admin.env

ExecStart=/opt/aatf-admin/venv/bin/uvicorn \
    --factory admin_service.app:create_app \
    --host 127.0.0.1 --port 8200 \
    --no-server-header --log-level info
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/aatf-admin
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the provisioning script**

Create `deploy/setup_admin_service.sh`:

```bash
#!/bin/bash
# Provision the admin service on the host. Idempotent; safe to re-run.
#
#   sudo ./deploy/setup_admin_service.sh
#
# Does NOT write secrets. It creates /etc/aatf-admin/admin.env with empty
# placeholders on first run; fill those in before starting the service. The
# service refuses to start with them unset, which is the intended behavior.
set -euo pipefail

USER_NAME="aatfadmin"
APP_DIR="/opt/aatf-admin"
STATE_DIR="/var/lib/aatf-admin"
ENV_DIR="/etc/aatf-admin"
ENV_FILE="$ENV_DIR/admin.env"
REPO_DIR="/home/ubuntu/ai-news-aggregator"
UNIT_DST="/etc/systemd/system/aatf-admin.service"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Creating system user $USER_NAME (not in docker group, by design)..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR"
install -d -o root -g root -m 0755 "$APP_DIR"

echo "Syncing application code..."
# The service runs its own copy so a mid-deploy checkout cannot swap code under
# a live process.
rsync -a --delete \
    --exclude '__pycache__' \
    "$REPO_DIR/admin_service/" "$APP_DIR/admin_service/"

if [ ! -d "$APP_DIR/venv" ]; then
    echo "Creating venv..."
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/admin_service/requirements.txt"

install -d -o root -g "$USER_NAME" -m 0750 "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE with empty placeholders -- fill these in."
    cat > "$ENV_FILE" <<'ENVEOF'
# Cloudflare Access. Both required; the service will not start without them.
ADMIN_CF_TEAM_DOMAIN=
ADMIN_CF_AUD=
ADMIN_ALLOWED_EMAILS=

# Optional: comma-separated Access service-token common names.
ADMIN_ALLOWED_SERVICE_TOKENS=

# GitHub API. Fine-grained PAT, this repo only. Actions: read (add write only
# when workflow dispatch is enabled).
ADMIN_GITHUB_TOKEN=
ADMIN_GITHUB_REPO=flyryan/ai-news-aggregator

# Free balance probes for the dashboard.
SCRAPECREATORS_API_KEY=
TWITTERAPI_IO_KEY=

ADMIN_STATE_DB=/var/lib/aatf-admin/admin.sqlite3
ADMIN_REPO_DIR=/home/ubuntu/ai-news-aggregator
ENVEOF
    chown root:"$USER_NAME" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
fi

install -m 0644 "$HERE/aatf-admin.service.example" "$UNIT_DST"
systemctl daemon-reload
systemctl enable aatf-admin.service >/dev/null

echo
echo "Provisioned. Next:"
echo "  1. Fill in $ENV_FILE (the service refuses to start until you do)"
echo "  2. sudo systemctl restart aatf-admin"
echo "  3. curl -sS localhost:8200/api/health"
```

Then: `chmod +x deploy/setup_admin_service.sh`

- [ ] **Step 3: Verify both files parse**

Run:
```bash
bash -n deploy/setup_admin_service.sh && echo "script OK"
python3 -c "
import configparser
c = configparser.ConfigParser(strict=False)
c.read('deploy/aatf-admin.service.example')
print('unit sections:', c.sections())
print('User =', c['Service']['User'])
"
```

Expected: `script OK`, sections `['Unit', 'Service', 'Install']`, `User = aatfadmin`.

- [ ] **Step 4: Document it**

Append to `deploy/README.md`:

```markdown
## Admin panel service (2026-07-28)

`admin.aatf.ai` runs as a systemd unit on the origin host, not as a container.
The service's job is to rebuild its sibling container, which from inside a
container would require mounting the docker socket — effective root. Running as
a dedicated `aatfadmin` user that is deliberately *not* in the docker group,
with privileged work behind sudo-allowlisted oneshot units, is a real boundary
instead of an apparent one.

Provision with `sudo ./deploy/setup_admin_service.sh` (idempotent). It creates
the user, a venv at `/opt/aatf-admin`, state at `/var/lib/aatf-admin`, and
`/etc/aatf-admin/admin.env` with empty placeholders.

**Fill in the env file before starting.** `ADMIN_CF_TEAM_DOMAIN`, `ADMIN_CF_AUD`,
and `ADMIN_ALLOWED_EMAILS` are required and the service exits without them —
deliberately, so a misconfigured deploy fails closed rather than serving
unauthenticated.

Cloudflare side (dashboard): tunnel ingress `admin.aatf.ai` →
`http://127.0.0.1:8200`, plus a Zero Trust Access application over that hostname
with a policy for the operator's email. The AUD tag from that application goes
in `ADMIN_CF_AUD`.
```

- [ ] **Step 5: Commit**

```bash
git add deploy/aatf-admin.service.example deploy/setup_admin_service.sh deploy/README.md
git commit -m "admin: systemd unit and host provisioning

Dedicated aatfadmin user outside the docker group, loopback bind, hardened unit
(ProtectSystem=strict, NoNewPrivileges, one ReadWritePaths for state).

The provisioning script writes an env file of empty placeholders rather than
secrets, and the service refuses to start until they are filled. A deploy that
forgets configuration should fail visibly, not come up open."
```

---

### Task 6: Wire the guard test into CI and verify on the host

**Files:**
- Modify: `.github/workflows/tests.yml`

**Interfaces:**
- Consumes: everything above.
- Produces: CI coverage for the auth guard.

- [ ] **Step 1: Add the CI job**

The auth test needs PyJWT and cryptography, so it belongs in the job that pip-installs, not the stdlib-only guard job. In `.github/workflows/tests.yml`, in the same job as `image_client_retry_test`, extend the install step's package list with `"PyJWT[crypto]>=2.9"` and add:

```yaml
      - name: Admin Access-JWT guard
        run: python3 -m unittest tests.admin_auth_test -v
```

- [ ] **Step 2: Verify the workflow parses and the test runs clean**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml')); print('valid')"
./venv/bin/python3 -m unittest tests.admin_auth_test -v
```

Expected: `valid`, then all 12 auth tests PASS.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: run the admin Access-JWT guard

Sits in the dependency-installing job since it needs PyJWT and cryptography.
Reintroducing header-presence auth should fail the build, not wait to be found
on a live host holding a GitHub token."
```

- [ ] **Step 4: Deploy to the host and verify**

Requires the Cloudflare dashboard steps to be done first.

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && git fetch origin && git log --oneline -1 origin/main'
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && sudo ./deploy/setup_admin_service.sh'
```

Then fill `/etc/aatf-admin/admin.env` on the host (team domain, AUD, allowed email), and:

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'sudo systemctl restart aatf-admin && sleep 2 && systemctl is-active aatf-admin && curl -sS localhost:8200/api/health'
```

Expected: `active` and `{"status":"ok"}`.

- [ ] **Step 5: Verify the service refuses to start misconfigured**

This is the failure mode that matters; confirm it rather than assuming.

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 'bash -s' <<'REMOTE'
sudo cp /etc/aatf-admin/admin.env /tmp/admin.env.bak
sudo sed -i 's/^ADMIN_CF_AUD=.*/ADMIN_CF_AUD=/' /etc/aatf-admin/admin.env
sudo systemctl restart aatf-admin 2>&1 | tail -2
sleep 2
echo "state: $(systemctl is-active aatf-admin)"
sudo journalctl -u aatf-admin -n 5 --no-pager | tail -3
sudo cp /tmp/admin.env.bak /etc/aatf-admin/admin.env
sudo systemctl restart aatf-admin && sleep 2 && echo "restored: $(systemctl is-active aatf-admin)"
REMOTE
```

Expected: state `failed` or `activating (auto-restart)` with a `ConfigError` about `ADMIN_CF_AUD` in the journal, then `restored: active`.

- [ ] **Step 6: Verify the tunnel path end to end**

```bash
curl -sS -o /dev/null -w "unauthenticated: %{http_code}\n" https://admin.aatf.ai/api/me
```

Expected: **302** to the Cloudflare Access login (or 401/403), never 200. If this returns 200 with a body, stop — Access is not enforcing on the hostname.

Then open `https://admin.aatf.ai/api/me` in a browser, complete the Access login, and confirm it returns your email with `"kind": "user"`.

---

## Self-Review

**Spec coverage.** Implements spec §1 (placement and process model) and §2 (authentication) in full, plus the state store that §4's audit log and §5's balance trend depend on. Alert dedup — deferred from plan 1's self-review with an explicit note — lands here as the `alert_state` table and `should_alert`/`mark_alerted`, keyed on `(source, fingerprint)` so a three-week outage alerts once.

**Placeholders.** None. Every step has literal content or a runnable command with expected output.

**Type/name consistency.** `AdminSettings` fields defined in Task 1 are used unchanged in Tasks 2, 4, and 5; `allowed_service_tokens` is added in Task 2 Step 4 because Task 2's test requires it, and the ordering note ("immediately after `allowed_emails`") keeps the dataclass positional-construction valid. `Principal(email, subject, kind)` is defined in Task 2 and destructured identically in Task 4's `/api/me`. `CF_JWT_HEADER` is defined once in `auth.py` and imported by `app.py` rather than re-typed. `AdminStore` method names in Task 3 match their call sites in Task 4 (`recent_actions`) and the Task 3 Step 2 smoke test.

**Two things deliberately deferred.** The four privileged actions are plan 3 — this plan intentionally ships a service that can do nothing but identify you, so the auth boundary can be verified before anything behind it is worth attacking. The dashboard UI is plan 4.

**One risk worth naming.** Task 5's provisioning `rsync`s `admin_service/` into `/opt/aatf-admin` rather than running from the checkout, so a `git reset --hard` mid-request cannot swap code under a live process. The cost is that deploying admin changes requires re-running the setup script; that is called out in the README text and becomes a maintenance action in plan 3.
