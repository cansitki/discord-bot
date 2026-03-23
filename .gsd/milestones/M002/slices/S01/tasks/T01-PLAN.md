---
estimated_steps: 5
estimated_files: 6
skills_used:
  - test
  - review
---

# T01: Add GitHub config, channel_repos migration, and ChannelRepo model

**Slice:** S01 — GitHub Client + Channel-Repo Linking
**Milestone:** M002

## Description

Add the data foundation that every other task in this slice depends on: optional GitHub environment variables in Config, the channel_repos SQLite migration, the ChannelRepo dataclass, and the PyJWT[crypto] dependency. Also set up the .venv in the worktree so all subsequent tasks have a working Python environment.

## Steps

1. **Set up .venv and install dependencies.** Create a virtual environment in the worktree root, install current dependencies, verify pytest runs and existing tests pass. This is critical — K002 says system Python is externally managed.

2. **Add PyJWT[crypto] to pyproject.toml.** Add `"PyJWT[crypto]>=2.8"` to the `dependencies` list in `pyproject.toml`. Reinstall dependencies. Verify `import jwt` and `from jwt import encode` work.

3. **Add optional GitHub fields to Config.** Add three optional fields to the frozen Config dataclass: `github_app_id: str | None`, `github_private_key: str | None`, `github_webhook_secret: str | None`. In `from_env()`, load them with `os.getenv()` defaulting to `None`. Follow K003 — use `os.getenv("GITHUB_APP_ID") or None` to treat empty string as missing. Add the loaded var names to the startup diagnostic log line. These are optional — the bot should still start without them (GitHub features just won't work).

4. **Create migration 003_channel_repos.sql.** Create `migrations/003_channel_repos.sql` with:
   ```sql
   CREATE TABLE IF NOT EXISTS channel_repos (
       guild_id INTEGER NOT NULL,
       channel_id INTEGER NOT NULL,
       repo_owner TEXT NOT NULL,
       repo_name TEXT NOT NULL,
       linked_by INTEGER NOT NULL,
       linked_at TEXT NOT NULL DEFAULT (datetime('now')),
       PRIMARY KEY (guild_id, channel_id)
   );
   ```
   The primary key on (guild_id, channel_id) means each channel can be linked to exactly one repo. The `linked_by` field records the user_id who created the link.

5. **Add ChannelRepo dataclass to bot/models.py.** Add a `ChannelRepo` dataclass alongside the existing `GuildConfig` and `ActionLog` models. Fields: `guild_id: int`, `channel_id: int`, `repo_owner: str`, `repo_name: str`, `linked_by: int`, `linked_at: str | None`. Include a `from_row()` classmethod that constructs from an aiosqlite.Row. Add a `repo_full_name` property that returns `f"{self.repo_owner}/{self.repo_name}"`.

6. **Update .env.example.** Add a GitHub section with:
   ```
   # ── GitHub App (optional — required for GitHub integration) ────────
   # Register a GitHub App at https://github.com/settings/apps
   GITHUB_APP_ID=
   GITHUB_PRIVATE_KEY=
   GITHUB_WEBHOOK_SECRET=
   ```

7. **Update tests.** Update `tests/test_config.py` to verify GitHub vars load as None by default and as actual values when set. Update `tests/test_models.py` to verify ChannelRepo.from_row() round-trips correctly and repo_full_name returns the right string. Verify migration 003 creates the channel_repos table.

## Must-Haves

- [ ] .venv created and working with all dependencies installed (including PyJWT[crypto])
- [ ] Config.from_env() loads github_app_id, github_private_key, github_webhook_secret as optional fields (None when missing)
- [ ] Migration 003 creates channel_repos table with correct schema and PK
- [ ] ChannelRepo dataclass with from_row() and repo_full_name property
- [ ] .env.example updated with GitHub App variables
- [ ] Existing tests still pass

## Verification

- `.venv/bin/python -m pytest tests/test_config.py tests/test_models.py -v` — all pass including new tests
- `.venv/bin/python -c "import jwt; from bot.config import Config; from bot.models import ChannelRepo; print('OK')"` succeeds
- `.venv/bin/python -m pytest tests/ -v` — all existing tests still pass (no regressions)

## Inputs

- `bot/config.py` — existing Config dataclass to extend
- `bot/models.py` — existing models to add ChannelRepo alongside
- `bot/database.py` — DatabaseManager with migration runner (no changes needed, just context)
- `migrations/001_initial.sql` — existing migration format to follow
- `migrations/002_ai_channel.sql` — existing migration format to follow
- `pyproject.toml` — dependency list to update
- `.env.example` — env var documentation to update
- `tests/test_config.py` — existing config tests to extend
- `tests/test_models.py` — existing model tests to extend
- `tests/conftest.py` — existing fixtures (may need migration fixture update)

## Expected Output

- `bot/config.py` — extended with github_app_id, github_private_key, github_webhook_secret
- `bot/models.py` — extended with ChannelRepo dataclass
- `migrations/003_channel_repos.sql` — new migration file
- `pyproject.toml` — PyJWT[crypto] added to dependencies
- `.env.example` — GitHub App section added
- `tests/test_config.py` — extended with GitHub config tests
- `tests/test_models.py` — extended with ChannelRepo tests

## Observability Impact

- **Config startup diagnostic log** now includes GITHUB_APP_ID, GITHUB_PRIVATE_KEY, GITHUB_WEBHOOK_SECRET in the loaded vars list when present — a future agent can inspect the Config loaded line to confirm GitHub credentials are loaded.
- **channel_repos table** is a new queryable surface: `SELECT * FROM channel_repos` shows all active channel-repo bindings. Empty until /link-repo is used.
- **Failure state**: When GitHub env vars are missing, Config fields are None. Downstream code (T02/T03) should check these fields and log/respond with a clear "GitHub not configured" message. No crash — graceful degradation.
