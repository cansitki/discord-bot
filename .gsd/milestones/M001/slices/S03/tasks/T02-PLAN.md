---
estimated_steps: 4
estimated_files: 4
skills_used:
  - test
---

# T02: Build AI cog with message routing, response chunking, and bot wiring

**Slice:** S03 — Claude AI Integration
**Milestone:** M001

## Description

Create the `AICog` in `bot/cogs/ai.py` — the Discord-facing integration that routes messages to Claude and delivers responses. The cog listens for `on_message` events and routes to Claude when the bot is mentioned or the message is in the guild's configured AI channel. It handles the Discord-specific concerns: typing indicator, response chunking for the 2000-char limit, error display, and the `/ai-channel` admin command.

Wire the cog into `bot/bot.py`'s `setup_hook` so it loads automatically.

## Steps

1. **Create `bot/cogs/ai.py`** with `AICog(commands.Cog)`:
   - Constructor takes `bot: DiscordBot`, creates a `ClaudeClient` instance using `bot.config.anthropic_api_key` and `bot.config.claude_model`
   - `@commands.Cog.listener() async def on_message(self, message)`:
     - Guard: return early if `message.author == self.bot.user` or `message.author.bot`
     - Guard: return early if no `message.guild` (DMs out of scope)
     - Determine if message is bot-directed:
       - Check if bot is mentioned in `message.mentions`
       - Check if `message.channel.id` matches the guild's `ai_channel_id` from guild_config
     - If neither, return early (ignore the message)
     - Strip the bot mention from message content if present (so Claude doesn't see `<@bot_id>`)
     - `async with message.channel.typing():` — show typing indicator
       - Call `self.claude_client.ask(clean_content)`
       - Send response using the chunking helper
     - On any exception: send a brief error message to the channel, log the full traceback
   - `def _chunk_response(self, text: str) -> list[str]`:
     - If len(text) <= 2000, return [text]
     - Split on `\n\n` boundaries first, accumulating chunks up to 2000 chars
     - If a single paragraph exceeds 2000, split on `\n` boundaries
     - If a single line exceeds 2000, hard-split at 2000 chars (never mid-word if possible, but hard-cut as fallback)
     - Return list of chunks, each ≤ 2000 chars
   - `/ai-channel` hybrid command:
     - `@commands.hybrid_command(name="ai-channel", description="Set the AI response channel")`
     - `@commands.has_permissions(manage_channels=True)` — admin-only
     - Takes optional `channel: discord.TextChannel = None` parameter
     - If channel provided: UPDATE guild_config SET ai_channel_id = channel.id WHERE guild_id = ctx.guild.id (INSERT if not exists using INSERT OR REPLACE pattern)
     - If no channel: clear ai_channel_id (set to NULL)
     - Confirm with a message
   - `async def setup(bot)` module-level function for `load_extension`

2. **Implement guild_config query helper** — The AI cog needs to look up `ai_channel_id` for a guild. Add a method or use `bot.db.fetchone` directly with `SELECT ai_channel_id FROM guild_config WHERE guild_id = ?`. Keep it simple — no new abstraction layer.

3. **Wire cog into `bot/bot.py`** — Add `await self.load_extension("bot.cogs.ai")` in `setup_hook` after the existing cog loads. Update the log message. The `on_message` in `bot.py` stays as-is — discord.py dispatches `on_message` to all cog listeners AND the bot's own `on_message` independently. The cog listener handles Claude routing; the bot's `on_message` handles `process_commands`. No conflict.

4. **Create `tests/test_ai_cog.py`** with tests:
   - **Routing tests:**
     - Message mentioning bot → Claude is called
     - Message in AI channel → Claude is called
     - Message not mentioning bot and not in AI channel → Claude NOT called
     - Bot's own message → ignored
     - Other bot's message → ignored
     - DM message (no guild) → ignored
   - **Response chunking tests:**
     - Short response (≤2000) → single message sent
     - Long response (>2000) → multiple messages sent, each ≤2000 chars
     - Split prefers paragraph boundaries over arbitrary cuts
     - Single paragraph >2000 chars splits on newlines
     - Single line >2000 chars hard-splits at 2000
   - **Error handling tests:**
     - Claude client raises exception → error message sent to channel, no crash
   - **/ai-channel command tests:**
     - Setting a channel updates guild_config
     - Clearing the channel sets ai_channel_id to NULL

   Mock the `ClaudeClient` (don't call real API). Mock `bot.db` for guild_config queries. Use the existing test patterns from `tests/test_bot.py` for mocking bot internals.

## Must-Haves

- [ ] Bot-mention messages routed to Claude
- [ ] AI-channel messages routed to Claude
- [ ] Non-directed messages ignored (no spurious Claude calls)
- [ ] Bot self-messages ignored
- [ ] Typing indicator shown during Claude call
- [ ] Responses >2000 chars split correctly
- [ ] `/ai-channel` command sets/clears the AI channel per guild
- [ ] Cog loaded in `bot.py` `setup_hook`
- [ ] All tests pass with no regressions

## Verification

- `.venv/bin/python -m pytest tests/test_ai_cog.py -v` — all tests pass
- `.venv/bin/python -m pytest tests/ -q` — all tests pass, no regressions
- `.venv/bin/python -c "from bot.cogs.ai import AICog; print('import ok')"` — imports cleanly
- `grep -q 'bot.cogs.ai' bot/bot.py` — cog is wired in setup_hook

## Inputs

- `bot/claude.py` — ClaudeClient class from T01
- `bot/config.py` — Config with `claude_model` and `anthropic_api_key` from T01
- `bot/models.py` — GuildConfig with `ai_channel_id` from T01
- `bot/bot.py` — existing bot with setup_hook and on_message
- `bot/database.py` — DatabaseManager for guild_config queries
- `tests/conftest.py` — existing test fixtures

## Expected Output

- `bot/cogs/ai.py` — new AI cog module
- `tests/test_ai_cog.py` — new test file for AI cog
- `bot/bot.py` — modified to load AI cog

## Observability Impact

- **New logger:** `bot.cogs.ai` — logs message routing decisions (guild_id, channel_id, user_id, is_mention, is_ai_channel), response delivery stats (chunk count, total length), and errors with full tracebacks
- **Routing visibility:** Every routed message logs whether it was a mention or AI channel match, allowing `grep "ai_cog.route"` to trace message routing
- **Response delivery:** `grep "ai_cog.response"` shows chunk count and total response length per delivered message
- **Error visibility:** Exceptions during Claude calls log full tracebacks under `ai_cog.error` and send a user-facing error message to the channel
- **Command audit:** `/ai-channel` set/clear operations log guild_id and channel_id under `ai_cog.ai_channel`
- **Redaction:** Message content is never logged — only metadata (guild_id, channel_id, user_id, boolean flags)
