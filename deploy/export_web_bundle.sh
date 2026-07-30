#!/bin/bash
# Export the built SPA bundle out of the web Docker image for the admin origin.
#
# The host has no node and web/_app is gitignored, so the only place the built
# bundle exists on the host is inside the image that Dockerfile.web builds. The
# admin service serves the panel (and previews) from the exported copy, which
# also guarantees the admin origin serves byte-identical pages -- same CSP
# hashes, same chunk names -- to what nginx serves on news.aatf.ai.
#
# Run as root. Called by deploy/setup_admin_service.sh at provision time and by
# aatf-rebuild-web.service after every rebuild, so the export can only go stale
# if someone rebuilds the image outside those paths.
#
#   deploy/export_web_bundle.sh [dest]   # default /var/lib/aatf-admin/site
set -euo pipefail

DEST="${1:-/var/lib/aatf-admin/site}"
CONTAINER="ai-news-aggregator"

# Prefer the image the running container was created from -- that is literally
# what the public site is serving. Fall back to the compose-built image name
# (compose v2 uses <project>-<service>) for a freshly built but not yet
# started image.
IMAGE="$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)"
if [ -z "$IMAGE" ]; then
    IMAGE="ai-news-aggregator-ai-news-aggregator:latest"
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "error: web image not found ($IMAGE). Build it first:" >&2
    echo "  docker compose -f docker-compose.web.yml build ai-news-aggregator" >&2
    exit 1
fi

# docker create (not run): nothing executes, and copying from a created
# container sees the image contents without any volume mounts, so web/data and
# web/assets stay out of the export -- the admin service serves those live from
# the checkout, which the git sync keeps current.
CID="$(docker create "$IMAGE")"
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT

STAGE="${DEST}.new"
rm -rf "$STAGE"
mkdir -p "$STAGE"
docker cp "$CID:/app/web/." "$STAGE/"
rm -rf "$STAGE/data" "$STAGE/assets"

if [ ! -f "$STAGE/index.html" ]; then
    echo "error: export produced no index.html -- wrong image?" >&2
    rm -rf "$STAGE"
    exit 1
fi

# Swap, keeping the window where $DEST is absent as small as possible. The
# admin service falls back to the checkout only when index.html is missing, so
# a request landing mid-swap degrades rather than mixing bundles.
OLD="${DEST}.old"
rm -rf "$OLD"
if [ -d "$DEST" ]; then
    mv "$DEST" "$OLD"
fi
mv "$STAGE" "$DEST"
rm -rf "$OLD"

# aatfadmin only ever reads this.
if id aatfadmin >/dev/null 2>&1; then
    chown -R root:root "$DEST"
fi
chmod -R a+rX "$DEST"

echo "Exported site bundle from $IMAGE to $DEST"
