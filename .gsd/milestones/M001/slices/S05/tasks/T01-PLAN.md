---
estimated_steps: 5
estimated_files: 3
skills_used:
  - test
---

# T01: Create assistant cog with channel summary and URL fetch tools

**Slice:** S05 — Claude General Assistant
**Milestone:** M001

## Description

Create the `bot/cogs/assistant.py` cog that provides two Claude tools: `summarize_channel` (fetches recent channel messages for Claude to summarize) and `fetch_url` (fetches a URL's text content for Claude to summarize). The cog follows the same structural pattern as `server_design.py` — module-level tool schema dicts plus cog methods as executors. It also implements a **tool-provider protocol** (`get_tools()` and `handle_tool_call()` methods) that T02 will use to generically collect tools from all cogs.

Translation requires no tool — Claude handles it natively. The system prompt update happens in T02.

Also wire the cog into `bot/bot.py` and write comprehensive tests in `tests/test_assistant.py`.

## Steps

1. **Create `bot/cogs/assistant.py`** with:
   - `SUMMARIZE_CHANNEL_TOOL` dict — Anthropic tool format. Input schema: `channel_id` (string, the Discord channel ID to summarize). Description tells Claude when to use it.
   - `FETCH_URL_TOOL` dict — Anthropic tool format. Input schema: `url` (string, the URL to fetch).
   - `AssistantCog(commands.Cog)` class with `__init__(self, bot)`.
   - `get_tools() -> list[dict]` — returns `[SUMMARIZE_CHANNEL_TOOL, FETCH_URL_TOOL]`.
   - `handle_tool_call(self, name: str, tool_input: dict, message: discord.Message) -> str` — dispatcher that routes to the correct handler.
   - `async _handle_summarize_channel(self, tool_input: dict, message: discord.Message) -> str`:
     - Parse `channel_id` from input. If not provided, use `message.channel`.
     - Get the channel via `self.bot.get_channel(int(channel_id))`.
     - If channel is None, return error string "Could not find that channel."
     - Call `channel.history(limit=50)` and collect messages.
     - If no messages, return "The channel is empty or I don't have access."
     - Format messages as: `[author_name]: message_content` (one per line), newest first reversed to chronological.
     - Return the formatted text for Claude to summarize (Claude produces the summary in its response).
     - Catch `discord.Forbidden` → return "I don't have permission to read that channel's history."
   - `async _handle_fetch_url(self, tool_input: dict) -> str`:
     - Validate URL starts with `http://` or `https://`.
     - Use `httpx.AsyncClient` with `timeout=10.0`, `follow_redirects=True`, `headers={"User-Agent": "DiscordBot/1.0"}`.
     - Check `Content-Type` header — if not `text/html` or `text/plain` (or similar text types), return "I can only read text/HTML content from URLs."
     - Read response text, cap at 10,000 characters.
     - Strip HTML tags with a simple regex (`re.sub(r'<[^>]+>', '', text)`) and collapse whitespace.
     - Return the cleaned text for Claude to summarize.
     - Handle errors: `httpx.TimeoutException` → "The URL took too long to respond.", `httpx.HTTPStatusError` (4xx/5xx) → "The URL returned an error (status code).", `httpx.RequestError` → "Could not fetch the URL.", generic Exception → "Something went wrong fetching that URL."
   - Module-level `async def setup(bot)` that calls `bot.add_cog(AssistantCog(bot))`.

2. **Update `bot/bot.py`** — Add `await self.load_extension("bot.cogs.assistant")` in `setup_hook`, after the existing cog loads. No other changes.

3. **Create `tests/test_assistant.py`** with tests covering:
   - Tool schema validation: `SUMMARIZE_CHANNEL_TOOL` and `FETCH_URL_TOOL` have required fields (`name`, `description`, `input_schema`).
   - `get_tools()` returns both tool schemas.
   - `handle_tool_call()` dispatches to the correct handler for known tools, returns error for unknown tools.
   - `_handle_summarize_channel`:
     - Normal case: channel with messages → returns formatted text with author names and content.
     - Empty channel → returns appropriate message.
     - Channel not found (bot.get_channel returns None) → returns error.
     - `discord.Forbidden` raised → returns permission error message.
     - Default channel (no channel_id in input) → uses message.channel.
   - `_handle_fetch_url`:
     - Normal HTML page → returns stripped text content (mock httpx).
     - Timeout → returns timeout message.
     - Non-HTTP URL → returns validation error.
     - Non-text content type → returns content type error.
     - Response too large → content is truncated to 10K chars.
     - HTTP error status → returns status error message.
   - Module `setup()` function adds the cog.

   Use the same mock patterns as `test_ai_cog.py` and `test_server_design.py`. Mock `httpx.AsyncClient` for URL tests. Mock `channel.history()` for summary tests.

## Must-Haves

- [ ] `SUMMARIZE_CHANNEL_TOOL` and `FETCH_URL_TOOL` are valid Anthropic tool schema dicts
- [ ] `get_tools()` returns both tools
- [ ] `handle_tool_call()` dispatches correctly and handles unknown tools
- [ ] Channel summary handles: normal, empty, not-found, Forbidden
- [ ] URL fetch handles: success, timeout, non-HTTP URL, non-text content, large response, HTTP errors
- [ ] Cog loads in `bot.py` setup_hook
- [ ] `tests/test_assistant.py` has ≥10 passing tests

## Verification

- `.venv/bin/python -m pytest tests/test_assistant.py -v` — all tests pass
- `.venv/bin/python -m pytest tests/test_bot.py -v` — bot tests still pass (cog loading)
- `grep -q "bot.cogs.assistant" bot/bot.py` — cog is registered

## Inputs

- `bot/cogs/server_design.py` — reference pattern for tool schemas, cog structure, and `setup()` function
- `bot/cogs/ai.py` — understand how tools are currently passed to Claude (will be refactored in T02)
- `bot/claude.py` — understand the `ask()` interface that tools feed into
- `bot/bot.py` — where to add the cog load
- `tests/test_ai_cog.py` — reference pattern for test fixtures and mocking
- `tests/test_server_design.py` — reference pattern for cog tests

## Expected Output

- `bot/cogs/assistant.py` — new assistant cog with tool schemas, executors, and protocol methods
- `bot/bot.py` — updated to load the assistant cog
- `tests/test_assistant.py` — comprehensive test suite for the assistant cog
