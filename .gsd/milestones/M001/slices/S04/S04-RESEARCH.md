# S04: Conversational Server Design — Research

**Date:** 2026-03-22
**Depth:** Targeted
**Requirement:** R002 — Conversational server design & scaffolding

## Summary

S04 wires Claude's tool-use loop (already working in `ClaudeClient.ask()`) to Discord guild operations so users can describe a server layout in chat, get a proposal from Claude, approve it, and have the bot build it. The core challenge is a two-phase flow — **propose then build** — mediated by Claude tool use and Discord UI buttons.

The existing codebase provides strong foundations: `ClaudeClient` already handles the tool-use loop with `tools` + `tool_executor` params, the AI cog routes messages and chunks responses, and the verification cog demonstrates the DynamicItem button pattern for persistent approve/deny interactions. The work is extending these patterns to server design — a new cog, tool definitions for channel/category/role CRUD, a confirmation UI, and rate-limit-safe sequential execution.

discord.py 2.7.1 handles rate limiting internally with automatic retry, so the "rate limit risk" from the roadmap is largely handled — only `RateLimited` is raised when timeouts exceed `max_ratelimit_timeout`. The main risk is ensuring the two-phase propose→approve→build flow works cleanly with Claude's tool-use model.

## Recommendation

Build a `ServerDesignCog` that intercepts server-design intent via Claude tool use. The flow:

1. User describes layout (via mention or AI channel) → AICog routes to Claude
2. Claude receives server-design tools and proposes a plan using a `propose_server_design` tool (returns a JSON structure, not actual Discord API calls)
3. Bot renders the proposal as an embed with Approve/Cancel buttons (using `DynamicItem` pattern from verification cog)
4. On approve → bot executes the plan: creates roles first, then categories, then channels with permission overwrites, sequentially with `asyncio.sleep` between calls for safety
5. All actions logged to `action_log`

**Two-tool approach** (not many-tool): Give Claude a `propose_server_design` tool that accepts the full structure as a single JSON object (categories, channels, roles, permissions). This is better than individual `create_channel`/`create_role` tools because:
- The user wants to approve the *entire plan* before anything is created
- Claude can reason about the whole structure at once
- No risk of Claude creating channels before approval
- Simpler tool executor — one tool, one structured output

After approval, the bot iterates the approved plan and calls discord.py methods directly. Claude isn't involved in the execution phase.

## Implementation Landscape

### Key Files

- `bot/cogs/server_design.py` **(new)** — The cog: intercepts design requests via tool use, renders proposal embeds, handles approve/cancel buttons, executes the build plan. This is the bulk of the work (~300-400 lines).
- `bot/claude.py` — No changes needed. `ClaudeClient.ask(tools=..., tool_executor=...)` already supports what we need.
- `bot/cogs/ai.py` — Needs modification: when the message is about server design, pass server-design tools and a tool executor to `claude_client.ask()`. Currently calls `ask(clean_content)` with no tools. Needs to detect context and pass tools.
- `bot/bot.py` — Add `await self.load_extension("bot.cogs.server_design")` in `setup_hook`.
- `migrations/003_server_design.sql` **(new)** — Optional: table for pending design proposals (guild_id, user_id, proposal JSON, status). Could also be in-memory dict since proposals are short-lived, but persistence across restarts is safer for the verification pattern.
- `tests/test_server_design.py` **(new)** — Tests for proposal rendering, button callbacks, build execution, error handling.

### Integration Pattern: AI Cog ↔ Server Design Cog

Two viable approaches for how AICog integrates with server design tools:

**Option A (recommended): AICog always passes tools.** AICog's `on_message` handler always passes the server-design tool definitions and a tool executor to `claude_client.ask()`. Claude decides whether to use them based on the message. The tool executor lives in `ServerDesignCog` and is registered with the bot. AICog looks up available tool executors from loaded cogs.

**Option B: ServerDesignCog has its own listener.** ServerDesignCog has a separate `on_message` listener that detects server-design intent independently. This duplicates routing logic and creates ordering conflicts.

Option A is cleaner — it keeps message routing in AICog and lets Claude decide when to use tools.

### Build Order

1. **Tool definition + proposal rendering** — Define the `propose_server_design` tool schema, build the proposal embed renderer, and the Approve/Cancel buttons. This can be tested entirely in isolation with mock data.
2. **Build execution** — The function that takes an approved plan JSON and creates roles → categories → channels in the guild. Test with mocked discord.py guild methods.
3. **Wire into AICog** — Modify `AICog.on_message` to pass tools and tool executor to `claude_client.ask()`. Wire the tool executor to call `ServerDesignCog.handle_proposal()`.
4. **Migration + action logging** — Add migration for pending proposals table (if persisting), add action log entries for all created resources.

### Verification Approach

