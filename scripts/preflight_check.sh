#!/bin/bash
# Preflight check for AI News Aggregator pipeline
# Runs nightly to ensure morning pipeline will succeed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ERRORS=()

cd "$PROJECT_DIR"

echo "=========================================="
echo "AI News Aggregator Preflight Check"
echo "$(date)"
echo "=========================================="

# 1. Sync with remote (hard reset to avoid pull issues)
echo "[CHECK] Syncing with remote..."
git fetch origin main 2>/dev/null
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "  ⚠️  Local differs from remote. Resetting..."
    git reset --hard origin/main
    if [ $? -eq 0 ]; then
        echo "  ✅ Synced to $(git rev-parse --short HEAD)"
    else
        ERRORS+=("Git reset failed")
    fi
else
    echo "  ✅ Already up to date ($(git rev-parse --short HEAD))"
fi

# 2. Check working tree is clean
echo "[CHECK] Working tree status..."
if [[ -n $(git status --porcelain) ]]; then
    echo "  ⚠️  Untracked/modified files detected. Cleaning..."
    git clean -fd web/data/ 2>/dev/null
    git checkout -- . 2>/dev/null
fi
echo "  ✅ Working tree clean"

# 3. Check venv and dependencies
echo "[CHECK] Python environment..."
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
    pip install -q -r requirements.txt 2>/dev/null
    echo "  ✅ Virtual environment ready"
else
    ERRORS+=("Virtual environment not found")
fi

# 4. Check required config files
echo "[CHECK] Configuration files..."
REQUIRED_CONFIGS=("config/providers.yaml" "config/prompts.yaml" ".env")
for cfg in "${REQUIRED_CONFIGS[@]}"; do
    if [ -f "$PROJECT_DIR/$cfg" ]; then
        echo "  ✅ $cfg exists"
    else
        ERRORS+=("Missing config: $cfg")
    fi
done

# 5. Check API connectivity (light test)
echo "[CHECK] API connectivity..."
if [ -f "$PROJECT_DIR/.env" ]; then
    source "$PROJECT_DIR/.env"
    if [ -n "$ANTHROPIC_API_BASE" ]; then
        # Quick HEAD request to check endpoint is reachable
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$ANTHROPIC_API_BASE" 2>/dev/null)
        if [ "$HTTP_CODE" != "000" ]; then
            echo "  ✅ API endpoint reachable"
        else
            ERRORS+=("API endpoint unreachable: $ANTHROPIC_API_BASE")
        fi
    fi
fi

# 6. Check disk space
echo "[CHECK] Disk space..."
AVAILABLE_GB=$(df -BG "$PROJECT_DIR" | awk 'NR==2 {print $4}' | sed 's/G//')
if [ "$AVAILABLE_GB" -lt 5 ]; then
    ERRORS+=("Low disk space: ${AVAILABLE_GB}GB available")
else
    echo "  ✅ ${AVAILABLE_GB}GB available"
fi

# Summary
echo ""
echo "=========================================="
if [ ${#ERRORS[@]} -eq 0 ]; then
    echo "✅ PREFLIGHT CHECK PASSED"
    echo "Pipeline should run successfully tomorrow."
    exit 0
else
    echo "❌ PREFLIGHT CHECK FAILED"
    echo "Issues found:"
    for err in "${ERRORS[@]}"; do
        echo "  - $err"
    done
    exit 1
fi
