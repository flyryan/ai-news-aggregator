#!/bin/bash
# Publish an approved preview to the live site: copy its files into the
# checkout, commit them SSH-signed, push to main, delete the preview.
#
# Runs as ubuntu inside aatf-promote@<job>.service -- never as the admin
# service. aatfadmin cannot write the checkout and holds no signing key, and
# must not: this script lives in the repo (owned by ubuntu), so a compromised
# panel cannot edit what it does, only ask for it to run against a job id that
# sudo has already pattern-checked.
#
# Everything here re-validates its inputs rather than trusting the caller:
# the job id shape, the allowlist of publishable files, and the signing
# configuration. The allowlist must stay identical to PUBLISHABLE in
# admin_service/preview.py; tests/preview_wiring_test.py pins the two equal.
#
# Usage: promote_preview.sh <job-id>       # e.g. hero-2026-07-29
# Exit:  0 published | 3 bad input | 4 nothing to publish or unchanged
#        5 signing not configured | 6 git failure
set -euo pipefail

JOB="${1:-}"
REPO_DIR="${PROMOTE_REPO_DIR:-/home/ubuntu/ai-news-aggregator}"
PREVIEWS_ROOT="${PROMOTE_PREVIEWS_ROOT:-/var/lib/aatf-admin/previews}"

# One name per line between the markers; the wiring test parses this block.
# BEGIN PUBLISHABLE
PUBLISHABLE=(
    summary.json
    hero.webp
    news.json
    research.json
    social.json
    reddit.json
    replay-index.json
    replay-stream.json.gz
    replay-prompts.json.gz
)
# END PUBLISHABLE

if ! printf '%s' "$JOB" | grep -Eq '^(hero|report)-[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
    echo "invalid job id: expected <kind>-YYYY-MM-DD, got '${JOB}'" >&2
    exit 3
fi
DATE="${JOB#*-}"

SOURCE_DIR="$PREVIEWS_ROOT/$JOB/web/data/$DATE"
if [ ! -d "$SOURCE_DIR" ]; then
    echo "no preview data at $SOURCE_DIR" >&2
    exit 4
fi

cd "$REPO_DIR"

# Check signing BEFORE touching the tree. An unsigned tip makes deploy.sh
# abort, which freezes the site on stale content -- a failure that surfaces
# far from its cause.
SIGNING_KEY="$(git config --get user.signingkey || true)"
GPG_SIGN="$(git config --get commit.gpgsign || true)"
if [ -z "$SIGNING_KEY" ] || [ "$GPG_SIGN" != "true" ]; then
    echo "commit signing is not configured for this user, so the promoted" >&2
    echo "commit would be rejected by the deploy gate. Configure" >&2
    echo "user.signingkey and commit.gpgsign=true, then promote again." >&2
    exit 5
fi

TARGET_DIR="web/data/$DATE"
mkdir -p "$TARGET_DIR"

COPIED=()
for name in "${PUBLISHABLE[@]}"; do
    if [ -f "$SOURCE_DIR/$name" ]; then
        cp -p "$SOURCE_DIR/$name" "$TARGET_DIR/$name"
        COPIED+=("$TARGET_DIR/$name")
    fi
done

if [ "${#COPIED[@]}" -eq 0 ]; then
    echo "preview contained no publishable files" >&2
    exit 4
fi

git add -- "${COPIED[@]}"

if git diff --cached --quiet; then
    echo "promoted files were identical to what is already published" >&2
    exit 4
fi

git commit -S -m "data: promote ${JOB%%-*} preview for $DATE

Approved via the admin panel (job $JOB)." || exit 6

if ! git push origin HEAD:main; then
    echo "commit created but push failed. The change is committed locally;" >&2
    echo "resolve and push manually." >&2
    exit 6
fi

# Reap on approval, as the spec requires. Group-writable tree; the 7-day
# reaper is the backstop if this fails.
rm -rf "$PREVIEWS_ROOT/$JOB" || true

printf 'published %s file(s) for %s:\n' "${#COPIED[@]}" "$DATE"
printf '  %s\n' "${COPIED[@]}"
