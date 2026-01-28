#!/bin/bash
# Preflight diagnostic for AI News Aggregator pipeline
# READ-ONLY check - does NOT modify anything
# Reports potential issues that could prevent the morning pipeline run

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ISSUES=()

cd "$PROJECT_DIR"

echo "=========================================="
echo "AI News Aggregator Preflight Diagnostic"
echo "$(date)"
echo "=========================================="
echo ""

# 1. Check git remote sync status
echo "[CHECK] Git repository sync status..."
git fetch origin main 2>/dev/null
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)

if [ -z "$LOCAL" ] || [ -z "$REMOTE" ]; then
    ISSUES+=("GIT: Cannot determine local/remote HEAD - git fetch failed")
    echo "  ❌ Git fetch failed"
elif [ "$LOCAL" != "$REMOTE" ]; then
    LOCAL_SHORT=$(git rev-parse --short HEAD)
    REMOTE_SHORT=$(git rev-parse --short origin/main)
    # Check if local is ahead, behind, or diverged
    MERGE_BASE=$(git merge-base HEAD origin/main 2>/dev/null)
    if [ "$MERGE_BASE" = "$REMOTE" ]; then
        AHEAD=$(git rev-list --count origin/main..HEAD)
        ISSUES+=("GIT: Local is $AHEAD commit(s) ahead of remote ($LOCAL_SHORT vs $REMOTE_SHORT) - unpushed changes")
        echo "  ⚠️  Local ahead of remote by $AHEAD commit(s)"
    elif [ "$MERGE_BASE" = "$LOCAL" ]; then
        BEHIND=$(git rev-list --count HEAD..origin/main)
        echo "  ℹ️  Local is $BEHIND commit(s) behind remote (will sync on run)"
    else
        ISSUES+=("GIT: Diverged branches - local $LOCAL_SHORT, remote $REMOTE_SHORT (force-push detected?)")
        echo "  ⚠️  Branches have diverged (force-push?)"
    fi
else
    echo "  ✅ In sync with remote ($(git rev-parse --short HEAD))"
fi

# 2. Check for uncommitted/untracked files that could cause issues
echo ""
echo "[CHECK] Working tree status..."
MODIFIED=$(git status --porcelain 2>/dev/null | grep -c "^ M\|^M ")
UNTRACKED=$(git status --porcelain 2>/dev/null | grep -c "^??")
STAGED=$(git status --porcelain 2>/dev/null | grep -c "^[MADRC]")

if [ "$MODIFIED" -gt 0 ]; then
    ISSUES+=("GIT: $MODIFIED modified file(s) - could cause merge conflicts")
    echo "  ⚠️  $MODIFIED modified file(s)"
fi
if [ "$UNTRACKED" -gt 0 ]; then
    # Check if untracked files are in web/data (these get cleaned)
    UNTRACKED_DATA=$(git status --porcelain 2>/dev/null | grep "^??" | grep -c "web/data/")
    UNTRACKED_OTHER=$((UNTRACKED - UNTRACKED_DATA))
    if [ "$UNTRACKED_OTHER" -gt 0 ]; then
        echo "  ℹ️  $UNTRACKED_OTHER untracked file(s) outside web/data/"
    fi
    if [ "$UNTRACKED_DATA" -gt 0 ]; then
        echo "  ✅ $UNTRACKED_DATA untracked file(s) in web/data/ (will be cleaned)"
    fi
fi
if [ "$STAGED" -gt 0 ]; then
    ISSUES+=("GIT: $STAGED staged change(s) - uncommitted work")
    echo "  ⚠️  $STAGED staged change(s)"
fi
if [ "$MODIFIED" -eq 0 ] && [ "$STAGED" -eq 0 ]; then
    echo "  ✅ Working tree clean"
fi

# 3. Check Python environment
echo ""
echo "[CHECK] Python environment..."
if [ ! -f "$PROJECT_DIR/venv/bin/activate" ]; then
    ISSUES+=("PYTHON: Virtual environment not found at venv/")
    echo "  ❌ venv/ not found"
elif [ ! -f "$PROJECT_DIR/venv/bin/python" ]; then
    ISSUES+=("PYTHON: venv exists but python binary missing")
    echo "  ❌ venv/bin/python missing"
else
    PYTHON_VERSION=$("$PROJECT_DIR/venv/bin/python" --version 2>&1)
    echo "  ✅ venv ready ($PYTHON_VERSION)"
    
    # Check if requirements might be outdated
    if [ "$PROJECT_DIR/requirements.txt" -nt "$PROJECT_DIR/venv/bin/pip" ]; then
        echo "  ℹ️  requirements.txt newer than venv - deps may update on run"
    fi
fi

# 4. Check required configuration files
echo ""
echo "[CHECK] Configuration files..."
REQUIRED_CONFIGS=("config/providers.yaml" "config/prompts.yaml" ".env")
for cfg in "${REQUIRED_CONFIGS[@]}"; do
    if [ -f "$PROJECT_DIR/$cfg" ]; then
        echo "  ✅ $cfg"
    else
        ISSUES+=("CONFIG: Missing required file: $cfg")
        echo "  ❌ $cfg MISSING"
    fi
done

