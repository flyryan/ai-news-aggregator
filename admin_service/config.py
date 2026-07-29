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
    allowed_service_tokens: frozenset[str]
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

        service_tokens = frozenset(
            part.strip()
            for part in (env.get("ADMIN_ALLOWED_SERVICE_TOKENS") or "").split(",")
            if part.strip()
        )

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
            allowed_service_tokens=service_tokens,
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