**Unit tests (pytest):**
- Proposal embed rendering: given a plan JSON, verify embed fields match
- Button callbacks: mock guild, verify approve triggers build, cancel cancels
- Build execution: mock `guild.create_role()`, `guild.create_category()`, `guild.create_text_channel()`, `guild.create_voice_channel()` — verify correct call order (roles first, then categories, then channels)
- Permission overwrites: verify `PermissionOverwrite` objects constructed correctly
- Error handling: verify partial failure doesn't crash (e.g., role created but channel creation fails)
- Tool definition schema: verify it matches Anthropic's expected format

**Manual integration test:**
- In a real Discord server, tell the bot "Set up a gaming server with channels for general chat, game announcements, and voice channels for different games"
- Verify Claude returns a structured proposal
- Verify Approve/Cancel buttons appear
- Click Approve → verify channels, categories, roles created
- Verify action_log entries

**Rate limit proof (from roadmap):**
- Create a design with 10+ channels → verify it completes without errors

### Tool Schema Design

```python
PROPOSE_TOOL = {
    "name": "propose_server_design",
    "description": "Propose a Discord server structure based on the user's description. Returns the proposal for user approval before any changes are made.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief description of the proposed server design"
            },
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "color": {"type": "string", "description": "Hex color code"},
                        "permissions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Permission names like 'manage_channels', 'send_messages'"
                        },
                        "hoist": {"type": "boolean"}
                    },
                    "required": ["name"]
                }
            },
            "categories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "channels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"type": "string", "enum": ["text", "voice"]},
                                    "topic": {"type": "string"},
                                    "role_access": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Role names that can access this channel"
                                    }
                                },
                                "required": ["name", "type"]
                            }
                        }
                    },
                    "required": ["name", "channels"]
                }
            }
        },
        "required": ["summary", "categories"]
    }
}
```

### discord.py API Methods Needed

All confirmed available in discord.py 2.7.1:

| Operation | Method | Key params |
|-----------|--------|------------|
| Create role | `guild.create_role()` | `name`, `permissions`, `colour`, `hoist`, `reason` |
| Create category | `guild.create_category()` | `name`, `overwrites`, `reason` |
| Create text channel | `guild.create_text_channel()` | `name`, `category`, `topic`, `overwrites`, `reason` |
| Create voice channel | `guild.create_voice_channel()` | `name`, `category`, `overwrites`, `reason` |
| Permission overwrite | `discord.PermissionOverwrite()` | `**kwargs` — e.g., `view_channel=True, send_messages=True` |

## Constraints

- **discord.py rate limiting** — discord.py auto-retries on 429s internally. Only raises `discord.RateLimited` when the timeout exceeds `max_ratelimit_timeout` (default: no max). For 10-20 channel creates, this is fine — no custom rate limiting needed, but sequential creation (not `asyncio.gather`) is still wise to avoid triggering global rate limits.
- **`max_tokens=1024` in ClaudeClient** — The current `_run_message_loop` uses `max_tokens=1024`. A complex server design proposal with 10+ channels and roles could exceed this. The server design cog should pass a larger `max_tokens` value. This means `ClaudeClient.ask()` needs a `max_tokens` parameter (currently hardcoded).
- **Discord embed size limit** — Embeds have a 6000 total character limit. A large proposal might exceed this. Chunk the proposal into multiple embeds or use a summary format.
- **DynamicItem pattern** — Approve/Cancel buttons must use `DynamicItem` (as in verification cog) for persistence across bot restarts. The `custom_id` template should encode `guild_id` and a proposal ID.

## Common Pitfalls

- **Creating channels before approval** — If Claude is given `create_channel` as a direct tool, it might call it immediately. Use a `propose_server_design` tool that only returns data, not performs actions. Execution happens only after button approval.
- **Role creation order matters** — Roles must exist before they can be referenced in channel permission overwrites. Build order must be: roles → categories → channels.
- **`max_tokens` truncation** — With `max_tokens=1024`, a complex proposal JSON could be truncated mid-output, producing invalid JSON in the tool call. Must increase `max_tokens` for design requests.
- **Channel name validation** — Discord channel names must be 1-100 characters, lowercase, no spaces (replaced with hyphens). Category names can have spaces. Claude might propose names that violate these — need a sanitizer.

## Open Risks

- **Claude tool-use reliability for structured output** — Claude must produce valid JSON matching the tool schema. With a complex nested schema (categories containing channels containing role references), there's a risk of malformed output. Mitigation: validate the schema in the tool executor before rendering.
- **Partial build failure** — If role creation succeeds but channel creation fails mid-way, the server is in an inconsistent state. Should we roll back? Rolling back (deleting created roles/channels) adds complexity. Simpler: log what was created, report the error, and let the admin clean up or re-run.
