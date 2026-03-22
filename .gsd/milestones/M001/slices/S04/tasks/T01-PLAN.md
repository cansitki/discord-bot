---
estimated_steps: 5
estimated_files: 2
skills_used:
  - test
  - review
---

# T01: Build ServerDesignCog with tool schema, proposal embed, DynamicItem buttons, and sequential build executor

**Slice:** S04 — Conversational Server Design
**Milestone:** M001

## Description

Create the `bot/cogs/server_design.py` module — the core of the server design feature. This cog defines the `propose_server_design` Claude tool, validates Claude's structured output, renders proposals as Discord embeds, provides persistent Approve/Cancel buttons via the DynamicItem pattern (same as verification.py), and executes approved plans by creating roles → categories → channels sequentially.

Also modify `bot/claude.py` to accept an optional `max_tokens` parameter in `ask()` and `_run_message_loop()`, since the hardcoded `1024` will truncate complex server design proposals.

The cog stores pending proposals in an in-memory dict keyed by `(guild_id, message_id)`. This is acceptable because proposals are short-lived — if the bot restarts, users simply re-request.

## Steps

1. **Add `max_tokens` parameter to `ClaudeClient`**: In `bot/claude.py`, add `max_tokens: int = 1024` parameter to `ask()` and pass it through to `_run_message_loop()`, which passes it to `messages.create()`. This replaces the hardcoded `1024`. All existing callers are unaffected (default is 1024).

2. **Create `bot/cogs/server_design.py` with tool schema and data structures**: Define `PROPOSE_TOOL` dict matching the Anthropic tool format (name, description, input_schema with `summary`, `roles`, and `categories` containing `channels`). Add `sanitize_channel_name()` function (lowercase, spaces→hyphens, strip invalid chars, clamp 1-100 chars). Add `validate_proposal()` that checks the proposal dict has required fields and valid structure. Add `DESIGN_SYSTEM_PROMPT` constant with instructions for Claude about server design.

3. **Implement proposal embed rendering**: `render_proposal_embed(proposal: dict) -> discord.Embed` — builds a gold-colored embed showing the summary, roles (with colors), and categories with their channels (type icons: 💬 for text, 🔊 for voice). Handle the 6000-char embed limit by truncating with "... and N more" if needed.

4. **Implement DynamicItem buttons**: `DesignApproveButton` and `DesignCancelButton` as `discord.ui.DynamicItem[discord.ui.Button]` subclasses. Template patterns: `design:approve:(?P<guild_id>\d+):(?P<msg_id>\d+)` and `design:cancel:(?P<guild_id>\d+):(?P<msg_id>\d+)`. Approve callback: check admin permissions, look up proposal from `ServerDesignCog._pending_proposals`, call `_execute_build()`, log to `action_log`. Cancel callback: remove from pending, edit message to "Cancelled". Helper `make_design_view(guild_id, msg_id)` that creates a View with both buttons.

5. **Implement `ServerDesignCog` class with build executor**: The cog class holds `_pending_proposals: dict[tuple[int, int], dict]`. Method `handle_propose(proposal: dict, message: discord.Message)` stores the proposal and sends the embed with buttons. Method `_execute_build(guild: discord.Guild, proposal: dict, user_id: int)` creates resources sequentially: (a) create roles via `guild.create_role()`, tracking created role objects in a name→Role map, (b) create categories via `guild.create_category()` with appropriate overwrites, (c) create channels in each category via `guild.create_text_channel()` or `guild.create_voice_channel()` with overwrites referencing created roles. Each step logs to `action_log`. On error: catch exception, report what was created vs. what failed, do NOT roll back. Add `async def setup(bot)` function to load the cog.

## Must-Haves

- [ ] `ClaudeClient.ask()` accepts `max_tokens` parameter, defaults to 1024
- [ ] `PROPOSE_TOOL` dict is valid Anthropic tool format with `name`, `description`, `input_schema`
- [ ] `sanitize_channel_name()` handles: spaces→hyphens, uppercase→lowercase, strips invalid chars, clamps length
- [ ] `validate_proposal()` rejects proposals missing `summary` or `categories`
- [ ] `render_proposal_embed()` produces a Discord Embed with summary, roles, and channel listing
- [ ] `DesignApproveButton` and `DesignCancelButton` use DynamicItem pattern with regex templates
- [ ] `_execute_build()` creates roles first, then categories, then channels — order matters for permission overwrites
- [ ] Build errors are caught and reported (partial failure doesn't crash)
- [ ] All actions logged to `action_log` via `bot.db.execute()`

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.cogs.server_design import ServerDesignCog, PROPOSE_TOOL, sanitize_channel_name, validate_proposal, render_proposal_embed, DesignApproveButton, DesignCancelButton; print('All imports OK')"`
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.cogs.server_design import sanitize_channel_name; assert sanitize_channel_name('General Chat') == 'general-chat'; assert sanitize_channel_name('A' * 200) == 'a' * 100; print('Sanitizer OK')"`
- `cd /home/coder/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.claude import ClaudeClient; import inspect; sig = inspect.signature(ClaudeClient.ask); assert 'max_tokens' in sig.parameters; print('max_tokens param OK')"`

## Inputs

- `bot/claude.py` — existing ClaudeClient with hardcoded `max_tokens=1024` in `_run_message_loop`
- `bot/cogs/verification.py` — reference for DynamicItem pattern (ApproveButton, DenyButton, make_verification_view)
- `bot/database.py` — DatabaseManager for action_log writes
- `bot/models.py` — ActionLog model (reference for action_log schema)

## Expected Output

- `bot/cogs/server_design.py` — new cog module with all server design functionality
- `bot/claude.py` — modified with `max_tokens` parameter in `ask()` and `_run_message_loop()`

## Observability Impact

- **New structured logger:** `server_design` namespace — logs at `server_design.propose`, `server_design.approve`, `server_design.cancel`, `server_design.build`, and `server_design.build.step`.
- **New action_log entries:** `server_design_proposed`, `server_design_approved`, `server_design_built`, `server_design_role_created`, `server_design_category_created`, `server_design_channel_created`.
- **Inspection:** Query `SELECT * FROM action_log WHERE action_type LIKE 'server_design_%' ORDER BY timestamp DESC` to see all design activity.
- **Failure visibility:** Build errors logged with guild_id, phase (role/category/channel), resource name, and exception type.  Partial builds list what was created before the failure.
- **Future agent check:** Import test (`from bot.cogs.server_design import ServerDesignCog, PROPOSE_TOOL`) confirms the module is loadable.
