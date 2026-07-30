#!/bin/bash
# Trigger a rebuild of the public web container from anywhere -- the one-liner
# for an agent or a human who just merged a frontend change:
#
#   scripts/trigger_rebuild.sh
#
# POSTs an HMAC-signed request to the `rebuild-web` hook on the deploy webhook
# listener (webhook.aatf.ai, reachable only through Cloudflare). The host runs
# aatf-rebuild-web.service: build, swap only on build success, verify
# /data/index.json serves, refresh the admin panel bundle -- all under the same
# privileged lock the panel and deploy webhook use. Verify afterwards with
#   curl -sS https://news.aatf.ai/data/index.json | head -c 200
#
# The secret comes from AATF_REBUILD_WEBHOOK_SECRET or
# ~/.config/aatf/rebuild-webhook-secret (chmod 600). It is the `rebuild-web`
# hook's HMAC secret in the host's webhook/hooks.json -- distinct from the
# GitHub deploy secret, so revoking agent access never touches deploys.
set -euo pipefail

URL="${AATF_REBUILD_WEBHOOK_URL:-https://webhook.aatf.ai/hooks/rebuild-web}"
SECRET="${AATF_REBUILD_WEBHOOK_SECRET:-}"
SECRET_FILE="$HOME/.config/aatf/rebuild-webhook-secret"

if [ -z "$SECRET" ] && [ -f "$SECRET_FILE" ]; then
    SECRET="$(<"$SECRET_FILE")"
fi
if [ -z "$SECRET" ]; then
    echo "No secret. Set AATF_REBUILD_WEBHOOK_SECRET or put it in $SECRET_FILE" >&2
    exit 2
fi

PAYLOAD="{\"action\":\"rebuild-web\",\"requested_by\":\"$(whoami)@$(hostname -s)\"}"
# Pre-calculate the signature; command substitution inside curl headers is
# unreliable, and openssl prints "SHA2-256(stdin)= <hex>" so take the last field.
SIGNATURE="sha256=$(printf '%s' "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $NF}')"

curl -fsS -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "X-Hub-Signature-256: ${SIGNATURE}" \
    --data "$PAYLOAD"
echo
