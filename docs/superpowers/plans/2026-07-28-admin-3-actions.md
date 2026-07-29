# Maintenance Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator run four privileged host actions from the panel — rebuild the web container, sync the checkout to a verified `origin/main`, dispatch a pipeline run, and regenerate a hero image — with live output, mutual exclusion, and an audit trail.

**Architecture:** The admin service never runs `docker` or `git` itself. Each action is a pre-declared systemd oneshot unit, started through a single sudo-allowlisted wrapper. Because the unit is not a child of the admin process, a rebuild survives the admin service restarting, and `journalctl` gives live output for free. A `flock` mutex held *inside* the unit — not in Python — serialises privileged work and is released by the kernel if anything dies.

**Tech Stack:** systemd 255 (verified on host), `flock` (verified present), sudo, bash, FastAPI, httpx, stdlib `unittest`.

## Global Constraints

- `aatfadmin` is **not** in the `docker` group and must never be added to it. The whole point of the separate user is that `ubuntu` already has `sudo(27)` and `docker(988)` — effective root — and the panel should not inherit that.
- The sudo allowlist is **exact commands, no wildcards on arguments that select the unit**. A rule permitting `systemctl start aatf-*` lets a caller start any unit whose name they can create.
- **Always scope the compose rebuild to the service name** (`ai-news-aggregator`). An unscoped `docker compose up -d --build` rebuilds every service in the project.
- **Never infer success from log output ending.** Read the unit's `Result` and `ExecMainStatus` from `systemctl show`. A crashed or OOM-killed build looks exactly like a slow one in a log tail.
- Every action writes an audit row (principal, action, target, outcome) via `AdminStore.record_action` from plan 2, and also to the journal.
- Git sync must route through `scripts/verified_sync.sh` (plan 0) so it enforces the signed-commit gate.
- Commits must be SSH-signed.

---

## Prerequisites

Plan 0 (`scripts/verified_sync.sh` exists) and plan 2 (`aatfadmin`, `AdminStore`, `require_principal`) must be complete.

**Host facts verified 2026-07-28:** systemd 255 (255.4-1ubuntu8.16), `/usr/bin/systemd-run` and `/usr/bin/flock` present, `systemd-journal` group exists (gid 999), `ai-news-aggregator` container up and healthy on `0.0.0.0:7100->80`.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/units/aatf-rebuild-web.service` (create) | Oneshot: build then restart the web container. |
| `deploy/units/aatf-git-sync.service` (create) | Oneshot: verified sync of the checkout. |
| `deploy/units/aatf-hero-regen@.service` (create) | Templated oneshot: regenerate a hero for one date. |
| `deploy/units/aatf-admin-redeploy.service` (create) | Oneshot: re-sync the admin service's own code and restart it. |
| `deploy/aatf-admin-trigger` (create) | The only thing `aatfadmin` may sudo. Validates the action name, then starts the matching unit. |
| `deploy/aatf-admin.sudoers` (create) | Exact-command allowlist. |
| `admin_service/actions.py` (create) | Start actions, poll status, stream journal output. |
| `admin_service/github.py` (create) | GitHub API client: dispatch, run status, logs. |
| `admin_service/app.py` (modify) | Action endpoints. |
| `tests/admin_actions_test.py` (create) | Guard tests: action allowlist, no shell injection, sudoers has no wildcards. |
| `deploy/setup_admin_service.sh` (modify) | Install units, wrapper, and sudoers. |

---

### Task 1: The oneshot units

**Files:**
- Create: `deploy/units/aatf-rebuild-web.service`, `deploy/units/aatf-git-sync.service`, `deploy/units/aatf-hero-regen@.service`, `deploy/units/aatf-admin-redeploy.service`

**Interfaces:**
- Consumes: `scripts/verified_sync.sh` (plan 0).
- Produces: four unit names — `aatf-rebuild-web.service`, `aatf-git-sync.service`, `aatf-hero-regen@<date>.service`, `aatf-admin-redeploy.service`. All run as `root` (they need docker and the checkout), all take `/var/lib/aatf-admin/privileged.lock`.

- [ ] **Step 1: Write the rebuild unit**

Create `deploy/units/aatf-rebuild-web.service`:

```ini
# Rebuild and restart the public web container.
#
# Runs as root because it drives docker. The admin service cannot do this
# itself: aatfadmin is deliberately not in the docker group, since docker group
# membership is effective root and would make an auth bug on the panel a host
# compromise.
#
# Not a child of the admin process, so restarting or redeploying the panel
# mid-build does not kill the build.
[Unit]
Description=AATF rebuild web container

[Service]
Type=oneshot
User=root
WorkingDirectory=/home/ubuntu/ai-news-aggregator

