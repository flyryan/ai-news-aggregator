"""Start and observe privileged host actions.

The service holds no privilege of its own: it shells out to a single
sudo-allowlisted wrapper that starts pre-declared systemd units. Everything
here is about naming an action, watching a unit, and recording what happened.

Completion is read from systemd, never inferred from log output. A build that
was OOM-killed produces the same silence as a build that is still running, and
treating silence as success is how a broken deploy gets reported green.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from .store import AdminStore

__all__ = ["ACTIONS", "ActionSpec", "ActionStatus", "ActionRunner", "ActionError"]

logger = logging.getLogger("admin_service.actions")

WRAPPER = "/usr/local/sbin/aatf-admin-trigger"


class ActionError(RuntimeError):
    """An action could not be started, or was asked for with bad input."""


@dataclass(frozen=True)
class ActionSpec:
    name: str
    unit: str
    needs_arg: bool
    description: str
    danger: str  # "low" | "medium" | "high" -- drives UI confirmation
    internal: bool = False  # driven by a dedicated flow, hidden from the generic action list


ACTIONS: dict[str, ActionSpec] = {
    "rebuild-web": ActionSpec(
        name="rebuild-web",
        unit="aatf-rebuild-web.service",
        needs_arg=False,
        description="Rebuild and restart the public site container",
        danger="high",  # replaces the only container serving news.aatf.ai
    ),
    "git-sync": ActionSpec(
        name="git-sync",
        unit="aatf-git-sync.service",
        needs_arg=False,
        description="Sync the host checkout to a verified origin/main",
        danger="medium",
    ),
    "hero-regen": ActionSpec(
        name="hero-regen",
        unit="aatf-hero-regen@{arg}.service",
        needs_arg=True,
        description="Regenerate a hero image into the preview area",
        danger="medium",  # spends an image-model call
    ),
    "admin-redeploy": ActionSpec(
        name="admin-redeploy",
        unit="aatf-admin-redeploy.service",
        needs_arg=False,
        description="Re-sync and restart the admin service itself",
        danger="medium",
    ),
    "promote": ActionSpec(
        name="promote",
        unit="aatf-promote@{arg}.service",
        needs_arg=True,  # the preview job id, <kind>-YYYY-MM-DD
        description="Publish an approved preview to the live site",
        danger="high",  # signed commit to main; deploys to the public site
        internal=True,  # the Preview panel drives this, with its own confirm
    ),
}


@dataclass(frozen=True)
class ActionStatus:
    unit: str
    active_state: str      # activating | active | inactive | failed
    result: str            # success | exit-code | timeout | oom-kill | signal | ...
    exit_code: int | None
    finished: bool

    @property
    def succeeded(self) -> bool:
        return self.finished and self.result == "success" and (self.exit_code in (0, None))


class ActionRunner:
    def __init__(self, store: AdminStore, *, wrapper: str = WRAPPER) -> None:
        self._store = store
        self._wrapper = wrapper

    # --- starting ---------------------------------------------------------

    def start(self, action: str, principal: str, arg: str | None = None) -> str:
        spec = ACTIONS.get(action)
        if spec is None:
            raise ActionError(f"unknown action: {action}")
        if spec.needs_arg and not arg:
            raise ActionError(f"{action} requires an argument")
        if not spec.needs_arg and arg:
            raise ActionError(f"{action} takes no argument")

        command = ["sudo", "-n", self._wrapper, action]
        if arg:
            command.append(arg)

        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._store.record_action(principal, action, arg, "error", str(exc))
            raise ActionError(f"could not start {action}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[:500]
            self._store.record_action(principal, action, arg, "refused", detail)
            raise ActionError(f"{action} refused: {detail}")

        unit = completed.stdout.strip() or spec.unit.format(arg=arg or "")
        self._store.record_action(principal, action, arg, "started", unit)
        logger.info("action %s started by %s -> %s", action, principal, unit)
        return unit

    # --- observing --------------------------------------------------------

    def status(self, unit: str) -> ActionStatus:
        """Read terminal state from systemd rather than guessing from logs."""
        try:
            completed = subprocess.run(
                [
                    "systemctl", "show", unit,
                    "--property=ActiveState",
                    "--property=Result",
                    "--property=ExecMainStatus",
                ],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ActionStatus(unit, "unknown", "unknown", None, False)

        fields: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value

        active_state = fields.get("ActiveState", "unknown")
        raw_exit = fields.get("ExecMainStatus", "")
        try:
            exit_code: int | None = int(raw_exit)
        except (TypeError, ValueError):
            exit_code = None

        return ActionStatus(
            unit=unit,
            active_state=active_state,
            result=fields.get("Result", "unknown"),
            exit_code=exit_code,
            finished=active_state in ("inactive", "failed"),
        )

    def logs(self, unit: str, lines: int = 200) -> list[str]:
        """Recent journal output for a unit.

        Requires membership in systemd-journal; no sudo. Returns a single
        explanatory line rather than raising if the journal is unreadable --
        a missing log view should not break the status page.
        """
        if shutil.which("journalctl") is None:
            return ["journalctl unavailable on this host (expected in local dev)"]

        try:
            completed = subprocess.run(
                ["journalctl", "-u", unit, "-n", str(max(1, min(lines, 2000))),
                 "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"could not read journal: {type(exc).__name__}"]

        if completed.returncode != 0:
            return [f"could not read journal for {unit}: {completed.stderr.strip()[:200]}"]
        return completed.stdout.splitlines()
