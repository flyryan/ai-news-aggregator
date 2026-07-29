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
    docker compose -f "$COMPOSE_FILE" build ai-news-aggregator
    docker compose -f "$COMPOSE_FILE" up -d ai-news-aggregator
    log "Rebuild complete"
fi

log "Verified sync complete at $TARGET"