# Build first, and only swap containers if the build succeeded. `up -d --build`
# in one step can tear down a working container and then fail to replace it,
# and restart: unless-stopped will loop the broken image.
#
# The service name is explicit: an unscoped `up -d` rebuilds every service in
# the compose project, including anything added later.
ExecStart=/usr/bin/flock /var/lib/aatf-admin/privileged.lock \
    /usr/bin/docker compose -f docker-compose.web.yml build ai-news-aggregator
ExecStart=/usr/bin/flock /var/lib/aatf-admin/privileged.lock \
    /usr/bin/docker compose -f docker-compose.web.yml up -d ai-news-aggregator

TimeoutStartSec=1200
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 2: Write the git sync unit**

Create `deploy/units/aatf-git-sync.service`:

```ini
# Sync the host checkout to a signature-verified origin/main.
#
# Delegates to scripts/verified_sync.sh so this path enforces the same
# CWE-345 signed-commit gate as the deploy webhook. Never bypass it.
[Unit]
Description=AATF verified git sync

[Service]
Type=oneshot
User=root
WorkingDirectory=/home/ubuntu/ai-news-aggregator

ExecStart=/usr/bin/flock /var/lib/aatf-admin/privileged.lock \
    /home/ubuntu/ai-news-aggregator/scripts/verified_sync.sh

TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 3: Write the hero regeneration unit**

Create `deploy/units/aatf-hero-regen@.service`:

```ini
# Regenerate the hero image for one report date. %i is the date.
#
# Writes into the preview area, not web/data: hero.webp is git-tracked, so a
# direct write would be reverted by the next deploy. Promotion is a separate,
# explicit step (see the preview plan).
#
# Runs as ubuntu rather than root: it only needs the pipeline venv and the
# preview directory, and the image API key is scoped to that user's env file.
[Unit]
Description=AATF hero regeneration for %i

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/ai-news-aggregator
EnvironmentFile=/etc/aatf-admin/hero.env

ExecStart=/usr/bin/flock /var/lib/aatf-admin/privileged.lock \
    /home/ubuntu/ai-news-aggregator/venv/bin/python3 \
    scripts/regenerate_hero.py %i -y \
    --web-dir /var/lib/aatf-admin/previews/hero-%i/web

TimeoutStartSec=900
StandardOutput=journal
StandardError=journal
```

`--web-dir` is the existing flag and it redirects the whole output tree: the script builds
both `web_dir/data/<date>/hero.webp` (`scripts/regenerate_hero.py:281`) and
`web_dir/data/<date>/summary.json` (`:238-240`) beneath it, so pointing it at a preview
directory keeps the live tree untouched with no code change. `-y` skips the interactive
confirmation, which would otherwise hang a non-interactive unit forever.

**The preview directory must be seeded before this runs.** The script reads the existing
`summary.json` to build its prompt (`:228-230`), so the unit's caller copies
`web/data/<date>/summary.json` into the preview tree first. That is a task in the preview
plan; until then this unit fails fast with a missing-summary error rather than writing
anywhere near the live tree, which is the correct failure.

- [ ] **Step 4: Write the admin self-redeploy unit**

Create `deploy/units/aatf-admin-redeploy.service`:

```ini
# Re-sync the admin service's own code from the checkout and restart it.
#
# The service runs from /opt/aatf-admin rather than the checkout so a
# git reset --hard cannot swap code under a live process. The cost is that
# admin changes need this step; this unit is that step.
#
# Deliberately does NOT take the privileged lock: it restarts the admin service,
# which would otherwise deadlock against a lock the admin service is waiting on.
[Unit]
Description=AATF admin self-redeploy

[Service]
Type=oneshot
User=root

ExecStart=/usr/bin/rsync -a --delete --exclude __pycache__ \
    /home/ubuntu/ai-news-aggregator/admin_service/ /opt/aatf-admin/admin_service/
ExecStart=/opt/aatf-admin/venv/bin/pip install --quiet -r \
    /home/ubuntu/ai-news-aggregator/admin_service/requirements.txt
ExecStart=/usr/bin/systemctl restart aatf-admin.service

TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 5: Verify all four parse as valid unit files**

Run:
```bash
python3 - <<'PY'
import configparser, pathlib
for p in sorted(pathlib.Path('deploy/units').glob('*.service')):
    c = configparser.ConfigParser(strict=False, allow_no_value=True)
    c.optionxform = str
    c.read(p)
    svc = c['Service']
    n = sum(1 for k in svc if k == 'ExecStart')
    print(f"{p.name:34s} Type={svc.get('Type'):8s} User={svc.get('User'):9s} ExecStart lines={n}")
PY
```

Expected: four units listed, all `Type=oneshot`, users `root`/`root`/`ubuntu`/`root`.

Note `configparser` keeps only the last duplicate key, so the reported `ExecStart lines=1` for multi-ExecStart units is a parser artifact, not a problem — systemd handles repeated `ExecStart` in `Type=oneshot` by running them in order.

- [ ] **Step 6: Commit**

