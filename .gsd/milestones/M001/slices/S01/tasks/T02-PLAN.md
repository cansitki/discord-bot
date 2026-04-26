---
estimated_steps: 5
estimated_files: 7
skills_used:
  - test
  - lint
---

# T02: Bot skeleton, entry point, ping cog, and integration tests

**Slice:** S01 — Bot Core & Database Foundation
**Milestone:** M001

## Description

Build the discord.py Bot subclass with async initialization, the `/ping` hybrid command cog, and the `__main__.py` entry point. This task wires together the config and database layers from T01 into a running bot that can connect to Discord, respond to commands, and shut down cleanly. Tests mock discord.py internals to verify the wiring without requiring a real Discord connection.

## Steps

1. **Implement `bot/bot.py`** — `DiscordBot(commands.Bot)` subclass:
   - Constructor takes a `Config` instance, stores it. Sets `command_prefix` from config, sets up intents: `guilds=True, members=True, message_content=True, guild_messages=True` (note: members and message_content are privileged — must be enabled in Discord Developer Portal).
   - `setup_hook(self)`: Creates `DatabaseManager(self.config.database_path)`, calls `await self.db.connect()`, calls `await self.db.run_migrations("migrations")`, loads cogs from `bot/cogs/` directory using `await self.load_extension("bot.cogs.ping")`, syncs command tree (guild-specific for dev, global for production — use an env var `DEV_GUILD_ID` if set).
   - `on_ready(self)`: Guarded with `self._ready = True` flag to prevent re-fires on reconnect. Logs bot username, discriminator, guild count, database status.
   - `on_message(self, message)`: Ignores messages from self (`if message.author == self.user: return`), then calls `await self.process_commands(message)`. This preserves prefix/hybrid command handling and leaves a hook point for S03's Claude integration.
   - `close(self)`: Calls `await self.db.close()` then `await super().close()` for clean shutdown.

2. **Implement `bot/cogs/ping.py`** — `PingCog(commands.Cog)`:
   - `@commands.hybrid_command(name="ping", description="Check bot latency")` that responds with the bot's WebSocket latency in milliseconds: `f"Pong! {round(self.bot.latency * 1000)}ms"`.
   - `async def setup(bot)` function at module level for cog loading via `load_extension`.
   - Create `bot/cogs/__init__.py` as empty file.

3. **Implement `bot/__main__.py`** — Entry point for `python -m bot`:
   - Imports `Config.from_env()`, creates `DiscordBot(config)`, calls `bot.run(config.discord_bot_token)`.
   - Wraps in `try/except` for clean error messaging on startup failures (missing token, database errors).
   - Uses `logging.basicConfig` to set up structured logging (INFO level by default).

4. **Create `.env.example`** — Template with all env var names and descriptions:
   - `DISCORD_BOT_TOKEN=` (required)
   - `ANTHROPIC_API_KEY=` (required)
   - `DATABASE_PATH=./data/bot.db` (optional, default shown)
   - `COMMAND_PREFIX=!` (optional, default shown)
   - `DEV_GUILD_ID=` (optional, for instant slash command sync during development)

5. **Write integration tests** — Tests that verify the wiring without a real Discord connection:
   - `tests/test_bot.py`:
     - Test that `DiscordBot` instantiates with a mock config and correct intents
     - Test that `setup_hook` calls database connect, run_migrations, and loads the ping cog (mock the database and extension loading)
     - Test that `on_ready` sets the `_ready` flag and doesn't re-fire
     - Test that `on_message` ignores bot's own messages and calls `process_commands` for others
     - Test that `close()` closes the database connection
   - `tests/test_ping.py`:
     - Test that `PingCog` has a `ping` command registered
     - Test that the ping command produces a response containing "Pong!" and a latency value
     - Use `discord.ext.commands` test utilities or mock the interaction context

## Must-Haves

- [ ] `DiscordBot` subclass configures correct intents (guilds, members, message_content, guild_messages)
- [ ] `setup_hook` connects database, runs migrations, loads ping cog
- [ ] `on_ready` is guarded against re-fires on reconnect
- [ ] `on_message` calls `process_commands` (does not swallow prefix/hybrid commands)
- [ ] `on_message` ignores messages from the bot itself
- [ ] `close()` cleanly closes database connection before calling super().close()
- [ ] `/ping` hybrid command responds with latency
- [ ] `python -m bot` is a valid entry point (requires env vars to actually connect)
- [ ] `.env.example` documents all env vars
- [ ] All tests pass: `python -m pytest tests/test_bot.py tests/test_ping.py -v`

## Verification

- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M001 && python -m pytest tests/ -v` — all tests pass (T01 + T02 tests)
- `python -c "from bot.bot import DiscordBot; from bot.cogs.ping import PingCog"` — imports succeed
- `test -f .env.example` — env example file exists

## Inputs

- `bot/config.py` — Config dataclass from T01
- `bot/database.py` — DatabaseManager class from T01
- `bot/models.py` — GuildConfig and ActionLog dataclasses from T01
- `migrations/001_initial.sql` — initial schema from T01
- `pyproject.toml` — project dependencies from T01
- `tests/conftest.py` — shared test fixtures from T01

## Expected Output

- `bot/bot.py` — DiscordBot subclass with setup_hook, on_ready, on_message, close
- `bot/__main__.py` — entry point for python -m bot
- `bot/cogs/__init__.py` — empty cogs package init
- `bot/cogs/ping.py` — PingCog with /ping hybrid command
- `.env.example` — environment variable template
- `tests/test_bot.py` — bot skeleton integration tests
- `tests/test_ping.py` — ping cog tests

## Observability Impact

- **on_ready** prints `Bot online: <user> (guilds: <count>, db: <status>)` on first connection — confirms bot identity, guild reach, and database health at a glance
- **setup_hook** logs database connection, each loaded cog name, and command tree sync target (global vs dev guild) via `logging.info`
- **close()** logs database closure during shutdown, confirming clean teardown
- **Entry point** sets up structured logging at INFO level; config/database errors are caught and logged before `sys.exit(1)` — no silent crashes
- **Reconnect guard**: `_on_ready_fired` flag prevents duplicate on_ready output on Discord reconnects, keeping logs clean
- **Inspection**: Check `bot._on_ready_fired` to verify whether the bot has completed initial ready; `bot.db` is `None` until setup_hook runs, then holds the active DatabaseManager
- **Failure visibility**: Missing env vars → `ValueError` with var name at startup; database errors → logged and exit code 1; cog load failures → exception in setup_hook halts startup