# 5. Check API credentials and connectivity
echo ""
echo "[CHECK] API configuration..."
if [ -f "$PROJECT_DIR/.env" ]; then
    # Source .env carefully (suppress errors from malformed lines)
    set +e
    source "$PROJECT_DIR/.env" 2>/dev/null
    set -e
    
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        ISSUES+=("API: ANTHROPIC_API_KEY not set in .env")
        echo "  ❌ ANTHROPIC_API_KEY not set"
    else
        echo "  ✅ ANTHROPIC_API_KEY set (${#ANTHROPIC_API_KEY} chars)"
    fi
    
    if [ -z "$ANTHROPIC_API_BASE" ]; then
        ISSUES+=("API: ANTHROPIC_API_BASE not set in .env")
        echo "  ❌ ANTHROPIC_API_BASE not set"
    else
        echo "  ✅ ANTHROPIC_API_BASE: $ANTHROPIC_API_BASE"
        
        # Test connectivity (HEAD request, 5s timeout)
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$ANTHROPIC_API_BASE" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "000" ]; then
            ISSUES+=("API: Cannot reach $ANTHROPIC_API_BASE (connection failed)")
            echo "  ❌ API endpoint unreachable"
        else
            echo "  ✅ API endpoint reachable (HTTP $HTTP_CODE)"
        fi
    fi
    
    if [ -z "$TWITTERAPI_IO_KEY" ]; then
        echo "  ℹ️  TWITTERAPI_IO_KEY not set (Twitter collection will be skipped)"
    else
        echo "  ✅ TWITTERAPI_IO_KEY set"
    fi
fi

# 6. Check disk space
echo ""
echo "[CHECK] Disk space..."
AVAILABLE_KB=$(df -k "$PROJECT_DIR" | awk 'NR==2 {print $4}')
AVAILABLE_GB=$((AVAILABLE_KB / 1024 / 1024))
AVAILABLE_MB=$((AVAILABLE_KB / 1024))

if [ "$AVAILABLE_GB" -lt 1 ]; then
    ISSUES+=("DISK: Only ${AVAILABLE_MB}MB available - pipeline needs ~500MB per run")
    echo "  ❌ Low disk space: ${AVAILABLE_MB}MB"
elif [ "$AVAILABLE_GB" -lt 5 ]; then
    echo "  ⚠️  ${AVAILABLE_GB}GB available (consider cleanup)"
else
    echo "  ✅ ${AVAILABLE_GB}GB available"
fi

# 7. Check if Docker container is running (if used)
echo ""
echo "[CHECK] Docker status..."
if command -v docker &>/dev/null; then
    CONTAINER_STATUS=$(docker ps --filter "name=ai-news-aggregator" --format "{{.Status}}" 2>/dev/null)
    if [ -n "$CONTAINER_STATUS" ]; then
        echo "  ✅ Container running: $CONTAINER_STATUS"
    else
        echo "  ℹ️  Container not running (runs via cron, not Docker)"
    fi
else
    echo "  ℹ️  Docker not available (pipeline runs directly)"
fi

# 8. Check the cron job itself
echo ""
echo "[CHECK] Cron job configuration..."
CRON_ENTRY=$(crontab -l 2>/dev/null | grep "daily_pipeline.sh" || true)
if [ -z "$CRON_ENTRY" ]; then
    ISSUES+=("CRON: daily_pipeline.sh not found in crontab")
    echo "  ❌ Pipeline cron job not found"
else
    echo "  ✅ Cron: $CRON_ENTRY"
fi

# 9. Check last run status
echo ""
echo "[CHECK] Last pipeline run..."
LATEST_LOG=$(ls -t "$PROJECT_DIR/logs/pipeline_"*.log 2>/dev/null | head -1)
if [ -n "$LATEST_LOG" ]; then
    LOG_DATE=$(basename "$LATEST_LOG" | sed 's/pipeline_\(.*\)\.log/\1/')
    LOG_SIZE=$(du -h "$LATEST_LOG" | cut -f1)
    
    # Check if it completed successfully
    if grep -q "Pipeline completed successfully\|All sources collected successfully" "$LATEST_LOG" 2>/dev/null; then
        echo "  ✅ Last run ($LOG_DATE): Completed successfully ($LOG_SIZE)"
    elif grep -q "fatal:\|Error:\|FAILED" "$LATEST_LOG" 2>/dev/null; then
        LAST_ERROR=$(grep -E "fatal:|Error:|FAILED" "$LATEST_LOG" | tail -1)
        ISSUES+=("PIPELINE: Last run ($LOG_DATE) had errors: $LAST_ERROR")
        echo "  ⚠️  Last run ($LOG_DATE): Had errors"
        echo "      $LAST_ERROR"
    else
        echo "  ℹ️  Last run ($LOG_DATE): Status unclear ($LOG_SIZE)"
    fi
else
    echo "  ℹ️  No previous logs found"
fi

# 10. Check for stale lock files or running processes
echo ""
echo "[CHECK] Process status..."
RUNNING_PIPELINE=$(pgrep -f "run_pipeline.py" 2>/dev/null || true)
if [ -n "$RUNNING_PIPELINE" ]; then
    ISSUES+=("PROCESS: Pipeline already running (PID: $RUNNING_PIPELINE)")
    echo "  ⚠️  Pipeline process already running (PID: $RUNNING_PIPELINE)"
else
    echo "  ✅ No conflicting processes"
fi

# Summary
echo ""
echo "=========================================="
if [ ${#ISSUES[@]} -eq 0 ]; then
    echo "✅ PREFLIGHT CHECK PASSED"
    echo "No issues detected. Pipeline should run successfully."
    exit 0
else
    echo "⚠️  PREFLIGHT CHECK: ${#ISSUES[@]} ISSUE(S) FOUND"
    echo ""
    echo "Issues to address:"
    for i in "${!ISSUES[@]}"; do
        echo "  $((i+1)). ${ISSUES[$i]}"
    done
    exit 1
fi
