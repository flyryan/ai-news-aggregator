#!/bin/bash
# Provision the admin service on the host. Idempotent; safe to re-run.
#
#   sudo ./deploy/setup_admin_service.sh
#
# Does NOT write secrets. It creates /etc/aatf-admin/admin.env with empty
# placeholders on first run; fill those in before starting the service. The
# service refuses to start with them unset, which is the intended behavior.
set -euo pipefail

USER_NAME="aatfadmin"
APP_DIR="/opt/aatf-admin"
STATE_DIR="/var/lib/aatf-admin"
ENV_DIR="/etc/aatf-admin"
ENV_FILE="$ENV_DIR/admin.env"
REPO_DIR="/home/ubuntu/ai-news-aggregator"
UNIT_DST="/etc/systemd/system/aatf-admin.service"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Creating system user $USER_NAME (not in docker group, by design)..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR/previews"
install -d -o root -g root -m 0755 "$APP_DIR"

# The privileged-action mutex. Created here so both the admin units and
# deploy.sh can flock it; deploy.sh skips locking when it does not exist.
touch "$STATE_DIR/privileged.lock"
chown "$USER_NAME":"$USER_NAME" "$STATE_DIR/privileged.lock"

echo "Syncing application code..."
# The service runs its own copy so a mid-deploy checkout cannot swap code under
# a live process.
rsync -a --delete --exclude '__pycache__' \
    "$REPO_DIR/admin_service/" "$APP_DIR/admin_service/"

if [ ! -d "$APP_DIR/venv" ]; then
    echo "Creating venv..."
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/admin_service/requirements.txt"

install -d -o root -g "$USER_NAME" -m 0750 "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE with empty placeholders -- fill these in."
    cat > "$ENV_FILE" <<'ENVEOF'
# Cloudflare Access. Both required; the service will not start without them.
ADMIN_CF_TEAM_DOMAIN=
ADMIN_CF_AUD=
ADMIN_ALLOWED_EMAILS=

# Optional: comma-separated Access service-token common names.
ADMIN_ALLOWED_SERVICE_TOKENS=

# GitHub API. Fine-grained PAT, this repo only. Actions: read (add write only
# when workflow dispatch is enabled).
ADMIN_GITHUB_TOKEN=
ADMIN_GITHUB_REPO=flyryan/ai-news-aggregator

# Free balance probes for the dashboard.
SCRAPECREATORS_API_KEY=
TWITTERAPI_IO_KEY=

ADMIN_STATE_DB=/var/lib/aatf-admin/admin.sqlite3
ADMIN_REPO_DIR=/home/ubuntu/ai-news-aggregator
ENVEOF
    chown root:"$USER_NAME" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
fi

install -m 0644 "$HERE/aatf-admin.service.example" "$UNIT_DST"
systemctl daemon-reload
systemctl enable aatf-admin.service >/dev/null

echo
echo "Provisioned. Next:"
echo "  1. Fill in $ENV_FILE (the service refuses to start until you do)"
echo "  2. sudo systemctl restart aatf-admin"
echo "  3. curl -sS localhost:8200/api/health"
