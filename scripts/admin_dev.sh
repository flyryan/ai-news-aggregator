#!/bin/bash
# Run the admin service locally for frontend development.
#
# Auth is bypassed (ADMIN_DEV=1) because there is no Cloudflare Access in front
# of localhost. The bypass logs a warning on every request and is never present
# on the host -- the provisioning script does not write it into the env file,
# and a guard test fails if it is set while the suite runs.
#
#   ./scripts/admin_dev.sh          # then: cd frontend && npm run dev
set -euo pipefail

cd "$(dirname "$0")/.."

export ADMIN_DEV=1
export ADMIN_CF_TEAM_DOMAIN="${ADMIN_CF_TEAM_DOMAIN:-dev.cloudflareaccess.com}"
export ADMIN_CF_AUD="${ADMIN_CF_AUD:-dev-aud}"
export ADMIN_ALLOWED_EMAILS="${ADMIN_ALLOWED_EMAILS:-dev@localhost}"
export ADMIN_REPO_DIR="${ADMIN_REPO_DIR:-$PWD}"
export ADMIN_STATE_DB="${ADMIN_STATE_DB:-$PWD/data/admin-dev/admin.sqlite3}"

if [ ! -x "./venv/bin/uvicorn" ]; then
    echo "uvicorn not found in ./venv. Install the service deps first:" >&2
    echo "  ./venv/bin/pip install -r admin_service/requirements.txt" >&2
    exit 1
fi

echo "Admin service -> http://127.0.0.1:8200  (auth bypassed: dev only)"
echo "Frontend      -> cd frontend && npm run dev   (proxies /api here)"
echo "Panel         -> http://localhost:5173/admin"
echo

exec ./venv/bin/uvicorn --factory admin_service.app:create_app \
    --host 127.0.0.1 --port 8200 --reload
