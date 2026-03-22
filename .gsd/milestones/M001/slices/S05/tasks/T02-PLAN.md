---
estimated_steps: 4
estimated_files: 4
skills_used:
  - test
  - review
---

# T02: Refactor ai.py to generic tool-provider protocol and update system prompt

**Slice:** S05 — Claude General Assistant
**Milestone:** M001

## Description

Replace the hardcoded `ServerDesignCog` tool check in `ai.py` with a generic loop that discovers tools from **any loaded cog** implementing the `get_tools()` / `handle_tool_call()` protocol. Add protocol methods to `ServerDesignCog` for backward compatibility. Update the default system prompt to mention assistant capabilities. Update `tests/test_ai_cog.py` to verify the new generic behavior.

After this task, both `AssistantCog` and `ServerDesignCog` tools appear in Claude's tool list automatically, and future cogs can add tools by simply implementing the two protocol methods.

## Steps

1. **Add protocol methods to `bot/cogs/server_design.py`**:
   - Add `get_tools(self) -> list[dict]` that returns `[PROPOSE_TOOL]`.
   - Add `handle_tool_call(self, name: str, tool_input: dict, message: discord.Message) -> str` that calls `self.handle_propose(tool_input, message)` for `"propose_server_design"` and returns `f"Unknown tool: {name}"` otherwise.
   - These methods match the same protocol `AssistantCog` already implements from T01.

2. **Refactor `bot/cogs/ai.py` `on_message`** — replace the hardcoded block:
   ```python
   design_cog = self.bot.get_cog("ServerDesignCog")
   if design_cog is not None:
       from bot.cogs.server_design import PROPOSE_TOOL
       tools = [PROPOSE_TOOL]
       ...
   ```
   With a generic protocol scan:
   ```python
   tools: list[dict[str, Any]] = []
   tool_cogs: list[Any] = []
   for cog_instance in self.bot.cogs.values():
       if cog_instance is self:
           continue  # skip AICog itself
       if hasattr(cog_instance, "get_tools") and hasattr(cog_instance, "handle_tool_call"):
           cog_tools = cog_instance.get_tools()
           if cog_tools:
               tools.extend(cog_tools)
               tool_cogs.append(cog_instance)

   if tools:
       max_tokens = 4096
       async def _tool_executor(tool_name: str, tool_input: dict[str, Any]) -> str:
           for tc in tool_cogs:
               tool_names = [t["name"] for t in tc.get_tools()]
               if tool_name in tool_names:
                   return await tc.handle_tool_call(tool_name, tool_input, message)
           return f"Unknown tool: {tool_name}"
       tool_executor = _tool_executor
   else:
       tools = None  # type: ignore
       tool_executor = None
   ```
   Remove the `from bot.cogs.server_design import PROPOSE_TOOL` import that was inside `on_message`. The `ai.py` module should no longer import anything from `server_design`.

3. **Update system prompt in `bot/claude.py`** — Modify `_DEFAULT_SYSTEM_PROMPT` to:
   ```
   "You are a helpful Discord bot assistant. Be concise, friendly, "
   "and helpful. Use Discord markdown formatting when appropriate. "
   "You can help with general questions, translate text between languages, "
   "summarize channel conversations, and summarize content from URLs."
   ```

4. **Update `tests/test_ai_cog.py`** — Modify the `TestServerDesignToolPassing` class (rename to `TestToolProviderProtocol` or similar):
   - `test_tools_passed_when_design_cog_loaded` → adapt to use the protocol: create a mock cog with `get_tools()` returning `[PROPOSE_TOOL]` and `handle_tool_call()`. Set `mock_bot.cogs` to a dict containing it. Verify tools are passed.
   - `test_no_tools_when_no_protocol_cogs` → set `mock_bot.cogs` to `{"AICog": cog}` (only the AICog itself). Verify no tools passed.
   - `test_multiple_cogs_tools_combined` → create two mock cogs each with `get_tools()` returning different tools. Verify Claude receives the combined list.
   - `test_tool_executor_dispatches_to_correct_cog` → with two cogs loaded, verify the executor routes tool calls to the cog that owns the tool name.
   - `test_tool_executor_unknown_tool` → verify unknown tool name returns error string.
   - `test_max_tokens_4096_when_tools_present` → verify max_tokens is 4096 when any cog provides tools.
   - `test_max_tokens_default_when_no_tools` → verify max_tokens stays 1024.
   - Keep existing routing/chunking/error tests unchanged.
   - Update `mock_bot` fixture: ensure `mock_bot.cogs` is a dict property (e.g., `mock_bot.cogs = {}` by default). Existing tests may need `mock_bot.cogs` set appropriately.

## Must-Haves

- [ ] `ai.py` no longer imports from `server_design.py` — fully generic
- [ ] Both `AssistantCog` and `ServerDesignCog` tools appear in Claude's tool list
- [ ] Tool executor dispatches to the correct cog based on tool name ownership
- [ ] `max_tokens=4096` when any tools are present, `1024` when none
- [ ] System prompt mentions summaries, translation, and URL capabilities
- [ ] `ServerDesignCog` has `get_tools()` and `handle_tool_call()` methods
- [ ] All existing tests pass (no regressions) — full suite 178+
- [ ] Updated `test_ai_cog.py` tests verify the generic protocol

## Verification

- `.venv/bin/python -m pytest tests/test_ai_cog.py -v` — all updated tests pass
- `.venv/bin/python -m pytest tests/test_server_design.py -v` — server design tests still pass
- `.venv/bin/python -m pytest tests/ -v` — full suite passes (180+ tests total)
- `! grep -q "from bot.cogs.server_design" bot/cogs/ai.py` — no direct import from server_design

## Inputs

- `bot/cogs/assistant.py` — T01 output; implements `get_tools()` and `handle_tool_call()` protocol
- `bot/cogs/ai.py` — current hardcoded tool passing to refactor
- `bot/cogs/server_design.py` — needs protocol methods added
- `bot/claude.py` — system prompt to update
- `tests/test_ai_cog.py` — existing tests to adapt for generic protocol
- `tests/test_assistant.py` — T01 output; reference for protocol test patterns

## Expected Output

- `bot/cogs/ai.py` — refactored to generic tool-provider protocol
- `bot/cogs/server_design.py` — updated with `get_tools()` and `handle_tool_call()` methods
- `bot/claude.py` — updated system prompt
- `tests/test_ai_cog.py` — updated tests for generic protocol

## Observability Impact

- **Tool discovery logging**: The generic protocol scan in `ai.py` doesn't add explicit logging (the tools themselves log via their cog loggers), but tool dispatch errors surface as `"Unknown tool: {name}"` strings returned to Claude.
- **Inspection surface**: `bot.cogs` dict now exposes all tool-providing cogs via `get_tools()` — any cog can be inspected at runtime. `ServerDesignCog.get_tools()` and `ServerDesignCog.handle_tool_call()` are now the canonical entry points (replacing the old direct `handle_propose` call from ai.py).
- **System prompt**: Updated to include assistant capabilities, visible in `ClaudeClient.system_prompt` attribute.
- **Failure state**: If a tool-providing cog is unloaded between discovery and dispatch, the executor returns `"Unknown tool: {name}"` — Claude sees this and can inform the user.
