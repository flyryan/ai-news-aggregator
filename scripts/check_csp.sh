#!/bin/bash
# CSP regression check for the AI News Aggregator web container.
#
# Verifies the split-CSP contract:
#   - The nginx header must NOT carry script authority (no script-src, no
#     default-src). Script policy lives in the SvelteKit build-time <meta>
#     tag with per-page 'sha256-...' hashes (frontend/svelte.config.js
#     kit.csp). A second script-src/default-src in the header would be
#     enforced IN ADDITION to the meta policy and block the hashed inline
#     hydration script, producing a blank site.
#   - The header's img-src must not include the https: wildcard source.
#   - The served HTML must contain the hashed meta CSP.
#
# Usage: check_csp.sh [BASE_URL]
#   BASE_URL defaults to http://localhost:7100 (docker-compose.web.yml
#   maps 7100:80).

set -u

BASE_URL="${1:-http://localhost:7100}"

fail() {
    echo "❌ FAIL: $1" >&2
    exit 1
}

echo "=========================================="
echo "CSP Regression Check"
echo "Target: $BASE_URL/"
echo "=========================================="

# --- Header checks -----------------------------------------------------------

HEADERS=$(curl -sSIL --max-time 30 "$BASE_URL/") \
    || fail "could not fetch headers from $BASE_URL/ (is the web container running?)"

# Last CSP header wins if redirects produced multiple responses
CSP_HEADER=$(printf '%s\n' "$HEADERS" | grep -i '^content-security-policy:' | tail -n 1 || true)

if [ -z "$CSP_HEADER" ]; then
    fail "no Content-Security-Policy header returned by nginx"
fi

if printf '%s' "$CSP_HEADER" | grep -qi 'script-src'; then
    fail "nginx CSP header contains script-src — script authority must live only in the SvelteKit <meta> CSP (see nginx.conf comment)"
fi

if printf '%s' "$CSP_HEADER" | grep -qi 'default-src'; then
    fail "nginx CSP header contains default-src — it applies to scripts and would block the hashed inline hydration script"
fi

IMG_SRC_DIRECTIVE=$(printf '%s' "$CSP_HEADER" | tr ';' '\n' | grep -i 'img-src' || true)
if printf '%s' "$IMG_SRC_DIRECTIVE" | grep -qi 'https:'; then
    fail "nginx CSP img-src contains 'https:' (wildcard remote images must stay disabled)"
fi

# Redundant with the script-src check above, but guards a partial regression
if printf '%s' "$CSP_HEADER" | tr ';' '\n' | grep -i 'script-src' | grep -qi 'unsafe-inline'; then
    fail "nginx CSP header contains 'unsafe-inline' inside script-src"
fi

echo "✅ nginx header OK: no script-src/default-src, img-src has no https: wildcard"

# --- Meta tag checks ---------------------------------------------------------

BODY=$(curl -sSL --max-time 30 "$BASE_URL/") \
    || fail "could not fetch HTML body from $BASE_URL/"

META_TAG=$(printf '%s' "$BODY" | grep -io '<meta[^>]*http-equiv="content-security-policy"[^>]*>' | head -n 1 || true)

if [ -z "$META_TAG" ]; then
    fail "served HTML has no http-equiv=\"content-security-policy\" meta tag (kit.csp missing from the frontend build?)"
fi

SCRIPT_SRC_DIRECTIVE=$(printf '%s' "$META_TAG" | tr ';' '\n' | grep -i 'script-src' || true)
if ! printf '%s' "$SCRIPT_SRC_DIRECTIVE" | grep -q 'sha256-'; then
    fail "meta CSP script-src has no 'sha256-' hash — the inline hydration script would be blocked"
fi

echo "✅ meta CSP OK: script-src carries a sha256- hash"
echo ""
echo "✅ PASS: CSP split contract intact"
exit 0
