# S06: Railway Deployment & Integration Test

**Goal:** Bot is deployment-ready for Railway with all dependencies declared, configuration files in place, and a verification script proving the bot can start and resolve all imports.
**Demo:** `railway.json` exists with correct start command, `pip install .` in a clean venv succeeds (including `httpx`), all 206+ tests pass, and a deployment verification script confirms the bot entry point loads without error.

## Must-Haves

- `httpx` added to `pyproject.toml` dependencies (deployment blocker — `assistant.py` imports it)
- `railway.json` created with `python -m bot` start command, no build command (Railpack auto-detects Python)
- `data/` added to `.gitignore` so local SQLite files aren't committed
- `.env.example` updated with Railway-specific env var documentation (`DATABASE_PATH` for volume mount, `RAILWAY_RUN_UID`)
- Deployment verification script that proves dependency resolution and bot module loading work
- Deployment guide documenting Railway setup steps (volume, env vars, deploy)

## Proof Level

- This slice proves: operational + final-assembly
- Real runtime required: yes (live Railway deployment + Discord server — verified manually)
- Human/UAT required: yes (manual testing against live Discord server per deployment checklist)

## Verification

- `.venv/bin/python -m pytest` — all existing tests still pass (206+)
- `.venv/bin/pip install --dry-run .` — dependency resolution succeeds with `httpx` declared
- `test -f railway.json && python -c "import json; json.load(open('railway.json'))"` — valid Railway config exists
- `bash scripts/verify-deploy.sh` — deployment verification script passes (import checks, config validation)
- `grep -q "^data/" .gitignore` — data directory is gitignored
- `python -c "from bot.config import Config; Config.from_env()" 2>&1 | grep -q "ValueError\|DISCORD_BOT_TOKEN"` — Config.from_env() raises ValueError naming the missing env var when required vars are absent

## Observability / Diagnostics

- Runtime signals: `bot/__main__.py` prints `Config loaded: ...` at startup listing which env vars were found; `bot.py` on_ready prints `Bot online: ... (guilds: N, db: status)`
- Inspection surfaces: `scripts/verify-deploy.sh` exercises the bot module loading path without real tokens; Railway dashboard shows process logs
- Failure visibility: `Config.from_env()` raises `ValueError` naming the missing env var; `DatabaseManager` logs connection failures; `setup_hook` logs each cog load
- Redaction constraints: `config.py` never logs secret values, only variable names

## Integration Closure

- Upstream surfaces consumed: all modules from S01–S05 (`bot/__main__.py`, `bot/bot.py`, `bot/config.py`, `bot/database.py`, `bot/claude.py`, `bot/models.py`, all cogs, all migrations)
- New wiring introduced in this slice: `railway.json` (Railway config-as-code), `scripts/verify-deploy.sh` (deployment smoke test)
- What remains before the milestone is truly usable end-to-end: manual verification against a live Discord server on Railway (documented in deployment guide, not automatable)

## Tasks

- [x] **T01: Fix dependencies, create Railway config, and update project files** `est:30m`
  - Why: The bot cannot deploy to Railway without `httpx` in declared dependencies (import crash), a `railway.json` config file (Railway needs to know how to run it), and `data/` in `.gitignore` (prevent committing local SQLite). These are all deployment blockers.
  - Files: `pyproject.toml`, `railway.json`, `.gitignore`, `.env.example`
  - Do: Add `httpx>=0.27` to pyproject.toml dependencies. Create `railway.json` with start command `python -m bot` and restart policy. Add `data/` to `.gitignore`. Update `.env.example` with Railway volume mount notes for `DATABASE_PATH` and `RAILWAY_RUN_UID`.
  - Verify: `.venv/bin/pip install --dry-run .` succeeds, `python -c "import json; json.load(open('railway.json'))"` passes, `grep -q "^data/" .gitignore` succeeds, `.venv/bin/python -m pytest` — all tests pass
  - Done when: All four files are updated/created, dependency resolution includes httpx, existing tests still pass

- [x] **T02: Write deployment verification script and deployment guide** `est:30m`
  - Why: A verification script proves the bot module can load and all imports resolve without real tokens — the last automated check before manual Railway deployment. The deployment guide documents the Railway setup steps so any team member can deploy.
  - Files: `scripts/verify-deploy.sh`, `docs/DEPLOY.md`
  - Do: Create `scripts/verify-deploy.sh` that verifies: (1) `railway.json` exists and is valid JSON, (2) all bot modules import successfully (`python -c "import bot.bot; import bot.config; import bot.claude; ..."` etc.), (3) `httpx` is importable, (4) migrations directory has SQL files. Create `docs/DEPLOY.md` with Railway setup steps: create project, attach volume at `/data`, set env vars (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY, DATABASE_PATH=/data/bot.db), deploy from GitHub, verify with the deployment checklist (bot online, /ping, Claude responds, verification gate, server design, SQLite persistence across redeploy).
  - Verify: `bash scripts/verify-deploy.sh` exits 0, `test -f docs/DEPLOY.md`, `grep -c "^## " docs/DEPLOY.md` returns >= 3
  - Done when: Verification script passes, deployment guide covers all setup steps and the full integration verification checklist

## Files Likely Touched

- `pyproject.toml`
- `railway.json`
- `.gitignore`
- `.env.example`
- `scripts/verify-deploy.sh`
- `docs/DEPLOY.md`
