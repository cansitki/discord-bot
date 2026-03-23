#!/usr/bin/env bash
# verify-deploy.sh — Pre-deployment smoke test for Railway
#
# Checks that all bot modules import, dependencies resolve, config files
# exist, and migrations are present. Does NOT require real tokens.
#
# Usage: bash scripts/verify-deploy.sh
# Exit:  0 = all checks pass, 1 = one or more checks failed

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Use the project venv if available, fall back to system python
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "FAIL: No python interpreter found"
    exit 1
fi

# ── Counters ──────────────────────────────────────────────────────────
PASS=0
FAIL=0

check() {
    local label="$1"
    shift
    if "$@" &>/dev/null; then
        echo "  ✅  $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌  $label"
        FAIL=$((FAIL + 1))
    fi
}

echo "════════════════════════════════════════════════════════════"
echo "  Deployment Verification"
echo "════════════════════════════════════════════════════════════"
echo ""

# ── 1. Config files ──────────────────────────────────────────────────
echo "▸ Config files"
check "pyproject.toml exists" test -f pyproject.toml
check "railway.json exists" test -f railway.json
check "railway.json is valid JSON" $PYTHON -c "import json; json.load(open('railway.json'))"

# ── 2. Bot module imports ────────────────────────────────────────────
echo ""
echo "▸ Bot module imports"

MODULES=(
    "bot.bot"
    "bot.config"
    "bot.claude"
    "bot.database"
    "bot.models"
    "bot.cogs.ping"
    "bot.cogs.verification"
    "bot.cogs.ai"
    "bot.cogs.server_design"
    "bot.cogs.assistant"
    "bot.cogs.github"
    "bot.webhook"
)

for mod in "${MODULES[@]}"; do
    check "import $mod" $PYTHON -c "import $mod"
done

# ── 3. Key dependencies ─────────────────────────────────────────────
echo ""
echo "▸ Key dependencies"
check "import httpx" $PYTHON -c "import httpx"
check "import aiohttp" $PYTHON -c "import aiohttp"
check "import discord" $PYTHON -c "import discord"
check "import anthropic" $PYTHON -c "import anthropic"

# ── 4. Migrations ────────────────────────────────────────────────────
echo ""
echo "▸ Migrations"
check "migrations/ directory exists" test -d migrations
check "migrations/ contains .sql files" bash -c 'ls migrations/*.sql &>/dev/null'

# ── Summary ──────────────────────────────────────────────────────────
TOTAL=$((PASS + FAIL))
echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
    echo "  RESULT: ALL $TOTAL CHECKS PASSED ✅"
else
    echo "  RESULT: $FAIL/$TOTAL CHECKS FAILED ❌"
fi
echo "════════════════════════════════════════════════════════════"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
