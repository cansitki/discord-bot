---
estimated_steps: 3
estimated_files: 2
skills_used:
  - review
---

# T02: Write deployment verification script and deployment guide

**Slice:** S06 — Railway Deployment & Integration Test
**Milestone:** M001

## Description

Create an automated pre-deployment smoke test script and a comprehensive deployment guide. The verification script (`scripts/verify-deploy.sh`) proves that the bot's modules all import correctly, dependencies resolve, `railway.json` is valid, and migrations exist — the last automated gate before manual Railway deployment. The deployment guide (`docs/DEPLOY.md`) documents the complete Railway setup process so any team member can deploy.

## Steps

1. **Create `scripts/verify-deploy.sh`** — a bash script that runs pre-deployment checks. It must use the project's virtual environment at `.venv/bin/python`. The script should:
   - Check `railway.json` exists and is valid JSON
   - Verify all bot modules import successfully: `bot.bot`, `bot.config`, `bot.claude`, `bot.database`, `bot.models`, `bot.cogs.ping`, `bot.cogs.verification`, `bot.cogs.ai`, `bot.cogs.server_design`, `bot.cogs.assistant`
   - Verify `httpx` is importable (the previously-missing dependency)
   - Verify migrations directory exists and contains `.sql` files
   - Verify `pyproject.toml` exists (Railway's Railpack needs it for Python detection)
   - Print a clear PASS/FAIL summary for each check
   - Exit with code 0 if all pass, code 1 if any fail
   - Make the script executable (`chmod +x`)
   - Important: the script must NOT require real tokens (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY) — it only checks imports and file structure, not runtime behavior. The import checks should work because we're importing modules, not instantiating classes that need env vars.

2. **Create `docs/DEPLOY.md`** — Railway deployment guide covering:
   - **Prerequisites**: GitHub repo, Railway account, Discord bot token, Anthropic API key
   - **Railway Setup**: Create project, create service from GitHub repo, configure as worker (not web)
   - **Volume Setup**: Attach a volume at `/data` mount point for SQLite persistence
   - **Environment Variables**: List all required (`DISCORD_BOT_TOKEN`, `ANTHROPIC_API_KEY`) and optional (`DATABASE_PATH=/data/bot.db`, `COMMAND_PREFIX`, `DEV_GUILD_ID`, `CLAUDE_MODEL`, `RAILWAY_RUN_UID=0`) env vars with descriptions
   - **Deploy**: Push to main branch triggers auto-deploy
   - **Verify Deployment**: Checklist of manual checks against a live Discord server:
     1. Bot appears online in Discord
     2. `/ping` responds with latency
     3. Mention bot → Claude responds
     4. `/ai-channel set` configures AI channel, bot responds in it
     5. New member join → locked to #verify → admin clicks Approve → member gets access
     6. Describe server layout → Claude proposes → click Approve → channels/roles created
     7. `/summarize` works, URL fetching works
     8. Trigger Railway redeploy → bot comes back online → SQLite data persists
   - **Troubleshooting**: Common issues (missing env vars, volume permissions, command sync delay)

3. **Run the verification script** to confirm it passes in the current environment.

## Must-Haves

- [ ] `scripts/verify-deploy.sh` exists, is executable, and exits 0 when all checks pass
- [ ] Script checks: railway.json validity, all bot module imports, httpx import, migrations exist, pyproject.toml exists
- [ ] Script does NOT require real Discord/Anthropic tokens
- [ ] `docs/DEPLOY.md` exists with Railway setup steps, env var list, volume configuration, and deployment verification checklist
- [ ] Deployment guide covers SQLite persistence verification (data survives redeploy)

## Verification

- `bash scripts/verify-deploy.sh` exits 0 (all checks pass)
- `test -f docs/DEPLOY.md` — deployment guide exists
- `grep -c "^## " docs/DEPLOY.md` returns >= 3 — guide has substantive sections
- `grep -q "DATABASE_PATH" docs/DEPLOY.md` — guide documents the critical volume env var
- `grep -q "/data/bot.db" docs/DEPLOY.md` — guide mentions the Railway volume mount path

## Inputs

- `railway.json` — created by T01, verified by this script
- `pyproject.toml` — updated by T01 with httpx dependency
- `.gitignore` — updated by T01 with data/ entry
- `.env.example` — updated by T01 with Railway notes
- `bot/` — all bot modules (read-only, for import verification)
- `migrations/` — SQL migration files (read-only, for existence check)

## Observability Impact

- **New signal**: `bash scripts/verify-deploy.sh` — prints per-check ✅/❌ results and exits 0/1; acts as the pre-deploy gate
- **Inspection**: script output shows exactly which module or config file failed, enabling targeted debugging
- **Failure visibility**: any broken import, missing migration, or invalid railway.json is surfaced by name before deploy
- **No runtime change**: this task adds tooling only — no changes to bot runtime logging or error paths

## Expected Output

- `scripts/verify-deploy.sh` — deployment verification script
- `docs/DEPLOY.md` — comprehensive Railway deployment guide
