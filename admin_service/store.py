"""Durable state for the admin service.

Lives outside the git checkout on purpose. `scripts/deploy.sh` runs
`git reset --hard` and `git clean -fd` on every deploy, so anything stored
inside the working tree is deleted the next time someone pushes -- including
the audit log, which is exactly the record you want to survive an incident.

SQLite because it is stdlib, single-file, and the write volume here is a few
rows per day.
"""

from __future__ import annotations

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
