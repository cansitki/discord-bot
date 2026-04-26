---
estimated_steps: 5
estimated_files: 11
skills_used:
  - test
  - lint
---

# T01: Config, database layer, models, migrations, and unit tests

**Slice:** S01 — Bot Core & Database Foundation
**Milestone:** M001

## Description

Build the entire data foundation that every downstream slice depends on: environment config loading with validation, async SQLite database manager with migration support, data models for guild configuration and action logging, the initial schema migration, and comprehensive pytest tests for all of it. This task produces fully tested, Discord-independent infrastructure.

## Steps

1. **Create `pyproject.toml`** with project metadata and dependencies:
   - Runtime: `discord.py>=2.3`, `anthropic>=0.40`, `aiosqlite>=0.20`, `python-dotenv>=1.0`
   - Dev: `pytest>=8.0`, `pytest-asyncio>=0.24`
   - Set `requires-python = ">=3.12"`, add `[project.scripts]` or note `python -m bot` entry
   - Install with `pip install -e ".[dev]"` or `pip install -e . && pip install pytest pytest-asyncio`

2. **Implement `bot/config.py`** — A `Config` dataclass that:
   - Calls `dotenv.load_dotenv()` to read `.env` in development
   - Reads `DISCORD_BOT_TOKEN` (required), `ANTHROPIC_API_KEY` (required), `DATABASE_PATH` (default `./data/bot.db`), `COMMAND_PREFIX` (default `!`)
   - Raises `ValueError` naming the missing variable if a required var is absent
   - Never logs or prints secret values
   - Provides a `Config.from_env()` classmethod

3. **Implement `bot/database.py`** — A `DatabaseManager` class that:
   - `connect()`: opens aiosqlite connection, enables WAL mode (`PRAGMA journal_mode=WAL`), sets `row_factory = aiosqlite.Row`
   - `close()`: closes the connection cleanly
   - `execute(sql, params)`: runs a write query, returns cursor
   - `fetchone(sql, params)`: runs a read query, returns single row or None
   - `fetchall(sql, params)`: runs a read query, returns list of rows
   - `run_migrations(migrations_dir)`: reads `migrations/*.sql` files sorted by name, checks `_migrations` table for already-applied ones, applies new ones in a transaction, records them in `_migrations`. Creates `_migrations` table if it doesn't exist. Logs each applied migration name. If a migration fails, raises with the filename so startup halts clearly.
   - Ensures `data/` directory exists before connecting (creates parent dirs for DATABASE_PATH)

4. **Implement `bot/models.py`** — Dataclasses:
   - `GuildConfig`: `guild_id: int`, `prefix: str`, `verify_channel_id: int | None`, `verify_role_id: int | None`, `log_channel_id: int | None`. Classmethod `from_row(row)` that constructs from an aiosqlite.Row.
   - `ActionLog`: `id: int | None`, `guild_id: int`, `user_id: int`, `action_type: str`, `target: str`, `details: str | None`, `timestamp: str | None` (ISO format, defaults to now). Classmethod `from_row(row)`.
   - Write `migrations/001_initial.sql` that creates `_migrations(name TEXT PRIMARY KEY, applied_at TEXT)`, `guild_config(guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT '!', verify_channel_id INTEGER, verify_role_id INTEGER, log_channel_id INTEGER)`, `action_log(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, action_type TEXT NOT NULL, target TEXT NOT NULL, details TEXT, timestamp TEXT NOT NULL DEFAULT (datetime('now')))`.

5. **Write tests** — `tests/conftest.py` with shared fixtures (tmp database path, mock env vars). Then:
   - `tests/test_config.py`: Config loads from env vars; raises on missing DISCORD_BOT_TOKEN; raises on missing ANTHROPIC_API_KEY; uses defaults for DATABASE_PATH and COMMAND_PREFIX.
   - `tests/test_database.py`: DatabaseManager connects to in-memory SQLite; `run_migrations` creates tables; `run_migrations` is idempotent (running twice doesn't error); `execute`/`fetchone`/`fetchall` work for insert/select; WAL mode is active.
   - `tests/test_models.py`: GuildConfig constructs from dict-like row; ActionLog constructs from dict-like row; default values work correctly.
   - Create `bot/__init__.py` and `tests/__init__.py` as empty files for package discovery.

## Must-Haves

- [ ] `pyproject.toml` exists with all runtime and dev dependencies; `pip install -e ".[dev]"` succeeds
- [ ] `Config.from_env()` raises `ValueError` naming the missing variable when required env vars are absent
- [ ] `Config.from_env()` returns a populated Config when all required env vars are set
- [ ] `DatabaseManager.connect()` opens aiosqlite connection with WAL mode
- [ ] `DatabaseManager.run_migrations()` applies `001_initial.sql` and creates `_migrations`, `guild_config`, `action_log` tables
- [ ] `DatabaseManager.run_migrations()` is idempotent — running twice does not error or re-apply
- [ ] `GuildConfig.from_row()` and `ActionLog.from_row()` construct from database row objects
- [ ] All tests pass: `python -m pytest tests/test_config.py tests/test_database.py tests/test_models.py -v`

## Verification

- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M001 && pip install -e ".[dev]" && python -m pytest tests/test_config.py tests/test_database.py tests/test_models.py -v` — all tests pass
- `python -c "from bot.config import Config; from bot.database import DatabaseManager; from bot.models import GuildConfig, ActionLog"` — all imports succeed

## Inputs

- `migrations/001_initial.sql` — created by this task (no prior inputs; first task in slice)

## Observability Impact

- **Config validation**: `Config.from_env()` raises `ValueError("Missing required environment variable: <VAR_NAME>")` — future agents can grep for this pattern to diagnose startup failures
- **Migration logging**: `DatabaseManager.run_migrations()` prints `Applying migration: <filename>` for each applied migration — visible in stdout during bot startup
- **Migration table**: `_migrations` table stores `(name, applied_at)` rows — queryable to inspect which migrations have been applied and when
- **Database path**: `DatabaseManager` creates parent directories for `DATABASE_PATH` — if the path is wrong, the error surfaces at `connect()` time with the full path
- **Failure signals**: Missing env vars → `ValueError` at config load; bad SQL in migration → exception with migration filename; connection failure → `aiosqlite` error at `connect()`

## Expected Output

- `pyproject.toml` — project metadata and dependency declarations
- `bot/__init__.py` — empty package init
- `bot/config.py` — Config dataclass with env var loading and validation
- `bot/database.py` — DatabaseManager class with aiosqlite, WAL mode, migration runner
- `bot/models.py` — GuildConfig and ActionLog dataclasses with from_row()
- `migrations/001_initial.sql` — initial schema creating _migrations, guild_config, action_log tables
- `tests/__init__.py` — empty test package init
- `tests/conftest.py` — shared pytest fixtures
- `tests/test_config.py` — config loading tests
- `tests/test_database.py` — database manager and migration tests
- `tests/test_models.py` — model construction tests