```bash
git add deploy/units/
git commit -m "admin: systemd oneshot units for privileged actions

Each action is a pre-declared unit rather than a command the service composes,
so the sudo allowlist can name exact commands and the panel never holds docker
access.

Units are not children of the admin process: a rebuild survives the panel
restarting, and journalctl gives live output with no pipe to break. A flock
inside ExecStart serialises them and is released by the kernel on crash --
unlike a lock held in Python, which leaks on SIGKILL.

Rebuild is build-then-up and scoped to the service name: one step can tear
down a working container and fail to replace it, and unscoped up -d rebuilds
everything in the project."
```

---

### Task 2: The sudo wrapper and allowlist

**Files:**
- Create: `deploy/aatf-admin-trigger`, `deploy/aatf-admin.sudoers`
- Test: `tests/admin_actions_test.py`

**Interfaces:**
- Consumes: the four unit names from Task 1.
- Produces: `/usr/local/sbin/aatf-admin-trigger <action> [arg]` where action ∈ `rebuild-web | git-sync | hero-regen | admin-redeploy`. Prints the started unit name on stdout. Exit 0 started, 2 unknown action, 3 invalid argument.

- [ ] **Step 1: Write the failing guard test**

Create `tests/admin_actions_test.py`:

```python
"""Security guard: the privileged-action path must not be widenable.

Context / why this exists
-------------------------
The admin panel runs as `aatfadmin`, a user deliberately kept out of the
`docker` group -- on this host `ubuntu` is in both `sudo` and `docker`, which
is effective root, and the panel holds a GitHub token that can spend money.
The only privilege `aatfadmin` has is one sudo entry pointing at one wrapper
script.

That makes two things load-bearing:

1. The sudoers entry must name exact commands. A rule like
   `systemctl start aatf-*` lets a caller start any unit whose name they can
   arrange to exist.
2. The wrapper must validate its action against a fixed allowlist and must not
   interpolate caller input into a shell. The date argument for hero-regen
   reaches a systemd instance name; `2026-01-01; rm -rf /` must be rejected by
   pattern, not escaped and hoped for.

  python3 -m unittest tests.admin_actions_test -v
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "deploy" / "aatf-admin-trigger"
SUDOERS = REPO_ROOT / "deploy" / "aatf-admin.sudoers"
UNITS_DIR = REPO_ROOT / "deploy" / "units"

EXPECTED_ACTIONS = {"rebuild-web", "git-sync", "hero-regen", "admin-redeploy"}


class SudoersTest(unittest.TestCase):
    def test_sudoers_exists(self):
        self.assertTrue(SUDOERS.is_file(), "deploy/aatf-admin.sudoers must exist")

    def test_sudoers_grants_only_the_wrapper(self):
        body = SUDOERS.read_text()
        commands = re.findall(r"NOPASSWD:\s*(.+)$", body, re.M)
        self.assertTrue(commands, "sudoers must contain at least one NOPASSWD command")
        for command in commands:
            self.assertIn(
                "/usr/local/sbin/aatf-admin-trigger",
                command,
                f"sudoers grants something other than the wrapper: {command!r}. "
                "Every privileged path must funnel through the one validated script.",
            )

    def test_sudoers_has_no_wildcards(self):
        body = "\n".join(
            line for line in SUDOERS.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        self.assertNotIn(
            "*", body,
            "a wildcard in the sudoers command turns an exact allowlist into a "
            "pattern match; enumerate the permitted invocations instead",
        )

    def test_sudoers_does_not_grant_all(self):
        body = SUDOERS.read_text()
        self.assertNotRegex(
            body, r"NOPASSWD:\s*ALL",
            "NOPASSWD: ALL is unrestricted root for the panel user",
        )


class WrapperTest(unittest.TestCase):
    def setUp(self):
        if not WRAPPER.is_file():
            self.skipTest("wrapper not yet written")
        self.body = WRAPPER.read_text()

    def test_wrapper_is_executable_bash_with_strict_mode(self):
        self.assertTrue(self.body.startswith("#!/bin/bash"))
        self.assertIn("set -euo pipefail", self.body)

    def test_wrapper_enumerates_exactly_the_known_actions(self):
        for action in EXPECTED_ACTIONS:
            self.assertIn(
                action, self.body, f"wrapper does not handle action {action!r}"
            )

    def test_wrapper_validates_the_date_argument(self):
        # A date reaches a systemd instance name; it must be pattern-checked.
        self.assertRegex(
            self.body,
            r"\[0-9\]\{4\}-\[0-9\]\{2\}-\[0-9\]\{2\}|[0-9]{4}-[0-9]{2}-[0-9]{2}",
            "wrapper must validate the hero date against a YYYY-MM-DD pattern",
        )

    def test_wrapper_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(WRAPPER)], capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_wrapper_rejects_unknown_actions_before_touching_systemctl(self):
        # The case statement must be the first thing that sees the action.
        case_pos = self.body.find("case")
        systemctl_pos = self.body.find("systemctl")
        self.assertNotEqual(-1, case_pos, "wrapper must dispatch via case")
        self.assertLess(
            case_pos, systemctl_pos,
            "the action allowlist must be evaluated before any systemctl call",
        )


class UnitsTest(unittest.TestCase):
    def test_every_action_has_a_unit(self):
        names = {p.name for p in UNITS_DIR.glob("*.service")}
        expected = {
            "aatf-rebuild-web.service",
            "aatf-git-sync.service",
            "aatf-hero-regen@.service",
            "aatf-admin-redeploy.service",
        }
        self.assertEqual(expected, names)

    def test_privileged_units_take_the_lock(self):
        # admin-redeploy is exempt: it restarts the admin service and would
        # deadlock against a lock that service is waiting on.
        for name in ("aatf-rebuild-web.service", "aatf-git-sync.service",
                     "aatf-hero-regen@.service"):
            body = (UNITS_DIR / name).read_text()
            self.assertIn(
                "flock", body,
                f"{name} must serialise via flock or two actions can race on the "
                "same checkout -- a git reset under a running build",
            )

    def test_rebuild_is_scoped_to_the_service_name(self):
        body = (UNITS_DIR / "aatf-rebuild-web.service").read_text()
        self.assertIn("ai-news-aggregator", body)
        self.assertNotRegex(
            body, r"up -d --build\s*$",
            "unscoped `up -d --build` rebuilds every service in the project",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.admin_actions_test -v`

