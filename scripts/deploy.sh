#!/bin/bash
set -e
cd /home/ubuntu/ai-news-aggregator

LOG_FILE="logs/deploy.log"
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
}

log "=== Deploy started ==="

# Step 1: Fetch latest from origin to ensure refs are current
log "Fetching from origin..."
git fetch origin

# Step 2: Hard reset to origin/main to ensure clean state
# This handles cases where previous run failed mid-way or branch drifted
log "Resetting to origin/main..."
git reset --hard origin/main
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

# Step 5: Fetch again (in case new commits arrived during push) and reset cleanly
log "Resetting back to origin/main..."
git fetch origin
git reset --hard origin/main

log "=== Deploy completed successfully ==="
