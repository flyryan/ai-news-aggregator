# Deploy Signature Gate Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the CWE-345 signed-commit bypass in `scripts/post_pipeline_verify.sh`, extract the verification logic into one shared script both deploy paths call, and move the deploy webhook off the over-privileged `ubuntu` user.

**Architecture:** `scripts/deploy.sh` enforces a signed-commit gate before deploying (`git verify-commit` against an allow-list). `scripts/post_pipeline_verify.sh` operates on the *same host checkout* but runs `git reset --hard origin/main` with no verification, then builds and runs the result — a bypass of the control on the same repository. Fix by extracting the gate into `scripts/verified_sync.sh` on the host and having both callers use it. Separately, `webhook.service` runs as `User=ubuntu`, a member of both `sudo` and `docker`; move it to a dedicated unprivileged user.

**Tech Stack:** bash, git SSH-signature verification, systemd, stdlib `unittest` for guard tests.

## Global Constraints

- Tests are **stdlib `unittest` only** — no pytest, no new test dependencies. CI runs `python3 -m unittest tests.<module> -v` (`.github/workflows/tests.yml:34-38`).
- Test files are named `tests/<name>_test.py`.
- Guard tests follow the documented style of `tests/webhook_hook_auth_test.py`: a module docstring stating the finding, why it exists, and what regression it prevents.
- Host paths: repo at `/home/ubuntu/ai-news-aggregator`, allow-list at `/home/ubuntu/deploy_allowed_signers` (outside the working tree so `git clean -fd` cannot remove it).
- Never introduce `ALLOW_UNSIGNED_DEPLOY=1` as a workaround in any new code path. It exists in `deploy.sh:28-29` as a deliberate, loudly-logged escape hatch; do not add more.
- Commits must be SSH-signed (repo policy; `scripts/deploy.sh:27-35` refuses unsigned tips).
- Host changes are applied over SSH: `ssh -i aatf-news.pem ubuntu@54.167.55.79`. Files under `/etc` and `/usr/lib/systemd` are host-only and cannot be committed to the repo; the repo carries templates plus a setup script.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/verified_sync.sh` (create) | The single implementation of "fetch, verify the tip is signed by a trusted key, reset to that exact hash". Callable locally or over SSH. |
| `scripts/deploy.sh` (modify) | Replace its inline verify+reset block with a call to `verified_sync.sh`. Behavior unchanged. |
| `scripts/post_pipeline_verify.sh` (modify, `:113-121`) | Replace both raw `git reset --hard origin/main` invocations with `verified_sync.sh`. This is the actual bug fix. |
| `tests/deploy_signature_gate_test.py` (create) | Guard test: no shipped script may reset to a remote ref without verification. |
| `deploy/webhook.service.example` (create) | Template unit running as `aatfdeploy`, tracked in git. |
| `deploy/README.md` (modify) | Document `verified_sync.sh` and the webhook user migration. |

---

### Task 1: Extract the signature gate into a shared script

**Files:**
- Create: `scripts/verified_sync.sh`
- Test: `tests/deploy_signature_gate_test.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/verified_sync.sh`, invoked as `verified_sync.sh [--rebuild] [--compose-file FILE]`. Exits 0 on verified sync, 1 on verification failure, 2 on usage error. Honors `REPO_DIR` (default `/home/ubuntu/ai-news-aggregator`), `ALLOWED_SIGNERS` (default `/home/ubuntu/deploy_allowed_signers`), `LOG_FILE` (default `logs/deploy.log`), `REMOTE` (default `origin`), `BRANCH` (default `main`).

- [ ] **Step 1: Write the failing guard test**

Create `tests/deploy_signature_gate_test.py`:

```python
"""Security guard: no shipped script may hard-reset to a remote ref unverified.

Context / why this exists
-------------------------
`scripts/deploy.sh` enforces a CWE-345 signed-commit gate: it runs
`git verify-commit` on the tip of origin/main against an allow-list and
refuses to deploy an unsigned commit. But `scripts/post_pipeline_verify.sh`
operated on the SAME host checkout and ran

    git fetch origin && git reset --hard origin/main && docker compose ... --build

with no verification at all, then built and ran the result. Anyone able to
move `flyryan/main` got code execution on the origin host via the *verification*
path, even though the deploy path would have refused the same commit.

This guard locks in the fix: any script that resets to a remote-tracking ref
must route through `scripts/verified_sync.sh`, which performs the
`git verify-commit` check and resets to the exact verified hash.

Stdlib-only (re + unittest), matching the repo's other guard tests:

  python3 -m unittest tests.deploy_signature_gate_test -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

# `git reset --hard <remote>/<branch>` — resetting to a remote ref means adopting
# whatever a push put there, which is exactly what must be gated.
RESET_TO_REMOTE = re.compile(r"git\s+reset\s+--hard\s+[\"']?(origin|upstream)/", re.I)

# The one script allowed to contain it, because it is the thing doing the verifying.
VERIFIER = "verified_sync.sh"


def _shell_scripts():
    return sorted(p for p in SCRIPTS.glob("*.sh") if p.is_file())


class DeploySignatureGateTest(unittest.TestCase):
    def test_verifier_script_exists(self):
        self.assertTrue(
            (SCRIPTS / VERIFIER).is_file(),
            f"scripts/{VERIFIER} must exist; it is the single implementation of the "
            "signed-commit gate that every sync path routes through.",
        )

    def test_verifier_actually_verifies(self):
        body = (SCRIPTS / VERIFIER).read_text()
        self.assertIn(
            "git verify-commit",
            body,
            f"scripts/{VERIFIER} must call `git verify-commit`; without it the "
            "shared helper is a rename of the bug, not a fix.",
        )
        self.assertIn(
            "allowedSignersFile",
            body,
            f"scripts/{VERIFIER} must configure gpg.ssh.allowedSignersFile, or "
            "`git verify-commit` has no trust anchor and cannot reject anything.",
        )

    def test_no_script_resets_to_remote_without_the_verifier(self):
        offenders = []
        for script in _shell_scripts():
            if script.name == VERIFIER:
                continue
            text = script.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if RESET_TO_REMOTE.search(line):
                    offenders.append(f"{script.name}:{lineno}: {stripped[:90]}")
        self.assertEqual(
            [],
            offenders,
            "These lines reset the working tree to a remote ref without the signature "
            "gate. Route them through scripts/verified_sync.sh instead:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.deploy_signature_gate_test -v`

Expected: FAIL. `test_verifier_script_exists` fails because `scripts/verified_sync.sh` does not exist yet, and `test_no_script_resets_to_remote_without_the_verifier` fails listing two `post_pipeline_verify.sh` offenders (the `REBUILD_WEB` branch and the plain branch).

- [ ] **Step 3: Write `scripts/verified_sync.sh`**

```bash
#!/bin/bash
# Fetch, verify, and reset the host checkout to a trusted commit.
#
# This is the single implementation of the CWE-345 signed-commit gate. Both
# scripts/deploy.sh (webhook path) and scripts/post_pipeline_verify.sh (operator
# verification path) call it, so there is exactly one definition of "a commit we
# are willing to run" and it cannot drift between them.
#
# Usage: verified_sync.sh [--rebuild] [--compose-file FILE]
# Exit:  0 verified and synced | 1 verification failed | 2 usage error
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/ai-news-aggregator}"
ALLOWED_SIGNERS="${ALLOWED_SIGNERS:-/home/ubuntu/deploy_allowed_signers}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.web.yml}"
REBUILD=false

while [ $# -gt 0 ]; do
    case "$1" in
        --rebuild) REBUILD=true; shift ;;
        --compose-file) COMPOSE_FILE="${2:?--compose-file needs a value}"; shift 2 ;;
        *) echo "usage: $0 [--rebuild] [--compose-file FILE]" >&2; exit 2 ;;
    esac
done

cd "$REPO_DIR"

LOG_FILE="${LOG_FILE:-logs/deploy.log}"
mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" | tee -a "$LOG_FILE"; }

if [ ! -r "$ALLOWED_SIGNERS" ]; then
    log "ABORT: allowed-signers file $ALLOWED_SIGNERS is missing or unreadable; refusing to sync"
    exit 1
fi

git config gpg.format ssh
git config gpg.ssh.allowedSignersFile "$ALLOWED_SIGNERS"

log "Fetching $REMOTE/$BRANCH..."
git fetch "$REMOTE"

# Capture the exact tip and operate on that hash throughout, so a commit arriving
# mid-sync cannot slip into the reset unverified.
TARGET="$(git rev-parse "$REMOTE/$BRANCH")"

if git verify-commit "$TARGET" 2>>"$LOG_FILE"; then
    log "Signature OK: $REMOTE/$BRANCH tip $TARGET signed by a trusted signer"
else
    log "ABORT: $REMOTE/$BRANCH tip $TARGET failed signed-commit verification; not syncing"
    exit 1
fi

log "Resetting to $TARGET..."
git reset --hard "$TARGET"
git clean -fd

if [ "$REBUILD" = "true" ]; then
    log "Rebuilding web container from $COMPOSE_FILE..."
    # Build first so a broken build cannot replace a working container.
    docker compose -f "$COMPOSE_FILE" build
    docker compose -f "$COMPOSE_FILE" up -d
    log "Rebuild complete"
fi

log "Verified sync complete at $TARGET"
```

Then make it executable:

```bash
chmod +x scripts/verified_sync.sh
```

- [ ] **Step 4: Run the test — expect one remaining failure**

Run: `python3 -m unittest tests.deploy_signature_gate_test -v`

Expected: `test_verifier_script_exists` and `test_verifier_actually_verifies` now PASS. `test_no_script_resets_to_remote_without_the_verifier` still FAILS, listing the two `post_pipeline_verify.sh` lines. That is correct — Task 2 fixes them.

- [ ] **Step 5: Commit**

```bash
git add scripts/verified_sync.sh tests/deploy_signature_gate_test.py
git commit -m "deploy: extract the signed-commit gate into verified_sync.sh

deploy.sh verified the tip before resetting; post_pipeline_verify.sh reset to
origin/main with no check at all, on the same checkout. One shared script means
one definition of a commit we will run, and a guard test that fails if any
script resets to a remote ref around it.

The gate itself is unchanged: fetch, rev-parse the tip once, verify-commit
against the allow-list, reset to that exact hash. Operating on the captured
hash keeps a commit that lands mid-sync from riding along unverified."
```

---

### Task 2: Route `post_pipeline_verify.sh` through the gate

**Files:**
- Modify: `scripts/post_pipeline_verify.sh:104-122` (the `force_sync_aws` function)
- Test: `tests/deploy_signature_gate_test.py` (already written in Task 1)

**Interfaces:**
- Consumes: `scripts/verified_sync.sh` from Task 1, including its `--rebuild` and `--compose-file` flags.
- Produces: nothing new.

- [ ] **Step 1: Confirm the test still fails for the right reason**

Run: `python3 -m unittest tests.deploy_signature_gate_test.DeploySignatureGateTest.test_no_script_resets_to_remote_without_the_verifier -v`

Expected: FAIL naming `post_pipeline_verify.sh` twice (around lines 117 and 120). Read the two offending lines before changing them so the replacement preserves the `REBUILD_WEB` distinction.

- [ ] **Step 2: Replace the unverified sync**

In `scripts/post_pipeline_verify.sh`, replace the body of `force_sync_aws` between `echo "[FIX] Forcing git sync on AWS host..."` and the closing `}` with:

```bash
    echo "[FIX] Forcing verified git sync on AWS host..."
    # Route through verified_sync.sh so this path enforces the same CWE-345
    # signed-commit gate as scripts/deploy.sh. Previously this ran a raw
    # `git reset --hard origin/main` and, with REBUILD_WEB=true, immediately
    # built and ran the result -- a bypass of the gate on the same checkout.
    local remote_cmd="cd '$REMOTE_REPO' && ./scripts/verified_sync.sh"
    if [ "$REBUILD_WEB" = "true" ]; then
        remote_cmd="$remote_cmd --rebuild --compose-file '$COMPOSE_FILE'"
    fi

    if ssh "${ssh_opts[@]}" "$host" "$remote_cmd" 2>&1; then
        return 0
    fi

    local rc=$?
    echo "[FAIL] Verified sync refused or failed (exit $rc)."
    echo "       If the tip is unsigned, sign and re-push rather than bypassing:"
    echo "       the gate is what stops an attacker-pushed commit from running here."
    return "$rc"
```

- [ ] **Step 3: Run the guard test to verify it passes**

Run: `python3 -m unittest tests.deploy_signature_gate_test -v`

Expected: PASS, all three tests. No script outside `verified_sync.sh` resets to a remote ref.

- [ ] **Step 4: Check the script still parses**

Run: `bash -n scripts/post_pipeline_verify.sh && bash -n scripts/verified_sync.sh`

Expected: no output, exit 0. (`bash -n` parses without executing — safe to run locally, and it catches the quoting mistakes this kind of edit invites.)

- [ ] **Step 5: Commit**

```bash
git add scripts/post_pipeline_verify.sh
git commit -m "deploy: close the unverified-sync bypass in post_pipeline_verify

force_sync_aws ran git reset --hard origin/main on the origin host with no
verify-commit and, under REBUILD_WEB=true, built and ran the result. deploy.sh
would have refused the same commit. Both paths now call verified_sync.sh.

On refusal it says to sign and re-push rather than offering a bypass flag --
the gate is the control, and an escape hatch advertised at the point of failure
is one that gets used."
```

---

### Task 3: Wire the guard test into CI

**Files:**
- Modify: `.github/workflows/tests.yml:38` (after the existing `extract_json_str_test` step)

**Interfaces:**
- Consumes: `tests/deploy_signature_gate_test.py` from Task 1.
- Produces: nothing.

- [ ] **Step 1: Add the CI step**

In `.github/workflows/tests.yml`, immediately after the step that runs `tests.extract_json_str_test`, add:

```yaml
      - name: Deploy signature gate guard
        run: python3 -m unittest tests.deploy_signature_gate_test -v
```

Match the surrounding steps' indentation exactly (6 spaces before `- name`). This test is stdlib-only, so it belongs in the dependency-light job alongside the other guards, not the job that pip-installs.

- [ ] **Step 2: Verify the workflow is valid YAML**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/tests.yml')); print('valid')"`

Expected: `valid`.

If PyYAML is not installed locally, skip this and rely on Step 3.

- [ ] **Step 3: Run the full guard suite as CI will**

Run:
```bash
python3 -m unittest tests.call_with_thinking_signature_test tests.webhook_hook_auth_test tests.extract_json_str_test tests.deploy_signature_gate_test -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: run the deploy signature gate guard

Sits with the other stdlib-only guards so a reintroduced unverified reset
fails the build rather than waiting to be noticed on the host."
```

---

### Task 4: Move the deploy webhook off `User=ubuntu`

**Files:**
- Create: `deploy/webhook.service.example`
- Create: `deploy/setup_webhook_user.sh`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: `scripts/verified_sync.sh` (the new user must be able to run the deploy).
- Produces: host user `aatfdeploy`; unit template at `deploy/webhook.service.example`.

**Context the implementer needs:** verified on the host 2026-07-28 — `webhook.service` is active at `/usr/lib/systemd/system/webhook.service`, runs `ExecStart=/usr/bin/webhook -nopanic -hooks /etc/webhook.conf -verbose` as `User=ubuntu`, and `ubuntu` is in groups `sudo(27)` and `docker(988)`. So a webhook payload that reaches command execution today runs as a user with full host root. The hook is HMAC-gated, so this is defense in depth, not an open hole.

- [ ] **Step 1: Write the unit template**

Create `deploy/webhook.service.example`:

```ini
# Deploy webhook listener, running as an unprivileged dedicated user.
#
# The shipped default ran as User=ubuntu, which is in both `sudo` and `docker`
# -- i.e. effective host root. The hook is HMAC-gated (see webhook/README.md),
# so this is defense in depth: it bounds what a hook-execution bug can reach.
#
# Install: see deploy/setup_webhook_user.sh
[Unit]
Description=GitHub deploy webhook listener
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=aatfdeploy
Group=aatfdeploy
ExecStart=/usr/bin/webhook -nopanic -hooks /etc/webhook.conf -ip 127.0.0.1 -verbose
Restart=on-failure
RestartSec=5

# The listener only ever needs to read its config and write the repo checkout.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/ai-news-aggregator

[Install]
WantedBy=multi-user.target
```

Note `-ip 127.0.0.1`: the listener is reached only through the cloudflared tunnel, so binding loopback removes the all-interfaces exposure that `webhook/README.md:54-56` already recommends against.

- [ ] **Step 2: Write the provisioning script**

Create `deploy/setup_webhook_user.sh`:

```bash
#!/bin/bash
# Provision the unprivileged deploy-webhook user and install its unit.
#
# Idempotent: safe to re-run. Run ON THE HOST as a user with sudo.
#
#   sudo ./deploy/setup_webhook_user.sh
set -euo pipefail

USER_NAME="aatfdeploy"
REPO_DIR="/home/ubuntu/ai-news-aggregator"
UNIT_SRC="$(dirname "$0")/webhook.service.example"
UNIT_DST="/etc/systemd/system/webhook.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Creating system user $USER_NAME..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
else
    echo "User $USER_NAME already exists."
fi

# The webhook runs deploy.sh, which writes the checkout and its log.
echo "Granting $USER_NAME write access to the checkout..."
chgrp -R "$USER_NAME" "$REPO_DIR"
chmod -R g+rwX "$REPO_DIR"

# The allow-list must stay readable for git verify-commit.
chgrp "$USER_NAME" /home/ubuntu/deploy_allowed_signers
chmod g+r /home/ubuntu/deploy_allowed_signers

# A drop-in at /etc/systemd/system overrides the packaged unit without editing it.
echo "Installing unit to $UNIT_DST..."
install -m 0644 "$UNIT_SRC" "$UNIT_DST"

systemctl daemon-reload
systemctl restart webhook
systemctl --no-pager status webhook | head -5

echo
echo "Done. Verify the next deploy still lands:"
echo "  sudo -u $USER_NAME git -C $REPO_DIR fetch origin"
```

Then: `chmod +x deploy/setup_webhook_user.sh`

- [ ] **Step 3: Verify the scripts parse**

Run: `bash -n deploy/setup_webhook_user.sh`

Expected: no output, exit 0.

- [ ] **Step 4: Document it**

Append to `deploy/README.md`:

```markdown
## Webhook listener user (2026-07-28)

The deploy webhook originally ran as `User=ubuntu`, which is in both `sudo` and
`docker` — effective host root. The hook is HMAC-gated, so this was never an open
door, but it meant any hook-execution bug inherited full host privilege.

`deploy/webhook.service.example` runs it as a dedicated `aatfdeploy` system user
with `NoNewPrivileges`, `ProtectSystem=full`, and a single `ReadWritePaths` entry
for the checkout. It also binds the listener to `127.0.0.1`, which is sufficient
because the only path in is the cloudflared tunnel.

Install with `sudo ./deploy/setup_webhook_user.sh` (idempotent). The script creates
the user, grants group write on the checkout and group read on
`deploy_allowed_signers` (needed by `git verify-commit`), installs the unit as a
`/etc/systemd/system` override, and restarts the service.

**After installing, confirm a real deploy still lands** — push a trivial commit to
`main` and check `logs/deploy.log` for a new "Deploy completed successfully". A
permissions mistake here shows up as a deploy that silently stops working.
```

- [ ] **Step 5: Commit**

```bash
git add deploy/webhook.service.example deploy/setup_webhook_user.sh deploy/README.md
git commit -m "deploy: run the webhook listener as an unprivileged user

It ran as User=ubuntu, which is in sudo and docker, so anything that reached
command execution through the listener had host root. The hook is HMAC-gated so
this is defense in depth, but the blast radius was larger than the job needs.

Adds a tracked unit template and an idempotent provisioning script: dedicated
aatfdeploy system user, NoNewPrivileges, ProtectSystem=full, one ReadWritePaths
entry, and a loopback bind (the only route in is the tunnel).

Host-only files, so the repo carries the template and the installer rather than
the live unit."
```

---

### Task 5: Apply to the host and verify end to end

**Files:** none (host operations).

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a host running the verified sync path and the unprivileged webhook.

**This task changes production.** Do it deliberately, and confirm each step before the next.

- [ ] **Step 1: Land the changes on the host**

Push the preceding commits to `main`, then let the existing webhook deploy them, or sync manually:

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && git fetch origin && git log --oneline -1 origin/main'
```

Confirm the tip matches your latest local commit before proceeding.

- [ ] **Step 2: Verify the gate accepts a good commit**

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && ./scripts/verified_sync.sh'
```

Expected: log lines ending `Verified sync complete at <hash>`, exit 0. If it aborts on signature verification, **stop** — either the allow-list is missing a signer or the tip is genuinely unsigned. Investigate rather than bypassing.

- [ ] **Step 3: Verify the gate rejects an unsigned commit**

This is the test that matters; a gate never observed rejecting is not known to work.

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 'bash -s' <<'REMOTE'
set -e
cd /tmp && rm -rf gate-test && mkdir gate-test && cd gate-test
git init -q fake-origin && cd fake-origin
git config user.email t@t && git config user.name t && git config commit.gpgsign false
git commit -q --allow-empty -m "unsigned commit"
cd /tmp/gate-test && git clone -q fake-origin work && cd work
REPO_DIR=/tmp/gate-test/work \
ALLOWED_SIGNERS=/home/ubuntu/deploy_allowed_signers \
LOG_FILE=/tmp/gate-test/gate.log \
BRANCH=$(git -C /tmp/gate-test/work rev-parse --abbrev-ref HEAD) \
  /home/ubuntu/ai-news-aggregator/scripts/verified_sync.sh && echo "UNEXPECTED: exit 0" || echo "REJECTED as expected (exit $?)"
REMOTE
```

Expected: `REJECTED as expected (exit 1)`. Then clean up: `ssh ... 'rm -rf /tmp/gate-test'`.

- [ ] **Step 4: Migrate the webhook user**

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'cd /home/ubuntu/ai-news-aggregator && sudo ./deploy/setup_webhook_user.sh'
```

Expected: the user is created, the unit installs, and `systemctl status webhook` shows `active (running)` as `aatfdeploy`.

- [ ] **Step 5: Confirm a real deploy still works**

Push a trivial commit to `main` (a comment or doc typo), wait ~30s, then:

```bash
ssh -i aatf-news.pem ubuntu@54.167.55.79 \
  'tail -12 /home/ubuntu/ai-news-aggregator/logs/deploy.log; echo "---"; systemctl is-active webhook'
```

Expected: a fresh `=== Deploy completed successfully ===` dated now, and `active`.

If the deploy did not fire, check `journalctl -u webhook -n 40` for permission errors — the likely cause is the checkout or allow-list group permissions from Step 4.

- [ ] **Step 6: Confirm the site is still serving**

```bash
curl -sS -o /dev/null -w "site=%{http_code}\n" https://news.aatf.ai/
curl -sS https://news.aatf.ai/data/index.json | python3 -c "import json,sys; print('latest:', json.load(sys.stdin)['latestDate'])"
```

Expected: `site=200` and the current latest date. This is the real success signal — the container healthcheck can pass while the site serves nothing useful.

---

## Self-Review

**Spec coverage.** This plan implements spec §8 (the `post_pipeline_verify.sh` bypass) and the final open item (migrating `webhook.service` off `User=ubuntu`). Both were explicitly approved. No other spec section is in scope here.

**Placeholders.** None. Every step has the literal file content, command, or diff to apply.

**Type/name consistency.** `scripts/verified_sync.sh` is referenced by the same path in Tasks 1, 2, 4, and 5. Its flags (`--rebuild`, `--compose-file`) are defined in Task 1 Step 3 and used identically in Task 2 Step 2. Env var names (`REPO_DIR`, `ALLOWED_SIGNERS`, `LOG_FILE`, `REMOTE`, `BRANCH`, `COMPOSE_FILE`) are defined in Task 1 and reused unchanged in Task 5's test invocations. The user name `aatfdeploy` is consistent across `webhook.service.example`, `setup_webhook_user.sh`, and the README text; it is deliberately distinct from the `aatfadmin` user that the admin-service plans create, since the two services have different jobs.

**One risk worth naming.** Task 4 changes file ownership on the live checkout. If the group permissions are wrong, deploys stop silently — which is why Task 5 Step 5 pushes a real commit and reads the log rather than trusting `systemctl status`.