Expected: `SudoersTest` fails (file missing), `WrapperTest` skips, `UnitsTest` passes if Task 1 landed.

- [ ] **Step 3: Write the wrapper**

Create `deploy/aatf-admin-trigger`:

```bash
#!/bin/bash
# The only command aatfadmin may run under sudo.
#
# Maps a fixed set of action names onto pre-declared systemd units. The action
# is matched against a literal case list before anything else happens, and the
# only free-form input -- the hero date -- is pattern-validated before it can
# reach a systemd instance name.
#
# Usage: aatf-admin-trigger <action> [arg]
# Exit:  0 started | 2 unknown action | 3 invalid argument
set -euo pipefail

ACTION="${1:-}"
ARG="${2:-}"

start_unit() {
    # --no-block so the caller returns immediately; the panel polls status.
    /usr/bin/systemctl start --no-block "$1"
    echo "$1"
}

case "$ACTION" in
    rebuild-web)
        start_unit aatf-rebuild-web.service
        ;;
    git-sync)
        start_unit aatf-git-sync.service
        ;;
    admin-redeploy)
        start_unit aatf-admin-redeploy.service
        ;;
    hero-regen)
        # The date becomes a systemd instance name. Validate by pattern rather
        # than escaping: anything that is not exactly YYYY-MM-DD is refused.
        if ! printf '%s' "$ARG" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
            echo "invalid date: expected YYYY-MM-DD" >&2
            exit 3
        fi
        start_unit "aatf-hero-regen@${ARG}.service"
        ;;
    *)
        echo "unknown action: ${ACTION}" >&2
        exit 2
        ;;
esac
```

Then: `chmod +x deploy/aatf-admin-trigger`

- [ ] **Step 4: Write the sudoers file**

Create `deploy/aatf-admin.sudoers`:

```
# Privileges for the admin panel service user.
#
# Exactly one command, enumerated per action. No wildcards: a rule like
# `systemctl start aatf-*` would let the caller start any unit whose name they
# can arrange to exist, which is a very different grant from "these four".
#
# The hero-regen date is validated inside the wrapper, so the argument is left
# open here -- that is the one place a wildcard is unavoidable, and it is why
# the wrapper pattern-checks before use.
#
# Install to /etc/sudoers.d/aatf-admin with mode 0440, and validate with
# `visudo -c -f` before activating.
aatfadmin ALL=(root) NOPASSWD: /usr/local/sbin/aatf-admin-trigger rebuild-web
aatfadmin ALL=(root) NOPASSWD: /usr/local/sbin/aatf-admin-trigger git-sync
aatfadmin ALL=(root) NOPASSWD: /usr/local/sbin/aatf-admin-trigger admin-redeploy

# hero-regen takes a date, so this one rule must accept an argument. The glob is
# as narrow as sudoers can express -- it matches the shape of a date and nothing
# with a slash, a space, or a shell metacharacter -- and the wrapper re-validates
# against ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ before the value reaches a unit name.
# Sudoers globs are fnmatch, not regex: [0-9] is a character class, but there is
# no repetition operator, hence the spelled-out form.
aatfadmin ALL=(root) NOPASSWD: /usr/local/sbin/aatf-admin-trigger hero-regen [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]

# Read-only status for the panel's action view.
aatfadmin ALL=(root) NOPASSWD: /usr/bin/systemctl show aatf-rebuild-web.service
aatfadmin ALL=(root) NOPASSWD: /usr/bin/systemctl show aatf-git-sync.service
aatfadmin ALL=(root) NOPASSWD: /usr/bin/systemctl show aatf-admin-redeploy.service
```

