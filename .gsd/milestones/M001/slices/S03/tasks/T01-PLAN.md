---
estimated_steps: 5
estimated_files: 6
skills_used:
  - test
---

# T01: Build ClaudeClient wrapper with tool-use loop and tests

**Slice:** S03 — Claude AI Integration
**Milestone:** M001

## Description

Create the `ClaudeClient` class in `bot/claude.py` — a thin async wrapper around the Anthropic SDK that owns the `AsyncAnthropic` instance, system prompt, and the message-to-response flow. The key method is `ask(message, tools, tool_executor)` which sends a user message to Claude and returns the final text response. When tools are provided and Claude requests tool use, the method runs a manual loop: extract tool calls, invoke the executor callback, send results back, repeat until Claude returns a final text response.

Also adds the `ai_channel_id` column to `guild_config` via a new migration, updates the `GuildConfig` model, and adds `CLAUDE_MODEL` to the Config dataclass. These are bundled here because they're tiny and T02 needs them.

## Steps

1. **Add `CLAUDE_MODEL` to `bot/config.py`** — Add `claude_model: str` field to the `Config` dataclass. Load from `os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")` in `from_env()`. No validation beyond non-empty — just a string passed to the SDK.

2. **Create `bot/claude.py`** with `ClaudeClient` class:
   - Constructor takes `api_key: str`, `model: str`, optional `system_prompt: str` (default: a brief bot personality prompt)
   - Creates `anthropic.AsyncAnthropic(api_key=api_key)` stored as `self._client`
   - `async def ask(self, message: str, tools: list[dict] | None = None, tool_executor: Callable[[str, dict], Awaitable[str]] | None = None) -> str`
   - Implementation:
     - Build messages list: `[{"role": "user", "content": message}]`
     - Call `self._client.messages.create(model=self.model, max_tokens=1024, system=self.system_prompt, messages=messages, tools=tools or [])`
     - If `response.stop_reason == "tool_use"`: extract `ToolUseBlock` items from `response.content`, call `tool_executor(tool.name, tool.input)` for each, append assistant response + tool results to messages, loop (max 10 iterations to prevent infinite loops)
     - If `response.stop_reason == "end_turn"`: extract text from `TextBlock` items in `response.content`, join with newlines, return
     - Handle empty text (Claude only used tools, no text): return a fallback like "(No text response)"
   - Error handling: catch `anthropic.APIError` and `anthropic.APIConnectionError`, log the error, return a user-friendly error string like "Sorry, I couldn't process that request. (API error)"
   - Logging: log API call start (model, message length), tool-use iterations, errors. Never log message content or API keys.

3. **Create `migrations/002_ai_channel.sql`**:
   ```sql
   ALTER TABLE guild_config ADD COLUMN ai_channel_id INTEGER;
   ```

4. **Update `bot/models.py`** — Add `ai_channel_id: int | None = None` to `GuildConfig` dataclass. Update `from_row()` to read it from the row.

5. **Create `tests/test_claude.py`** with comprehensive tests:
   - Test simple ask: mock `AsyncAnthropic.messages.create` to return a `Message` with a `TextBlock`, verify `ask()` returns the text
   - Test tool-use loop: mock first response with `stop_reason="tool_use"` and a `ToolUseBlock`, mock second response with `stop_reason="end_turn"` and a `TextBlock`. Provide a mock `tool_executor`. Verify the executor was called with the right name/input, and the final text is returned.
   - Test multiple tool calls in one response: mock a response with two `ToolUseBlock` items — verify both are executed
   - Test API error handling: mock `create` to raise `anthropic.APIError`, verify `ask()` returns an error string (not raises)
   - Test connection error handling: mock `create` to raise `anthropic.APIConnectionError`, verify error string returned
   - Test empty text response: mock response with only `ToolUseBlock` and no `TextBlock` after tool loop resolves, verify fallback text
   - Test max iterations guard: mock tool-use responses that never resolve — verify loop stops after max iterations
   - Test Config now has `claude_model` field with default
   - Test `GuildConfig.from_row()` handles `ai_channel_id`

   Use `unittest.mock.AsyncMock` and `unittest.mock.patch` to mock the Anthropic client. Construct mock `Message` objects using the actual Anthropic types (`TextBlock`, `ToolUseBlock`, `Message`) with `model_construct()` to avoid validation.

## Must-Haves

- [ ] `ClaudeClient.ask()` returns text for a simple query
- [ ] Tool-use loop cycles correctly: tool_use → execute → send results → final text
- [ ] API errors caught and returned as user-friendly error strings (no unhandled exceptions)
- [ ] Empty response handled with fallback text
- [ ] Max iteration guard prevents infinite tool-use loops
- [ ] `CLAUDE_MODEL` env var loaded in Config with default `claude-sonnet-4-20250514`
- [ ] `ai_channel_id` column added via migration, `GuildConfig` updated
- [ ] All tests pass

## Verification

- `.venv/bin/python -m pytest tests/test_claude.py -v` — all tests pass
- `.venv/bin/python -c "from bot.claude import ClaudeClient; print('import ok')"` — imports cleanly
- `.venv/bin/python -m pytest tests/ -q` — no regressions in existing tests

## Inputs

- `bot/config.py` — existing Config dataclass to extend with `claude_model`
- `bot/models.py` — existing GuildConfig to extend with `ai_channel_id`
- `migrations/001_initial.sql` — existing schema that the new migration builds on
- `tests/conftest.py` — existing test fixtures (mock_env_full, db_manager, etc.)

## Expected Output

- `bot/claude.py` — new ClaudeClient wrapper module
- `bot/config.py` — modified with `claude_model` field
- `bot/models.py` — modified with `ai_channel_id` field
- `migrations/002_ai_channel.sql` — new migration file
- `tests/test_claude.py` — new test file with comprehensive Claude client tests

## Observability Impact

- **New signals:** `bot.claude` logger emits structured logs for API call start (model, message length, tool count), tool-use loop iterations, max-iteration warnings, and API error details (error type, status code). Message content and API keys are never logged.
- **Inspection:** `grep "claude" bot.log` surfaces all Claude client activity — call flow, tool loops, and errors.
- **Failure visibility:** API errors and connection errors are caught, logged with context (model, error type), and returned as user-friendly strings. Callers (the AI cog in T02) can display these directly to users.
- **What a future agent checks:** Run `pytest tests/test_claude.py -v` to verify all Claude client behaviors. Import check via `from bot.claude import ClaudeClient` confirms module integrity.
