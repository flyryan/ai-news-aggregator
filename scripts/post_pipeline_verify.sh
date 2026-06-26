#!/bin/bash
# Post-pipeline verification for AI News Aggregator
# Verifies the public site received the update.
#
# Optional environment variables:
#   SITE_URL              Public site URL. Defaults to https://news.aatf.ai.
#   AWS_HOST              SSH target, e.g. ubuntu@203.0.113.10.
#   SSH_KEY               SSH key path. Defaults to ./aatf-news.pem.
#   AWS_PROFILE           AWS CLI profile for instance lookup.
#   AWS_CLI               AWS CLI executable. Defaults to aws.
#   AWS_REGION            AWS region for instance lookup. Defaults to us-east-1.
#   AWS_INSTANCE_ID       EC2 instance id to resolve when AWS_HOST is unset.
#   AWS_INSTANCE_NAME     EC2 Name tag to resolve when AWS_HOST is unset.
#   AWS_SSH_USER          SSH username for EC2 lookup. Defaults to ubuntu.
#   REMOTE_REPO           Repo path on the host. Defaults to /home/ubuntu/ai-news-aggregator.
#   COMPOSE_FILE          Compose file for rebuilds. Defaults to docker-compose.web.yml.
#   REBUILD_WEB           Set true to rebuild/restart the web-only container after sync.
#   SSH_STRICT_HOST_KEY   ssh StrictHostKeyChecking mode for the deploy SSH.
#                         Defaults to accept-new (trust on first use, refuse a
#                         changed key). Set to yes (with SSH_KNOWN_HOSTS) to fully
#                         pin, or no to disable checking (insecure, not advised).
#   SSH_KNOWN_HOSTS       Path to a known_hosts file for host-key pinning. When
#                         set, passed as ssh UserKnownHostsFile.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/aatf-news.pem}"
AWS_HOST="${AWS_HOST:-}"
AWS_PROFILE="${AWS_PROFILE:-}"
AWS_CLI="${AWS_CLI:-aws}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_INSTANCE_ID="${AWS_INSTANCE_ID:-}"
AWS_INSTANCE_NAME="${AWS_INSTANCE_NAME:-}"
AWS_SSH_USER="${AWS_SSH_USER:-ubuntu}"
REMOTE_REPO="${REMOTE_REPO:-/home/ubuntu/ai-news-aggregator}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.web.yml}"
REBUILD_WEB="${REBUILD_WEB:-false}"
SITE_URL="${SITE_URL:-https://news.aatf.ai}"
# Host-key verification mode for the deploy SSH. Default 'accept-new' trusts a
# host on first contact but REFUSES a changed key (blocks active MITM of a
# known host) — unlike the old 'no', which silently accepted any key every time.
# Set SSH_STRICT_HOST_KEY=yes with SSH_KNOWN_HOSTS=/path/to/known_hosts to fully
# pin (recommended for production).
SSH_STRICT_HOST_KEY="${SSH_STRICT_HOST_KEY:-accept-new}"
SSH_KNOWN_HOSTS="${SSH_KNOWN_HOSTS:-}"

# Today's date (what should be on the site after pipeline runs)
TODAY=$(date +%Y-%m-%d)

resolve_aws_host() {
    if [ -n "$AWS_HOST" ]; then
        echo "$AWS_HOST"
        return
    fi

    if [ -z "$AWS_INSTANCE_ID" ] && [ -z "$AWS_INSTANCE_NAME" ]; then
        echo "AWS_HOST is unset. Set AWS_HOST, or set AWS_INSTANCE_ID/AWS_INSTANCE_NAME for AWS CLI lookup." >&2
        return 1
    fi

    local aws_args=(--region "$AWS_REGION")
    if [ -n "$AWS_PROFILE" ]; then
        aws_args+=(--profile "$AWS_PROFILE")
    fi

    local query='Reservations[].Instances[?State.Name==`running`].PublicIpAddress | [0]'
    local public_ip
    if [ -n "$AWS_INSTANCE_ID" ]; then
        public_ip=$("$AWS_CLI" ec2 describe-instances \
            "${aws_args[@]}" \
            --instance-ids "$AWS_INSTANCE_ID" \
            --query "$query" \
            --output text)
    else
        public_ip=$("$AWS_CLI" ec2 describe-instances \
            "${aws_args[@]}" \
            --filters "Name=tag:Name,Values=$AWS_INSTANCE_NAME" \
            --query "$query" \
            --output text)
    fi

    if [ -z "$public_ip" ] || [ "$public_ip" = "None" ]; then
        echo "Could not resolve a running public IP for the configured AWS instance." >&2
        return 1
    fi

    echo "$AWS_SSH_USER@$public_ip"
}

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
    local host
    host="$(resolve_aws_host)"

    local -a ssh_opts=(-i "$SSH_KEY" -o "StrictHostKeyChecking=$SSH_STRICT_HOST_KEY" -o ConnectTimeout=30)
    if [ -n "$SSH_KNOWN_HOSTS" ]; then
        ssh_opts+=(-o "UserKnownHostsFile=$SSH_KNOWN_HOSTS")
    fi

    echo "[FIX] Forcing git sync on AWS host..."
    if [ "$REBUILD_WEB" = "true" ]; then
        ssh "${ssh_opts[@]}" "$host" \
            "cd '$REMOTE_REPO' && git fetch origin && git reset --hard origin/main && docker compose -f '$COMPOSE_FILE' up -d --build" 2>&1
    else
        ssh "${ssh_opts[@]}" "$host" \
            "cd '$REMOTE_REPO' && git fetch origin && git reset --hard origin/main" 2>&1
    fi
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
