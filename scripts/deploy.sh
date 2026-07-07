#!/bin/bash
set -e
cd /home/ubuntu/ai-news-aggregator

LOG_FILE="logs/deploy.log"
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
}

# Commit-signature verification (CWE-345). The tip of origin/main must be
# SSH-signed by a key in this allow-list, or we refuse to deploy it. The file
# lives outside the working tree so `git reset --hard` / `git clean -fd` can't
# remove it; see deploy/README.md.
ALLOWED_SIGNERS="/home/ubuntu/deploy_allowed_signers"
git config gpg.format ssh
git config gpg.ssh.allowedSignersFile "$ALLOWED_SIGNERS"

log "=== Deploy started ==="

# Step 1: Fetch latest from origin to ensure refs are current
log "Fetching from origin..."
git fetch origin

# Step 1b: Verify the commit we are about to deploy is signed by a trusted key.
# Capture the exact tip and operate on that hash throughout, so a commit
# arriving mid-deploy can't slip into the final reset unverified.
TARGET="$(git rev-parse origin/main)"
if [ "${ALLOW_UNSIGNED_DEPLOY:-}" = "1" ]; then
    log "WARNING: signature check bypassed via ALLOW_UNSIGNED_DEPLOY=1 (target $TARGET)"
elif git verify-commit "$TARGET" 2>>"$LOG_FILE"; then
    log "Signature OK: origin/main tip $TARGET signed by a trusted signer"
else
    log "ABORT: origin/main tip $TARGET failed signed-commit verification; not deploying"
    exit 1
fi

# Step 2: Hard reset to the verified commit to ensure clean state
# This handles cases where previous run failed mid-way or branch drifted
log "Resetting to $TARGET..."
git reset --hard "$TARGET"
git clean -fd

log "Pull/reset completed successfully"

# Step 3: Swap to internal README for EMU push
log "Swapping README for EMU..."
cp /home/ubuntu/README-internal.md README.md
git add README.md

# Step 3b: Remove flyryan-only automation before mirroring to the AATF org.
if [ -f ".github/workflows/daily-pipeline.yml" ]; then
    log "Removing flyryan-only daily pipeline workflow before EMU push..."
    rm -f .github/workflows/daily-pipeline.yml
    git add -u .github/workflows/daily-pipeline.yml
fi

# Only commit if there are changes (avoids error if README already matches)
if ! git diff --cached --quiet; then
    git commit -m "Use internal README for AATF org" --no-verify
    log "README swap committed"
else
    log "README already matches internal version, no commit needed"
fi

# Step 4: Push to github-emu (force needed because swap commit diverges)
log "Pushing to github-emu..."
git push github-emu main --force
log "Push to github-emu completed successfully"

# Step 5: Reset back to the verified commit (drop the swap commit made for EMU).
# We reset to the same verified $TARGET rather than re-fetching, so a new
# (possibly unsigned) commit arriving during the deploy cannot be pulled in here
# without going through the Step 1b check on the next deploy.
log "Resetting back to $TARGET..."
git reset --hard "$TARGET"

log "=== Deploy completed successfully ==="
