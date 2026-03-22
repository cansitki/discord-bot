# S03: Claude AI Integration

**Goal:** Wire Claude Sonnet into the Discord bot so users can have natural, contextual conversations by mentioning the bot or messaging in a designated AI channel.
**Demo:** User sends a message mentioning the bot → bot shows typing indicator → Claude responds with an intelligent answer. User sets an AI channel with `/ai-channel` → all messages in that channel get Claude responses.

## Must-Haves

- `ClaudeClient` wrapper in `bot/claude.py` that takes a string message, calls Anthropic API, returns a string response
- Tool-use loop in `ClaudeClient.ask()` — accepts optional tools list and executor callback, cycles until Claude returns a final text response
- AI cog in `bot/cogs/ai.py` with `on_message` listener that routes to Claude when bot is mentioned or message is in the guild's AI channel
- `/ai-channel` hybrid command to set the designated AI channel per guild
- Typing indicator shown during Claude API call
- Response splitting for messages >2000 characters (Discord limit)
- Bot ignores its own messages in the AI cog listener (guard against self-reply loops)
- `ai_channel_id` column added to `guild_config` via migration
- `CLAUDE_MODEL` env var with sensible default for model selection
- Error handling: API errors, empty responses, and timeouts produce user-visible error messages (not silent failures)

## Proof Level

- This slice proves: contract
- Real runtime required: no (mocked Anthropic client)
- Human/UAT required: no (deferred to S06)

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_claude.py tests/test_ai_cog.py -v` — all tests pass
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/ -q` — all existing tests still pass (no regressions)
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.claude import ClaudeClient; print('import ok')"` — module imports cleanly
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.cogs.ai import AICog; print('import ok')"` — cog imports cleanly
- At least one test verifies the tool-use loop cycles correctly (mock tool_use stop_reason → tool execution → final text response)
- At least one test verifies error handling produces a user-facing error message, not a crash
- At least one test verifies response chunking splits correctly at >2000 chars

## Observability / Diagnostics

- Runtime signals: Structured logging via `logging.getLogger(__name__)` in `claude.py` and `ai.py` — log message routing decisions (mention vs AI channel vs ignored), Claude API call start/end, tool-use loop iterations, errors
- Inspection surfaces: Log output searchable via `grep "claude\|ai_cog"` in bot logs
- Failure visibility: Claude API errors logged with model name, stop_reason, and error type; user sees a Discord error message with the failure class (API error, timeout, empty response)
- Redaction constraints: Never log message content or API keys; log only routing metadata (guild_id, channel_id, user_id, is_mention, is_ai_channel)

## Integration Closure

- Upstream surfaces consumed: `bot/bot.py` (Bot instance, cog loading, `on_message`), `bot/config.py` (Config with `anthropic_api_key`), `bot/database.py` (DatabaseManager for guild config queries), `bot/models.py` (GuildConfig dataclass), `migrations/001_initial.sql` (existing schema)
- New wiring introduced in this slice: `bot.cogs.ai` extension loaded in `setup_hook`, `bot/claude.py` module created, `migrations/002_ai_channel.sql` migration added, `CLAUDE_MODEL` env var added to Config
- What remains before the milestone is truly usable end-to-end: S04 (server design tools), S05 (general assistant), S06 (Railway deployment + live integration test)

## Tasks

- [x] **T01: Build ClaudeClient wrapper with tool-use loop and tests** `est:1h`
  - Why: The Claude client is the foundation for all AI features — S03 cog, S04 server design tools, S05 general assistant all depend on it. Also adds the `ai_channel_id` migration and model update since T02 needs both.
  - Files: `bot/claude.py`, `tests/test_claude.py`, `bot/config.py`, `bot/models.py`, `migrations/002_ai_channel.sql`
  - Do: Create `ClaudeClient` class wrapping `AsyncAnthropic` with `ask(message, tools, tool_executor)` method. Implement the manual tool-use loop (check `stop_reason == "tool_use"`, extract tool calls, call executor, send results back, repeat). Handle errors (API errors → error message string, empty response → fallback text). Add `CLAUDE_MODEL` env var to `Config` with default `claude-sonnet-4-20250514`. Add `ai_channel_id` to `GuildConfig.from_row()`. Write migration `002_ai_channel.sql`. Write comprehensive tests with mocked `AsyncAnthropic`.
  - Verify: `.venv/bin/python -m pytest tests/test_claude.py -v` — all pass; `.venv/bin/python -c "from bot.claude import ClaudeClient; print('ok')"`
  - Done when: `ClaudeClient.ask()` handles simple query, tool-use cycle, API error, and empty response — all proven by passing tests

- [x] **T02: Build AI cog with message routing, response chunking, and bot wiring** `est:1h`
  - Why: Connects Claude to Discord — routes messages to the Claude client and delivers responses back to users. This is the user-facing feature that makes the demo true.
  - Files: `bot/cogs/ai.py`, `tests/test_ai_cog.py`, `bot/bot.py`
  - Do: Create `AICog` with `on_message` cog listener that routes to Claude when bot is mentioned OR message is in the guild's AI channel. Show `channel.typing()` during API call. Split responses >2000 chars on paragraph boundaries, then newlines, then hard-cut. Add `/ai-channel` hybrid command (admin-only) to set AI channel in guild_config. Wire cog loading into `bot.py` `setup_hook`. Guard against bot self-replies. Write tests for routing logic, chunking, error display, and the /ai-channel command.
  - Verify: `.venv/bin/python -m pytest tests/test_ai_cog.py -v` — all pass; `.venv/bin/python -m pytest tests/ -q` — no regressions
  - Done when: AI cog routes mentions and AI-channel messages to Claude, chunks long responses correctly, displays errors gracefully — all proven by passing tests, and `bot.py` loads the cog

## Files Likely Touched

- `bot/claude.py` (new)
- `bot/cogs/ai.py` (new)
- `bot/config.py` (modify — add CLAUDE_MODEL)
- `bot/models.py` (modify — add ai_channel_id)
- `bot/bot.py` (modify — load ai cog)
- `migrations/002_ai_channel.sql` (new)
- `tests/test_claude.py` (new)
- `tests/test_ai_cog.py` (new)