**Verified on the host rather than assumed.** A trailing `""` does *not* mean "exactly one
argument" — tested with a throwaway user and rule, `cmd ""` denied every invocation
including the single-argument case, because it matches one *empty-string* argument
literally. The bare form (`cmd action` with no trailing token) behaves as intended: allowed
with no further arguments, denied with any. That is why the three no-argument actions use
the bare form and `hero-regen` uses an explicit character-class glob.

This is also why the guard test in Step 1 checks for `*` specifically rather than any glob
character: `[0-9]` constrains, `*` does not.

Journal reads do not need sudo — add `aatfadmin` to the `systemd-journal` group instead
(Task 5).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.admin_actions_test -v`

Expected: all PASS.

- [ ] **Step 6: Verify the wrapper's rejection paths by hand**

Run:
```bash
bash deploy/aatf-admin-trigger 2>&1; echo "no action -> exit $?"
bash deploy/aatf-admin-trigger bogus 2>&1; echo "unknown -> exit $?"
bash deploy/aatf-admin-trigger hero-regen '2026-01-01; rm -rf /' 2>&1; echo "injection -> exit $?"
bash deploy/aatf-admin-trigger hero-regen 'notadate' 2>&1; echo "bad date -> exit $?"
```

Expected: exits 2, 2, 3, 3. The injection attempt must be refused by the pattern check, and no `systemctl` call is reached. (Running these locally is safe: `systemctl start` would only be attempted on a valid action, and macOS has no systemctl — but do not run a valid action locally.)

- [ ] **Step 7: Commit**

```bash
git add deploy/aatf-admin-trigger deploy/aatf-admin.sudoers tests/admin_actions_test.py
git commit -m "admin: sudo-allowlisted action wrapper

One command, four enumerated actions, no wildcards in the sudoers rule. A
pattern like 'systemctl start aatf-*' would grant far more than four units.

The hero date reaches a systemd instance name, so it is pattern-validated
against YYYY-MM-DD before use rather than escaped -- '2026-01-01; rm -rf /'
exits 3 without reaching systemctl.

