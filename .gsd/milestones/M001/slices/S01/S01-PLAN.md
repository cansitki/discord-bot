# S01: Bot Core & Database Foundation

**Goal:** Bot is online in Discord, responds to `/ping`, SQLite database initialized with migration-managed schema, hybrid command framework operational, action logging infrastructure ready for downstream slices.
**Demo:** Run `python -m bot` → bot connects to Discord Gateway, responds to `/ping` with latency, SQLite database file exists with `guild_config`, `action_log`, and `_migrations` tables populated.

## Must-Haves

- Config module loads and validates `DISCORD_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_PATH`, `COMMAND_PREFIX` from environment
- DatabaseManager wraps aiosqlite with `connect()`, `close()`, `execute()`, `fetchone()`, `fetchall()`, `run_migrations()`
- SQLite WAL mode enabled, migrations tracked in `_migrations` table, applied sequentially on startup
- `guild_config` table (guild_id, prefix, verify_channel_id, verify_role_id, log_channel_id) and `action_log` table (id, guild_id, user_id, action_type, target, details, timestamp) created via migration
- GuildConfig and ActionLog dataclasses for row-to-object mapping
- Bot subclass with `setup_hook` that connects database, runs migrations, loads cogs
- `/ping` hybrid command in a cog that responds with latency
- `on_ready` logs bot name and guild count (guarded against re-fires)
- `on_message` calls `process_commands` (preserves prefix/hybrid command handling for S03)
- `Bot.close()` cleanly closes database connection
- Entry point `python -m bot` starts the bot

## Proof Level

- This slice proves: contract (database layer) + integration (bot connects and responds)
- Real runtime required: yes (Discord Gateway for full integration; database and config testable offline)
- Human/UAT required: no (automated tests cover contract; manual `/ping` confirms integration)

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -m pytest tests/ -v` — all tests pass
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.config import Config"` — config module importable
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.database import DatabaseManager"` — database module importable
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.bot import DiscordBot"` — bot module importable
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.cogs.ping import PingCog"` — cog importable
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.config import Config; Config.from_env()"` — raises ValueError naming missing DISCORD_BOT_TOKEN when env var absent (failure-path check)
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && python -c "from bot.database import DatabaseManager; import asyncio; db = DatabaseManager(':memory:'); asyncio.run(db.connect()); asyncio.run(db.run_migrations('migrations')); asyncio.run(db.close())"` — migrations run against in-memory DB without error (diagnostic surface check)

## Observability / Diagnostics

- Runtime signals: `on_ready` prints bot username + guild count; migration runner logs each applied migration name; config loader prints loaded var names (never values)
- Inspection surfaces: `_migrations` table shows applied migrations; `action_log` table queryable for audit trail
- Failure visibility: Config validation raises `ValueError` with missing var name; migration failures halt startup with the failing migration file name; database connection errors surface in `setup_hook`
- Redaction constraints: `DISCORD_BOT_TOKEN` and `ANTHROPIC_API_KEY` never logged or printed

## Integration Closure

- Upstream surfaces consumed: none (first slice)
- New wiring introduced in this slice: `bot.db` attribute (DatabaseManager attached to Bot instance), cog loading from `bot/cogs/`, migration runner reading `migrations/*.sql`
- What remains before the milestone is truly usable end-to-end: S02 (verification gate), S03 (Claude integration), S04 (server design), S05 (general assistant), S06 (Railway deployment)

## Tasks

- [x] **T01: Config, database layer, models, migrations, and unit tests** `est:1h`
  - Why: Everything downstream (bot, cogs, Claude, verification) depends on config loading, database access, and the schema. This task builds and fully tests the data layer without needing Discord.
  - Files: `pyproject.toml`, `bot/__init__.py`, `bot/config.py`, `bot/database.py`, `bot/models.py`, `migrations/001_initial.sql`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_database.py`, `tests/test_models.py`
  - Do: Create `pyproject.toml` with discord.py, anthropic, aiosqlite, python-dotenv dependencies plus pytest/pytest-asyncio dev deps. Implement `Config` dataclass that reads env vars with defaults and fails fast on missing required vars. Implement `DatabaseManager` class with aiosqlite connection, WAL mode, row factory, migration runner that tracks applied migrations in `_migrations` table. Define `GuildConfig` and `ActionLog` dataclasses with `from_row()` classmethods. Write `001_initial.sql` creating all three tables. Write pytest tests covering: config loading with/without env vars, database CRUD, migration idempotency, model construction from rows.
  - Verify: `python -m pytest tests/test_config.py tests/test_database.py tests/test_models.py -v` — all pass
  - Done when: Config validates env vars and fails on missing required ones; DatabaseManager creates tables via migration; models round-trip through database; all tests green

- [x] **T02: Bot skeleton, entry point, ping cog, and integration tests** `est:1h`
  - Why: Proves the bot framework works end-to-end: starts up, connects database, loads cogs, handles commands. This is the slice's demo — `/ping` responding means the hybrid command framework is operational.
  - Files: `bot/bot.py`, `bot/__main__.py`, `bot/cogs/__init__.py`, `bot/cogs/ping.py`, `.env.example`, `tests/test_bot.py`, `tests/test_ping.py`
  - Do: Implement `DiscordBot(commands.Bot)` subclass with intents (guilds, members, message_content, guild_messages), `setup_hook` that connects database/runs migrations/loads cogs, `on_ready` with re-fire guard, `on_message` calling `process_commands`, `close()` for clean shutdown. Create `PingCog` with `@commands.hybrid_command` that returns latency. Create `__main__.py` entry point that loads config and runs bot. Create `.env.example` with all env var names. Write tests that mock discord.py internals to verify setup_hook behavior, cog loading, command registration, and the on_message → process_commands chain.
  - Verify: `python -m pytest tests/test_bot.py tests/test_ping.py -v` — all pass; `python -c "from bot.bot import DiscordBot; from bot.cogs.ping import PingCog"` succeeds
  - Done when: Bot class instantiable with mocked token; setup_hook wires database and loads ping cog; PingCog registered as hybrid command; entry point importable; all tests green

## Files Likely Touched

- `pyproject.toml`
- `bot/__init__.py`
- `bot/__main__.py`
- `bot/config.py`
- `bot/database.py`
- `bot/models.py`
- `bot/bot.py`
- `bot/cogs/__init__.py`
- `bot/cogs/ping.py`
- `migrations/001_initial.sql`
- `.env.example`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_config.py`
- `tests/test_database.py`
- `tests/test_models.py`
- `tests/test_bot.py`
- `tests/test_ping.py`
