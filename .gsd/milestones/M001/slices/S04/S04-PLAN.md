# S04: Conversational Server Design

**Goal:** Users describe a Discord server layout in natural language, Claude proposes a structure, the user approves via button, and the bot builds all categories, channels, roles, and permissions.
**Demo:** In a real Discord server, say "set up a gaming server with general chat, announcements, and voice channels for different games" → Claude returns a structured proposal embed → click Approve → bot creates all the channels, categories, and roles. A design with 10+ channels completes without errors.

## Must-Haves

- `propose_server_design` Claude tool that produces a structured JSON plan (categories, channels, roles, permissions) — Claude proposes but never directly creates resources
- Proposal rendered as a Discord embed with Approve/Cancel DynamicItem buttons that survive bot restarts
- Build executor that creates roles → categories → channels sequentially with correct permission overwrites
- Channel name sanitizer (lowercase, hyphens for spaces, 1-100 chars)
- `max_tokens` parameterizable in `ClaudeClient.ask()` (current hardcoded 1024 truncates large proposals)
- AICog passes server-design tools to Claude on every message (Claude decides when to use them)
- All created resources logged to `action_log`
- Schema validation of Claude's tool output before rendering the proposal
- 10+ channel design completes without rate-limit errors (proves roadmap risk)

## Proof Level

- This slice proves: contract + integration
- Real runtime required: no (all Discord API calls mocked in tests; manual UAT deferred to S06)
- Human/UAT required: no (manual testing in S06 final integration)

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_server_design.py -v` — all pass
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_ai_cog.py -v` — existing + new tests pass (AICog now passes tools)
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_claude.py -v` — existing + new max_tokens test pass
- Test for 10+ channel build verifies all `create_text_channel`/`create_voice_channel` calls made without error
- Test for partial failure verifies error is reported and already-created resources are listed

## Observability / Diagnostics

- Runtime signals: structured logging at `server_design.*` namespace — `server_design.propose` (proposal generated), `server_design.approve` (user approved), `server_design.build` (build started/completed/failed), `server_design.build.step` (each resource created)
- Inspection surfaces: `action_log` table entries with `action_type` in (`server_design_proposed`, `server_design_approved`, `server_design_built`, `server_design_role_created`, `server_design_category_created`, `server_design_channel_created`)
- Failure visibility: build errors logged with guild_id, phase (role/category/channel), resource name, and exception type; partial builds list what was created before failure
- Redaction constraints: none (no secrets in server design data)

## Integration Closure

- Upstream surfaces consumed: `bot/claude.py` (ClaudeClient.ask with tool support), `bot/cogs/ai.py` (on_message routing), `bot/bot.py` (cog loading, dynamic item registration), `bot/database.py` (action_log writes), `bot/cogs/verification.py` (DynamicItem pattern reference)
- New wiring introduced in this slice: `bot/cogs/server_design.py` loaded as extension in `bot.py`, DynamicItem subclasses registered via `add_dynamic_items`, AICog passes tools to ClaudeClient
- What remains before the milestone is truly usable end-to-end: S05 (general assistant features), S06 (Railway deployment + live integration test)

## Tasks

- [x] **T01: Build ServerDesignCog with tool schema, proposal embed, DynamicItem buttons, and sequential build executor** `est:2h`
  - Why: This is the core implementation — the cog that defines the `propose_server_design` tool, validates Claude's output, renders proposal embeds, handles Approve/Cancel buttons via DynamicItem, and executes the approved plan by creating roles→categories→channels sequentially. Also adds `max_tokens` parameter to `ClaudeClient.ask()` since the hardcoded 1024 will truncate complex proposals.
  - Files: `bot/cogs/server_design.py`, `bot/claude.py`
  - Do: (1) Add optional `max_tokens` param to `ClaudeClient.ask()` and `_run_message_loop()`, defaulting to 1024 for backward compat. (2) Create `bot/cogs/server_design.py` with: `PROPOSE_TOOL` schema dict, `validate_proposal()` for schema validation, `sanitize_channel_name()` utility, `render_proposal_embed()` that builds a Discord Embed from the plan JSON, `DesignApproveButton`/`DesignCancelButton` as DynamicItem subclasses (pattern from verification.py, custom_id encodes guild_id + proposal hash), `ServerDesignCog` class with `_pending_proposals` dict, `handle_propose()` tool executor method, and `_execute_build()` that creates roles first, then categories, then channels with permission overwrites — all sequential with action_log entries. (3) Channel name sanitizer: lowercase, replace spaces with hyphens, strip non-alphanumeric except hyphens, clamp to 1-100 chars.
  - Verify: `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.cogs.server_design import ServerDesignCog, PROPOSE_TOOL; print('OK')"` succeeds
  - Done when: `bot/cogs/server_design.py` exists with all components, `ClaudeClient.ask()` accepts `max_tokens` param, `sanitize_channel_name()` handles edge cases

- [x] **T02: Wire server design into AICog and bot, add migration and comprehensive tests** `est:2h`
  - Why: The cog exists but isn't loaded or connected. AICog needs to pass server-design tools to Claude. Bot needs to register the cog and its DynamicItems. Tests must prove the full propose→approve→build flow works and that 10+ channel designs complete without errors.
  - Files: `bot/cogs/ai.py`, `bot/bot.py`, `migrations/003_server_design.sql`, `tests/test_server_design.py`, `tests/test_ai_cog.py`, `tests/test_claude.py`
  - Do: (1) In `bot/bot.py` `setup_hook`: add `load_extension("bot.cogs.server_design")`, import and register `DesignApproveButton`/`DesignCancelButton` as dynamic items. (2) In `bot/cogs/ai.py`: get `ServerDesignCog` from bot, build tool list and executor, pass to `claude_client.ask(clean_content, tools=..., tool_executor=..., max_tokens=4096)`. (3) Add `migrations/003_server_design.sql` — no new tables needed (proposals are in-memory, actions use existing `action_log`), but add a comment migration as a no-op or skip if truly unnecessary. (4) Write `tests/test_server_design.py` covering: tool schema format, proposal validation, channel name sanitization, embed rendering, approve/cancel button callbacks with mocked guild, build execution order (roles→categories→channels), permission overwrites, partial failure handling, 10+ channel bulk build. (5) Add test in `tests/test_ai_cog.py` verifying tools are passed to Claude. (6) Add test in `tests/test_claude.py` verifying max_tokens parameter.
  - Verify: `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -m pytest tests/test_server_design.py tests/test_ai_cog.py tests/test_claude.py -v`
  - Done when: All tests pass, cog is loaded in bot.py, AICog passes tools to Claude, 10+ channel test proves rate-limit safety

## Files Likely Touched

- `bot/cogs/server_design.py` (new — core of this slice)
- `bot/claude.py` (add max_tokens parameter)
- `bot/cogs/ai.py` (pass tools to Claude)
- `bot/bot.py` (load cog, register dynamic items)
- `tests/test_server_design.py` (new — comprehensive tests)
- `tests/test_ai_cog.py` (add tool-passing tests)
- `tests/test_claude.py` (add max_tokens test)