Guard test pins all of it: exact-command sudoers, no wildcards, no NOPASSWD:
ALL, action allowlist evaluated before any systemctl call, every action has a
unit, privileged units take the lock, and the rebuild stays scoped to one
service."
```

---

### Task 3: The actions module

**Files:**
- Create: `admin_service/actions.py`

**Interfaces:**
- Consumes: `AdminStore` (plan 2), the wrapper from Task 2.
- Produces:
  - `ACTIONS: dict[str, ActionSpec]` keyed by `rebuild-web | git-sync | hero-regen | admin-redeploy`
  - `@dataclass(frozen=True) ActionSpec(name, unit, needs_arg, description, danger)`
  - `@dataclass(frozen=True) ActionStatus(unit, active_state, result, exit_code, finished)`
  - `class ActionRunner` with `start(action, principal, arg=None) -> str`, `status(unit) -> ActionStatus`, `logs(unit, lines=200) -> list[str]`
  - exception `ActionError(RuntimeError)`

- [ ] **Step 1: Write the module**

Create `admin_service/actions.py`:

```python
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
        completed = subprocess.run(
            [
                "systemctl", "show", unit,
                "--property=ActiveState",
                "--property=Result",
                "--property=ExecMainStatus",
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
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
            return ["journalctl unavailable on this host"]

        completed = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(max(1, min(lines, 2000))),
             "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if completed.returncode != 0:
            return [f"could not read journal for {unit}: {completed.stderr.strip()[:200]}"]
        return completed.stdout.splitlines()
```

- [ ] **Step 2: Verify the module's validation logic**

Run:
```bash
./venv/bin/python3 -c "
import tempfile, pathlib
from admin_service.actions import ActionRunner, ActionError, ACTIONS
from admin_service.store import AdminStore
d = tempfile.mkdtemp()
r = ActionRunner(AdminStore(pathlib.Path(d)/'t.sqlite3'), wrapper='/bin/false')
print('known actions:', sorted(ACTIONS))
for action, arg, label in [
    ('nope', None, 'unknown action'),
    ('hero-regen', None, 'missing required arg'),
    ('rebuild-web', '2026-01-01', 'unexpected arg'),
]:
    try:
        r.start(action, 'me@x.com', arg); print(f'{label:22s} STARTED (BAD)')
    except ActionError as e:
        print(f'{label:22s} refused: {e}')
# wrapper=/bin/false simulates a refusal
try:
    r.start('rebuild-web', 'me@x.com')
except ActionError as e:
    print('wrapper refusal      ->', str(e)[:40])
"
```

Expected: the three validation cases refused with clear messages, then a wrapper refusal. Note the audit log records the refusal too — a rejected attempt is exactly what you want recorded.

- [ ] **Step 3: Commit**

```bash
git add admin_service/actions.py
git commit -m "admin: action runner over the sudo wrapper

Validates the action and its argument locally, shells to the wrapper, and
records every attempt -- including refusals, which are the ones worth having
in an audit log.

Completion comes from systemctl show (ActiveState, Result, ExecMainStatus),
never from log output ending: an OOM-killed build and a slow build produce
identical silence, and only one of them is success."
```

---

### Task 4: GitHub client and action endpoints

**Files:**
- Create: `admin_service/github.py`
- Modify: `admin_service/app.py`

**Interfaces:**
- Consumes: `AdminSettings`, `ActionRunner`, `require_principal`.
- Produces:
  - `class GitHubClient` with `list_runs(workflow="daily-pipeline.yml", limit=30)`, `dispatch(workflow, inputs)`, `in_flight(workflow) -> dict | None`, `job_logs(run_id) -> str`
  - endpoints `GET /api/actions`, `POST /api/actions/{action}`, `GET /api/actions/status/{unit}`, `GET /api/actions/logs/{unit}`, `POST /api/pipeline/dispatch`

- [ ] **Step 1: Write the GitHub client**

Create `admin_service/github.py`:

```python
"""Minimal GitHub REST client for the admin panel.

Only what the panel needs: list runs, dispatch, and read job logs. Logs are the
reason a token is mandatory rather than optional -- run metadata is public on
this repo, but the logs endpoint returns 403 unauthenticated.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

__all__ = ["GitHubClient", "GitHubError"]

logger = logging.getLogger("admin_service.github")

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, token: str = "", *, timeout: float = 20.0) -> None:
        self._repo = repo
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{API}/repos/{self._repo}{path}"
        try:
            response = httpx.get(
                url, headers=self._headers(), params=params, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if response.status_code == 403 and not self._token:
            raise GitHubError(
                "GitHub returned 403. Run metadata is public on this repo but logs "
                "are not; set ADMIN_GITHUB_TOKEN."
            )
        if response.status_code >= 400:
            raise GitHubError(f"GitHub {response.status_code} for {path}")
        return response.json()

    def list_runs(self, workflow: str = "daily-pipeline.yml", limit: int = 30) -> list[dict]:
        payload = self._get(f"/actions/workflows/{workflow}/runs", per_page=limit)
        runs = []
        for run in payload.get("workflow_runs", []):
            runs.append({
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "event": run.get("event"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "html_url": run.get("html_url"),
                "run_attempt": run.get("run_attempt"),
            })
        return runs

    def in_flight(self, workflow: str = "daily-pipeline.yml") -> dict | None:
        """A queued or running run, if any.

        Dispatching while one is active would cancel it: daily-pipeline.yml sets
        cancel-in-progress for workflow_dispatch.
        """
        for status in ("in_progress", "queued"):
            payload = self._get(
                f"/actions/workflows/{workflow}/runs", status=status, per_page=1
            )
            runs = payload.get("workflow_runs") or []
            if runs:
                return {"id": runs[0].get("id"), "status": status,
                        "html_url": runs[0].get("html_url")}
        return None

    def dispatch(self, workflow: str, inputs: dict[str, str], ref: str = "main") -> None:
        if not self._token:
            raise GitHubError("dispatch requires ADMIN_GITHUB_TOKEN with actions:write")
        url = f"{API}/repos/{self._repo}/actions/workflows/{workflow}/dispatches"
        try:
            response = httpx.post(
                url, headers=self._headers(),
                json={"ref": ref, "inputs": inputs}, timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"dispatch failed: {exc}") from exc
        if response.status_code != 204:
            raise GitHubError(f"dispatch returned {response.status_code}: {response.text[:200]}")

    def job_logs(self, run_id: int) -> str:
        """Plain-text logs for a run's jobs.

        Uses the per-job endpoint (plain text) rather than the run endpoint
        (a zip). The redirect Location points at a different host, so the auth
        header is deliberately not forwarded across it.
        """
        if not self._token:
            raise GitHubError("reading logs requires ADMIN_GITHUB_TOKEN")

        jobs = self._get(f"/actions/runs/{run_id}/jobs").get("jobs", [])
        chunks: list[str] = []
        for job in jobs[:5]:
            job_id = job.get("id")
            url = f"{API}/repos/{self._repo}/actions/jobs/{job_id}/logs"
            try:
                head = httpx.get(
                    url, headers=self._headers(),
                    follow_redirects=False, timeout=self._timeout,
                )
                if head.status_code in (301, 302, 307):
                    location = head.headers.get("location", "")
                    # No Authorization here: different host, and forwarding a
                    # bearer token across a redirect leaks it.
                    body = httpx.get(location, timeout=self._timeout).text
                else:
                    body = head.text
            except httpx.HTTPError as exc:
                body = f"(could not fetch logs for job {job_id}: {exc})"
            chunks.append(f"===== {job.get('name', job_id)} =====\n{body}")
        return "\n\n".join(chunks)
```

- [ ] **Step 2: Add the endpoints**

In `admin_service/app.py`, add these imports at the top:

```python
from .actions import ACTIONS, ActionError, ActionRunner
from .github import GitHubClient, GitHubError
```

Inside `create_app`, after `store` is set on `app.state`, add:

```python
    runner = ActionRunner(store)
    github = GitHubClient(settings.github_repo, settings.github_token)
    app.state.runner = runner
    app.state.github = github
```

Then add these routes before `return app`:

```python
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
        status_obj = runner.status(unit)
        return {
            "unit": status_obj.unit,
            "active_state": status_obj.active_state,
            "result": status_obj.result,
            "exit_code": status_obj.exit_code,
            "finished": status_obj.finished,
            "succeeded": status_obj.succeeded,
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
        # killing a run that is most of the way through an expensive pipeline.
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
```

- [ ] **Step 3: Verify the endpoints enforce auth and validate input**

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
    def get_signing_key_from_jwt(self, t): return type('K',(),{'key':pub})()
s = AdminSettings.from_env({'ADMIN_CF_TEAM_DOMAIN':'t.cloudflareaccess.com','ADMIN_CF_AUD':'a','ADMIN_ALLOWED_EMAILS':'op@x.com'})
app = create_app(s, AccessVerifier(s, jwks_client=Stub()), AdminStore(pathlib.Path(tempfile.mkdtemp())/'t.sqlite3'))
c = TestClient(app)
now = datetime.datetime.now(datetime.timezone.utc)
tok = jwt.encode({'aud':['a'],'iss':'https://t.cloudflareaccess.com','sub':'u','email':'op@x.com','iat':now,'nbf':now,'exp':now+datetime.timedelta(minutes=5)}, priv, algorithm='RS256')
H={'cf-access-jwt-assertion':tok}
print('actions unauth:      ', c.get('/api/actions').status_code)
print('actions auth:        ', c.get('/api/actions').status_code, '->', c.get('/api/actions', headers=H).status_code)
print('action names:        ', [a['name'] for a in c.get('/api/actions', headers=H).json()['actions']])
print('unknown action:      ', c.post('/api/actions/bogus', headers=H).status_code)
print('hero without arg:    ', c.post('/api/actions/hero-regen', headers=H).status_code)
"
```

Expected: `401` unauthenticated; `200` authenticated listing the four actions; `400` for an unknown action; `400` for `hero-regen` with no date.

- [ ] **Step 4: Commit**

```bash
git add admin_service/github.py admin_service/app.py
git commit -m "admin: action and pipeline-dispatch endpoints

Dispatch refuses with 409 when a run is already in flight. daily-pipeline.yml
sets cancel-in-progress for workflow_dispatch, so an unguarded button would
silently kill a run that might be 30 minutes into an expensive pipeline.

Log fetching uses the per-job plain-text endpoint and does not forward the
bearer token across the redirect -- the Location points at a different host."
```

---

### Task 5: Provisioning and host verification

**Files:**
- Modify: `deploy/setup_admin_service.sh`, `deploy/README.md`

- [ ] **Step 1: Extend the provisioning script**

In `deploy/setup_admin_service.sh`, before the final `echo` block, add:

```bash
echo "Installing action units..."
install -m 0644 "$REPO_DIR"/deploy/units/*.service /etc/systemd/system/

echo "Installing sudo wrapper..."
install -m 0755 "$REPO_DIR/deploy/aatf-admin-trigger" /usr/local/sbin/aatf-admin-trigger

echo "Installing sudoers entry..."
# Validate before activating: a malformed sudoers file can lock out sudo.
install -m 0440 "$REPO_DIR/deploy/aatf-admin.sudoers" /etc/sudoers.d/.aatf-admin.tmp
if visudo -c -f /etc/sudoers.d/.aatf-admin.tmp >/dev/null; then
    mv /etc/sudoers.d/.aatf-admin.tmp /etc/sudoers.d/aatf-admin
else
    rm -f /etc/sudoers.d/.aatf-admin.tmp
    echo "ERROR: sudoers file failed validation; not installed" >&2
    exit 1
fi

# Journal reads for the live output view. Group membership, not sudo.
usermod -aG systemd-journal "$USER_NAME"

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR/previews"
touch "$STATE_DIR/privileged.lock"
chown "$USER_NAME":"$USER_NAME" "$STATE_DIR/privileged.lock"

if [ ! -f /etc/aatf-admin/hero.env ]; then
    cat > /etc/aatf-admin/hero.env <<'HEROEOF'
# Image provider credentials for host-side hero regeneration.
GEMINI_API_KEY=
HEROEOF
    chown root:ubuntu /etc/aatf-admin/hero.env
    chmod 0640 /etc/aatf-admin/hero.env
fi

systemctl daemon-reload
```

- [ ] **Step 2: Verify the script still parses**

Run: `bash -n deploy/setup_admin_service.sh && echo OK`

Expected: `OK`.

- [ ] **Step 3: Apply on the host**

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && git fetch origin && sudo ./deploy/setup_admin_service.sh'
```

Expected: units installed, sudoers validated and installed, no errors.

- [ ] **Step 4: Verify the privilege boundary holds**

This is the test that matters — confirm `aatfadmin` can do exactly four things and nothing more.

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 'bash -s' <<'REMOTE'
echo "--- aatfadmin must NOT be in docker ---"
id aatfadmin
echo "--- permitted: the wrapper ---"
sudo -u aatfadmin sudo -n -l 2>&1 | grep aatf-admin-trigger | head -4
echo "--- refused: arbitrary systemctl ---"
sudo -u aatfadmin sudo -n /usr/bin/systemctl restart nginx 2>&1 | head -1
echo "--- refused: docker ---"
sudo -u aatfadmin docker ps 2>&1 | head -1
echo "--- refused: bad action ---"
sudo -u aatfadmin sudo -n /usr/local/sbin/aatf-admin-trigger bogus 2>&1 | head -1
echo "--- refused: date injection ---"
sudo -u aatfadmin sudo -n /usr/local/sbin/aatf-admin-trigger hero-regen '2026-01-01; id' 2>&1 | head -1
REMOTE
```

Expected: `aatfadmin` groups show **no** `docker`; the wrapper is listed as permitted; `systemctl restart nginx` is refused by sudo; `docker ps` fails with a permission error; `bogus` exits 2; the injection attempt exits 3.

If `docker ps` *succeeds*, stop — the user was added to the docker group somewhere and the boundary is gone.

- [ ] **Step 5: Run a real action end to end**

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 'bash -s' <<'REMOTE'
sudo -u aatfadmin sudo -n /usr/local/sbin/aatf-admin-trigger git-sync
sleep 8
systemctl show aatf-git-sync.service --property=ActiveState --property=Result --property=ExecMainStatus
journalctl -u aatf-git-sync.service -n 8 --no-pager --output=cat
REMOTE
```

Expected: the unit name echoed, then `ActiveState=inactive`, `Result=success`, `ExecMainStatus=0`, and journal lines ending in `Verified sync complete at <hash>`.

- [ ] **Step 6: Verify the site survived and the lock works**

```bash
curl -sS -o /dev/null -w "site: %{http_code}\n" https://news.aatf.ai/
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'flock -n /var/lib/aatf-admin/privileged.lock -c "echo lock is free" || echo "lock is HELD"'
```

Expected: `site: 200` and `lock is free` (no action running).

- [ ] **Step 7: Commit**

```bash
git add deploy/setup_admin_service.sh deploy/README.md
git commit -m "admin: provision action units, wrapper, and sudoers

sudoers is validated with visudo -c into a temp path before being moved into
place -- a malformed file there can lock out sudo entirely.

Journal access is group membership rather than another sudo rule: reading logs
is not a privileged action and should not be granted like one."
```

---

## Self-Review

**Spec coverage.** Implements spec §4 (all four actions, the shared `flock`, systemd-status completion detection, the audit log) and the dispatch in-flight guard called out in the §4 table. The `deploy.sh` side of the shared lock is noted below as a gap.

**Placeholders.** None. Every step has literal file content or a runnable command with expected output.

**Type/name consistency.** Unit filenames in Task 1 match `ACTIONS[...].unit` in Task 3 and the `EXPECTED_ACTIONS`/`expected` sets in Task 2's tests. Wrapper action names (`rebuild-web`, `git-sync`, `hero-regen`, `admin-redeploy`) are identical across the wrapper's `case`, the sudoers entries, `ACTIONS`, and the guard test. `ActionStatus` fields set in Task 3 are the exact keys returned by `/api/actions/status/{unit}` in Task 4. `AdminStore.record_action(principal, action, target, outcome, detail)` is called with that arity in both `ActionRunner` and the dispatch endpoint.

**Two known gaps, deliberately left for later plans.**

1. **`deploy.sh` does not yet take the shared lock.** Spec §4 requires it, and until it does, a GitHub push mid-rebuild can `git reset --hard` the build context under a running build. It is not in this plan because `deploy.sh` is on the webhook path and changing it belongs with plan 0's verification cycle. **Add a task to whichever plan lands last:** wrap `deploy.sh`'s body in `flock /var/lib/aatf-admin/privileged.lock`.
2. **`regenerate_hero.py --output-dir` may not exist.** Task 1 Step 3 says to check and flags that the flag must not be silently dropped. Adding it is a preview-plan task.

**One judgment call.** `admin-redeploy` deliberately does not take the privileged lock, because it restarts the admin service — the process that would be holding or awaiting that lock. The tradeoff is that a redeploy can overlap a rebuild; that is acceptable since they touch different things (`/opt/aatf-admin` versus the container), and the alternative is a deadlock.
