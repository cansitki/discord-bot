---
estimated_steps: 4
estimated_files: 4
skills_used:
  - review
  - lint
---

# T01: Fix dependencies, create Railway config, and update project files

**Slice:** S06 — Railway Deployment & Integration Test
**Milestone:** M001

## Description

The bot codebase is complete (206 tests pass) but has deployment blockers: `httpx` is imported by `bot/cogs/assistant.py` but not declared in `pyproject.toml`, no `railway.json` exists, `data/` (local SQLite directory) isn't gitignored, and `.env.example` doesn't document Railway-specific env vars. This task fixes all four issues to make the project deployment-ready.

## Steps

1. **Add `httpx>=0.27` to `pyproject.toml` dependencies.** Open `pyproject.toml`, find the `dependencies` list, and add `"httpx>=0.27"` after the existing entries. This is required because `bot/cogs/assistant.py` does `import httpx` for URL fetching but the dependency is only available transitively through `anthropic`. Declaring it explicitly prevents breakage if `anthropic` ever drops it.

2. **Create `railway.json` with minimal config.** Create a new file `railway.json` at the project root with:
   ```json
   {
     "$schema": "https://railway.com/railway.schema.json",
     "build": {},
     "deploy": {
       "startCommand": "python -m bot",
       "restartPolicyType": "ON_FAILURE",
       "restartPolicyMaxRetries": 10
     }
   }
   ```
   No build command is needed — Railway's Railpack auto-detects Python from `pyproject.toml`. The `restartPolicyType: ON_FAILURE` ensures the bot restarts on crashes but not on clean shutdowns. No healthcheck is configured because this is a worker process (Discord Gateway), not an HTTP server.

3. **Add `data/` to `.gitignore`.** Append `data/` to the `.gitignore` file. The bot creates `data/bot.db` locally (SQLite), and this directory must not be committed. Add it near the end, after the existing patterns, with a comment explaining why.

4. **Update `.env.example` with Railway deployment notes.** Add the following env vars with documentation comments:
   - `RAILWAY_RUN_UID=0` — Ensures write permissions on Railway volume mount (may be needed depending on container user)
   - Update the `DATABASE_PATH` comment to explain the Railway volume mount: "On Railway, set to `/data/bot.db` (volume mount path) to persist across redeploys"
   - Add `CLAUDE_MODEL` — Already used by `config.py` but not documented in `.env.example`
   - Add `DEV_GUILD_ID` docs clarification about Railway vs local usage

## Must-Haves

- [ ] `httpx>=0.27` is listed in `pyproject.toml` `[project] dependencies`
- [ ] `railway.json` exists at project root with valid JSON, start command `python -m bot`, and `ON_FAILURE` restart policy
- [ ] `data/` is in `.gitignore`
- [ ] `.env.example` documents `DATABASE_PATH` Railway volume mount usage and `CLAUDE_MODEL`
- [ ] All 206+ existing tests still pass after changes

## Verification

- `.venv/bin/python -m pytest` — all tests pass (changes are config-only, no code logic changed)
- `.venv/bin/pip install --dry-run .` — dependency resolution includes `httpx` (look for httpx in the output)
- `python -c "import json; d=json.load(open('railway.json')); assert d['deploy']['startCommand'] == 'python -m bot'"` — Railway config is valid
- `grep -q '^data/' .gitignore` — data directory is gitignored
- `grep -q 'CLAUDE_MODEL' .env.example` — new env vars documented

## Observability Impact

- **Dependency resolution**: `pip install --dry-run .` now includes `httpx` in the resolved set, proving the transitive dependency is explicitly declared. A future agent can verify this with `pip show httpx` after install.
- **Railway config validation**: `railway.json` is machine-parseable — any agent can verify the start command and restart policy with `python -c "import json; ..."`.
- **Failure visibility**: No runtime behavior changes — all four modifications are config/metadata files. Failure would manifest as import errors at deploy time (`ModuleNotFoundError: httpx`) or Railway misconfiguration (missing start command).

## Inputs

- `pyproject.toml` — current project dependencies (missing `httpx`)
- `.gitignore` — current gitignore (missing `data/`)
- `.env.example` — current env var documentation (missing Railway notes)
- `bot/cogs/assistant.py` — confirms `httpx` is imported (read-only, for reference)

## Expected Output

- `pyproject.toml` — updated with `httpx>=0.27` in dependencies
- `railway.json` — new file, Railway deployment configuration
- `.gitignore` — updated with `data/` entry
- `.env.example` — updated with Railway env var documentation
