---
estimated_steps: 5
estimated_files: 6
skills_used:
  - test
  - review
---

# T02: Wire server design into AICog and bot, add comprehensive tests

**Slice:** S04 — Conversational Server Design
**Milestone:** M001

## Description

Connect the ServerDesignCog (built in T01) to the rest of the bot. Modify AICog to pass server-design tools and a tool executor to `ClaudeClient.ask()` so Claude can propose designs when users request them. Register the cog and its DynamicItem buttons in `bot.py`. Write comprehensive tests covering the full propose→approve→build flow, including a 10+ channel design test that proves the roadmap's rate-limit risk is retired.

The key integration pattern: AICog always passes the server-design tool to Claude. Claude decides whether to use it based on the message content. When Claude calls the tool, the executor in ServerDesignCog stores the proposal and sends the embed. The AICog then sends Claude's accompanying text response (e.g., "I've prepared a server design for you — please review and approve it above!").

## Steps

1. **Modify `bot/bot.py`**: In `setup_hook()`, add `await self.load_extension("bot.cogs.server_design")`. Import `DesignApproveButton` and `DesignCancelButton` from `bot.cogs.server_design` and add them to `self.add_dynamic_items()` call alongside the verification buttons.

2. **Modify `bot/cogs/ai.py`**: In `on_message()`, after cleaning content, look up the `ServerDesignCog` via `self.bot.get_cog("ServerDesignCog")`. If the cog is loaded, get the `PROPOSE_TOOL` schema and build a tool executor function that calls `design_cog.handle_propose(tool_input, message)` when `propose_server_design` is called. Pass `tools=[PROPOSE_TOOL]`, `tool_executor=executor`, and `max_tokens=4096` to `self.claude_client.ask()`. If the cog is not loaded, fall back to the current no-tools call. The tool executor should: (a) validate the proposal, (b) call `handle_propose()` which stores and renders it, (c) return a string result to Claude like "Proposal displayed to user for approval."

3. **Write `tests/test_server_design.py`**: Comprehensive test module covering:
   - **Tool schema tests**: `PROPOSE_TOOL` has required `name`, `description`, `input_schema` keys; schema has `summary` and `categories` as required properties.
   - **Channel name sanitizer tests**: spaces→hyphens, uppercase→lowercase, strips special chars, empty→fallback, length clamping to 100.
   - **Proposal validation tests**: valid proposal passes, missing `summary` fails, missing `categories` fails, empty categories list fails.
   - **Embed rendering tests**: embed has title, description includes summary, fields show categories and channels with type icons; large proposal (20+ channels) doesn't exceed 6000 chars.
   - **DynamicItem button tests**: custom_id format matches regex template, `from_custom_id` reconstructs state correctly, approve callback with admin permissions triggers build, approve without admin is rejected, cancel removes proposal and edits message.
   - **Build execution tests**: mock `guild.create_role()`, `guild.create_category()`, `guild.create_text_channel()`, `guild.create_voice_channel()` — verify roles created first, then categories, then channels; verify permission overwrites use created role objects; verify `action_log` entries written for each resource.
   - **Partial failure test**: mock one `create_text_channel` to raise `discord.Forbidden` — verify error message includes what was created, no crash.
   - **10+ channel bulk build test**: proposal with 3 categories, 12+ channels total (mix of text and voice) — verify all create calls made in correct order, no assertion errors.

4. **Add tool-passing test to `tests/test_ai_cog.py`**: New test class `TestServerDesignToolPassing` with tests:
   - When `ServerDesignCog` is loaded, `claude_client.ask()` is called with `tools` and `tool_executor` arguments.
   - When `ServerDesignCog` is NOT loaded, `claude_client.ask()` is called without tools (backward compat).
   - The `max_tokens` parameter is passed as `4096`.

5. **Add max_tokens test to `tests/test_claude.py`**: New test verifying that `ClaudeClient.ask(message, max_tokens=4096)` passes `max_tokens=4096` to `messages.create()` instead of the default `1024`.

## Must-Haves

- [ ] `bot.py` loads `server_design` cog and registers both DynamicItem button classes
- [ ] AICog passes `PROPOSE_TOOL` and tool executor to `claude_client.ask()` when ServerDesignCog is available
- [ ] AICog passes `max_tokens=4096` for server design requests
- [ ] AICog falls back to no-tools call when ServerDesignCog is not loaded
- [ ] `tests/test_server_design.py` covers: schema, sanitizer, validation, rendering, buttons, build order, partial failure, 10+ channels
- [ ] All existing tests in `test_ai_cog.py` and `test_claude.py` still pass (no regressions)
- [ ] 10+ channel build test proves the roadmap rate-limit risk is retired at contract level

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_server_design.py -v` — all tests pass
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_ai_cog.py -v` — all tests pass (existing + new)
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_claude.py -v` — all tests pass (existing + new)
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/ -v` — full suite green

## Inputs

- `bot/cogs/server_design.py` — T01 output: complete cog with PROPOSE_TOOL, ServerDesignCog, buttons, build executor
- `bot/claude.py` — T01 output: ClaudeClient with max_tokens parameter
- `bot/cogs/ai.py` — existing AICog to be modified
- `bot/bot.py` — existing bot setup to be modified
- `bot/cogs/verification.py` — reference for DynamicItem registration pattern
- `tests/test_ai_cog.py` — existing tests to extend
- `tests/test_claude.py` — existing tests to extend
- `tests/conftest.py` — shared fixtures

## Expected Output

- `bot/cogs/ai.py` — modified to pass tools and executor to Claude
- `bot/bot.py` — modified to load server_design cog and register dynamic items
- `tests/test_server_design.py` — new comprehensive test file
- `tests/test_ai_cog.py` — extended with tool-passing tests
- `tests/test_claude.py` — extended with max_tokens test

## Observability Impact

- **AICog tool passing**: When ServerDesignCog is loaded, every `claude_client.ask()` call now includes `tools=[PROPOSE_TOOL]`, `tool_executor`, and `max_tokens=4096`. The existing `ai_cog.route` structured log now implicitly covers tool-enabled requests. The tool executor delegates to `ServerDesignCog.handle_propose()` which already logs under `server_design.propose`.
- **Inspection**: To verify tool passing is active at runtime, check that `bot.get_cog("ServerDesignCog")` returns non-None. If the cog fails to load, the fallback path (no tools, max_tokens=1024) is taken silently — this is by design for graceful degradation.
- **Failure visibility**: If the server_design cog fails to load in `bot.py`, the `load_extension` call will raise and log an error. The AICog will continue working without tools. Tool executor errors (e.g., `handle_propose` raising) are caught by the outer `except Exception` block in `on_message` and reported as a generic error to the user.
- **Test coverage signals**: 51 tests in `test_server_design.py` (schema, sanitizer, validation, embed, buttons, build executor, partial failure, 10+ channels, callbacks, handle_propose), 6 new tests in `test_ai_cog.py` (tool passing), 2 new tests in `test_claude.py` (max_tokens).
