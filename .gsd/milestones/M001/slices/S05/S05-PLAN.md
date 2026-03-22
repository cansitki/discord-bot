# S05: Claude General Assistant

**Goal:** Users can ask Claude anything, get channel conversation summaries, translate text, and paste a link to get a summary — all via the existing message routing in ai.py.
**Demo:** Mention the bot with "summarize the last 50 messages in this channel" → Claude calls the `summarize_channel` tool, fetches history, and returns a summary. Paste a URL and ask "what's this about?" → Claude calls `fetch_url`, retrieves content, and summarizes it. Say "translate 'hello world' to Spanish" → Claude responds with the translation (no tool needed).

## Must-Haves

- `bot/cogs/assistant.py` cog with `summarize_channel` and `fetch_url` tool schemas and executors
- Generic tool-provider protocol in `ai.py`: any cog with `get_tools()` and `handle_tool_call()` gets its tools included in Claude calls
- `server_design.py` updated to implement the same protocol (backward-compatible)
- System prompt updated to mention assistant capabilities (summaries, translation, link previews)
- `bot.py` loads the assistant cog
- Channel summary tool handles errors (empty channel, missing permissions)
- URL fetch tool handles errors (timeout, non-HTML, too-large responses) and caps content at ~10KB
- All existing tests still pass (no regressions)

## Verification

- `.venv/bin/python -m pytest tests/test_assistant.py -v` — all assistant cog tests pass (tool schemas, summarize_channel executor, fetch_url executor, error handling)
- `.venv/bin/python -m pytest tests/test_ai_cog.py -v` — all ai cog tests pass with updated generic protocol assertions
- `.venv/bin/python -m pytest tests/ -v` — full suite passes (178+ tests, no regressions)

## Observability / Diagnostics

- **Logger `assistant`** emits structured messages for every tool call: `assistant.summarize_channel` (channel_id, message count), `assistant.fetch_url` (url, chars fetched, content_type errors, timeouts)
- **Failure visibility**: All error paths (Forbidden, timeout, non-text content, HTTP errors) are logged at WARNING level with the tool name and relevant context before returning a user-friendly error string
- **Inspection surface**: `bot.get_cog("AssistantCog").get_tools()` returns current tool schemas; `_pending_proposals` equivalent not applicable here (stateless tools)
- **Redaction**: URLs are truncated to 100 chars in log messages; no user message content is logged

## Integration Closure

- Upstream surfaces consumed: `bot/cogs/ai.py` (message routing, tool passing), `bot/claude.py` (ask() with tools), `bot/bot.py` (cog loading), `bot/cogs/server_design.py` (existing tool pattern)
- New wiring introduced in this slice: `bot/cogs/assistant.py` loaded in `bot.py` setup_hook; `ai.py` scans all cogs for `get_tools()`/`handle_tool_call()` protocol; `server_design.py` gains protocol methods
- What remains before the milestone is truly usable end-to-end: S06 (Railway deployment + integration test)

## Tasks

- [x] **T01: Create assistant cog with channel summary and URL fetch tools** `est:45m`
  - Why: Implements the three assistant features (summaries, URL previews, translation via prompt). Creates the new cog with tool schemas, executors, and protocol methods. Also adds the cog to bot.py and writes comprehensive tests.
  - Files: `bot/cogs/assistant.py`, `bot/bot.py`, `tests/test_assistant.py`
  - Do: Create `assistant.py` with `SUMMARIZE_CHANNEL_TOOL` and `FETCH_URL_TOOL` schemas (Anthropic tool format), implement `handle_summarize_channel()` using `channel.history(limit=50)`, implement `handle_fetch_url()` using `httpx.AsyncClient` with 10s timeout and ~10KB content cap, add `get_tools()` and `handle_tool_call()` protocol methods, basic HTML tag stripping for URL content. Load cog in `bot.py` setup_hook. Write tests covering: tool schema structure, channel summary formatting (normal, empty, permission error), URL fetch (success, timeout, non-HTML, too-large, invalid URL), protocol method behavior.
  - Verify: `.venv/bin/python -m pytest tests/test_assistant.py -v`
  - Done when: `tests/test_assistant.py` passes with ≥10 test cases covering both tools and edge cases

- [x] **T02: Refactor ai.py to generic tool-provider protocol and update system prompt** `est:30m`
  - Why: Replaces the hardcoded `ServerDesignCog` tool check in `ai.py` with a generic scan of all loaded cogs that implement `get_tools()`/`handle_tool_call()`. This makes assistant tools (and any future cog tools) automatically available to Claude. Also updates the system prompt and adapts `server_design.py` to the protocol.
  - Files: `bot/cogs/ai.py`, `bot/cogs/server_design.py`, `bot/claude.py`, `tests/test_ai_cog.py`
  - Do: (1) Add `get_tools()` and `handle_tool_call()` methods to `ServerDesignCog` in `server_design.py`. (2) Replace the hardcoded `design_cog = self.bot.get_cog("ServerDesignCog")` block in `ai.py` `on_message` with a loop over `self.bot.cogs.values()` that collects tools from any cog with `get_tools()`/`handle_tool_call()`. Build a combined tool list and a dispatcher executor. Set `max_tokens=4096` when any tools are present. (3) Update `_DEFAULT_SYSTEM_PROMPT` in `bot/claude.py` to mention assistant capabilities. (4) Update `tests/test_ai_cog.py` to verify generic protocol: tools from multiple cogs appear, dispatcher routes to correct cog, unknown tools handled, max_tokens logic.
  - Verify: `.venv/bin/python -m pytest tests/ -v` — full suite passes
  - Done when: Full test suite passes (178+ existing + new tests), `ai.py` no longer imports from `server_design.py`, and both assistant and server_design tools are collected via the generic protocol

## Files Likely Touched

- `bot/cogs/assistant.py` (new)
- `bot/cogs/ai.py`
- `bot/cogs/server_design.py`
- `bot/claude.py`
- `bot/bot.py`
- `tests/test_assistant.py` (new)
- `tests/test_ai_cog.py`
