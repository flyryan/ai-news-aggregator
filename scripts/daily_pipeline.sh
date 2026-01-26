#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$PROJECT_DIR/logs/pipeline_$(date +%Y-%m-%d).log"

cd "$PROJECT_DIR"

# If running interactively, show output on screen AND log to file
# If running from cron (no tty), just log to file
if [[ -t 1 ]]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    exec >> "$LOG_FILE" 2>&1
fi

echo "=========================================="
echo "Pipeline run started: $(date)"
echo "=========================================="

# Clean untracked files in web/data/ to prevent pull conflicts from failed runs
echo "[$(date)] Cleaning untracked data files..."
git clean -fd web/data/

# Pull latest changes
echo "[$(date)] Git pull..."
git pull origin main

# Activate venv and install any new dependencies
echo "[$(date)] Running pipeline..."
source venv/bin/activate
pip install -q -r requirements.txt

python3 run_pipeline.py

# Commit and push if there are changes
if [[ -n $(git status --porcelain) ]]; then
    echo "[$(date)] Committing changes..."
    git add -A
    git commit -m "data: Auto-update $(date +%Y-%m-%d)"
    echo "[$(date)] Pushing to remote..."
    git push origin main
else
    echo "[$(date)] No changes to commit"
fi

echo "[$(date)] Pipeline completed successfully"
