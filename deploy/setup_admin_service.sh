#!/bin/bash
# Provision the admin service on the host. Idempotent; safe to re-run.
#
#   sudo ./deploy/setup_admin_service.sh
#
# Does NOT write secrets. It creates /etc/aatf-admin/admin.env and hero.env
# with empty placeholders on first run; fill those in before starting the
# service. The service refuses to start with them unset, which is the intended
# behavior.
set -euo pipefail

USER_NAME="aatfadmin"
APP_DIR="/opt/aatf-admin"
STATE_DIR="/var/lib/aatf-admin"
ENV_DIR="/etc/aatf-admin"
ENV_FILE="$ENV_DIR/admin.env"
HERO_ENV_FILE="$ENV_DIR/hero.env"
REPO_DIR="/home/ubuntu/ai-news-aggregator"
UNIT_DST="/etc/systemd/system/aatf-admin.service"
WRAPPER_DST="/usr/local/sbin/aatf-admin-trigger"
SUDOERS_DST="/etc/sudoers.d/aatf-admin"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Creating system user $USER_NAME (not in docker group, by design)..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

# journalctl for the panel's log view. Reading logs is not a privileged
# action, so it comes from group membership, not sudo.
usermod -aG systemd-journal "$USER_NAME"

# Previews are written from both sides: the service (aatfadmin) creates and
# seeds them, the hero and promote units (ubuntu) write into and read from
# them. Setgid + group-writable + UMask=0002 in both units keeps either side
# able to read and delete the other's files. Granting ubuntu the aatfadmin
# group gives it nothing it does not already have -- ubuntu is in sudo and
# docker, which is effective root; the boundary this design protects is the
# other direction.
usermod -aG aatfadmin ubuntu

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 2770 "$STATE_DIR/previews"
install -d -o root -g root -m 0755 "$APP_DIR"

# The privileged-action mutex. Created here so both the admin units and
# deploy.sh can flock it; deploy.sh skips locking when it does not exist.
# Group-writable: the hero and promote units flock it as ubuntu.
touch "$STATE_DIR/privileged.lock"
chown "$USER_NAME":"$USER_NAME" "$STATE_DIR/privileged.lock"
chmod 0664 "$STATE_DIR/privileged.lock"

# aatfadmin must be able to read the checkout: the dashboard reads committed
# report data, previews seed from it, and /data on the admin origin serves it.
# /home/ubuntu is 0750 on this host, so grant traverse -- execute only, no
# read -- with an ACL; the repo itself is already world-readable.
if ! command -v setfacl >/dev/null 2>&1; then
    echo "Installing acl (setfacl) for the /home/ubuntu traverse grant..."
    apt-get install -y -qq acl || true
fi
if command -v setfacl >/dev/null 2>&1; then
    setfacl -m "u:$USER_NAME:--x" /home/ubuntu
else
    echo "WARNING: setfacl unavailable; falling back to chmod o+x /home/ubuntu" >&2
    chmod o+x /home/ubuntu
fi

echo "Installing the sudo wrapper, sudoers policy, and action units..."
install -o root -g root -m 0755 "$HERE/aatf-admin-trigger" "$WRAPPER_DST"

# visudo validates BEFORE the file is live; a syntax error in sudoers.d can
# lock everyone out of sudo on the whole host.
visudo -c -q -f "$HERE/aatf-admin.sudoers"
install -o root -g root -m 0440 "$HERE/aatf-admin.sudoers" "$SUDOERS_DST"

for unit in "$HERE"/units/*.service; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done

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
ADMIN_SITE_DIR=/var/lib/aatf-admin/site
ENVEOF
    chown root:"$USER_NAME" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
fi

if [ ! -f "$HERO_ENV_FILE" ]; then
    echo "Creating $HERO_ENV_FILE with empty placeholders -- fill these in."
    cat > "$HERO_ENV_FILE" <<'ENVEOF'
# Environment for aatf-hero-regen@.service (runs scripts/regenerate_hero.py as
# ubuntu). The script reads the repo's config/providers.yaml, which is
# git-ignored: copy it onto the host and put the API key(s) it interpolates
# here, e.g.
#   GOOGLE_API_KEY=
ENVEOF
    chown root:ubuntu "$HERO_ENV_FILE"
    chmod 0640 "$HERO_ENV_FILE"
fi

systemctl daemon-reload
systemctl enable aatf-admin.service >/dev/null

# The panel is the built SPA bundle, which exists only inside the web Docker
# image (the host has no node). Export it now if the image is present; the
# rebuild unit refreshes it on every rebuild afterwards.
if ! "$HERE/export_web_bundle.sh" "$STATE_DIR/site"; then
    echo "WARNING: no site bundle exported yet. Build the web image, then run" >&2
    echo "  sudo $HERE/export_web_bundle.sh" >&2
fi

echo
echo "Provisioned. Remaining host prerequisites the panel needs:"
echo "  - hero regeneration: repo venv with pipeline deps + config/providers.yaml"
echo "    (host is web-only today; hero-regen fails cleanly until these exist)"
echo "  - promotion: ubuntu's git must have commit signing configured"
echo "Next:"
echo "  1. Fill in $ENV_FILE (the service refuses to start until you do)"
echo "  2. sudo systemctl restart aatf-admin"
echo "  3. curl -sS localhost:8200/api/health"
echo "  4. Open https://admin.aatf.ai/admin through Cloudflare Access"
