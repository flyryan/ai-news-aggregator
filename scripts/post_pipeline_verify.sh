#!/bin/bash
# Post-pipeline verification for AI News Aggregator
# Runs after pipeline to verify AWS received the update
# Can auto-fix by forcing git sync on AWS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SSH_KEY="$PROJECT_DIR/aatf-news.pem"
AWS_HOST="ubuntu@3.91.64.50"
SITE_URL="https://news.aatf.ai"

# Today's date (what should be on the site after pipeline runs)
TODAY=$(date +%Y-%m-%d)

echo "=========================================="
echo "Post-Pipeline AWS Verification"
echo "$(date)"
echo "Checking for: $TODAY"
echo "=========================================="

# Function to check if date exists on site
check_site() {
    local latest_date=$(curl -s "$SITE_URL/data/index.json" 2>/dev/null | jq -r '.dates[0].date' 2>/dev/null)
    echo "$latest_date"
}

# Function to force sync AWS
force_sync_aws() {
    echo "[FIX] Forcing git sync on AWS..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=30 "$AWS_HOST" \
        "cd /home/ubuntu/ai-news-aggregator && git fetch origin && git reset --hard origin/main" 2>&1
}

# Check current state
echo ""
echo "[CHECK] Fetching latest date from $SITE_URL..."
SITE_DATE=$(check_site)

if [ "$SITE_DATE" = "$TODAY" ]; then
    echo "✅ AWS is current: $SITE_DATE"
    exit 0
fi

echo "⚠️  AWS shows: $SITE_DATE (expected: $TODAY)"

# Attempt auto-fix
echo ""
force_sync_aws

# Wait for site to update (Cloudflare cache, etc.)
echo ""
echo "[WAIT] Giving AWS 10s to settle..."
sleep 10

# Re-check
echo ""
echo "[VERIFY] Re-checking site..."
SITE_DATE=$(check_site)

if [ "$SITE_DATE" = "$TODAY" ]; then
    echo "✅ Auto-fix successful! AWS now shows: $SITE_DATE"
    exit 0
else
    echo "❌ AUTO-FIX FAILED"
    echo "Site still shows: $SITE_DATE (expected: $TODAY)"
    echo ""
    echo "Manual intervention required."
    exit 1
fi
