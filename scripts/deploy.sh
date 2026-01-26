#!/bin/bash
set -e
cd /home/ubuntu/ai-news-aggregator

# Pull latest from flyryan (includes public README.md)
git pull origin main
echo "$(date): Pull completed successfully" >> logs/deploy.log

# Swap to internal README for EMU push
cp README-internal.md README.md
git add README.md
git commit -m "Use internal README for AATF org" --no-verify

# Push to github-emu (force needed because swap commit diverges from flyryan)
git push github-emu main --force
echo "$(date): Push to github-emu completed successfully" >> logs/deploy.log

# Reset back to flyryan state (removes temp commit, restores public README)
git reset --hard origin/main
