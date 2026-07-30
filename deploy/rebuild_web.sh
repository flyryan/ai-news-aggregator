#!/bin/bash
# Rebuild the public web container: build, swap, verify, refresh admin bundle.
#
# Run as root under the privileged lock (aatf-rebuild-web.service wraps this in
# flock). One script so a single lock span covers the whole sequence -- with
# build and up locked separately, a deploy-webhook push could git reset the
# build context between the two steps.
#
# Build first, and only swap containers if the build succeeded: `up -d --build`
# in one step can tear down a working container and then fail to replace it,
# and restart:unless-stopped loops the broken image. The service name is
# explicit because an unscoped `up -d` rebuilds every service in the project.
set -euo pipefail

cd /home/ubuntu/ai-news-aggregator

docker compose -f docker-compose.web.yml build ai-news-aggregator
docker compose -f docker-compose.web.yml up -d ai-news-aggregator

probe() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -o /dev/null "$1"
    else
        wget -qO /dev/null "$1"
    fi
}

# Never report success on silence: a container that came up broken looks
# exactly like a slow one until something actually asks it for content.
for _ in $(seq 1 30); do
    if probe "http://127.0.0.1:7100/data/index.json"; then
        echo "site is serving /data/index.json"
        # Refresh the admin origin's copy of the bundle it just shipped. Best
        # effort: a stale panel bundle is an inconvenience, a failed rebuild
        # report on a successful rebuild is a lie.
        if [ -d /var/lib/aatf-admin ]; then
            deploy/export_web_bundle.sh \
                || echo "warning: bundle export failed; admin panel bundle is stale" >&2
        fi
        exit 0
    fi
    sleep 2
done

echo "site is not serving /data/index.json 60s after rebuild" >&2
exit 1
