---
estimated_steps: 5
estimated_files: 3
skills_used:
  - test
---

# T02: Implement create_issue tool, preview embed, DynamicItem buttons, and bot wiring

**Slice:** S02 — Natural Language Issue Creation
**Milestone:** M002

## Description

Populate the GitHubCog's tool-provider stubs with a working `create_issue` Claude tool. When Claude calls this tool, the handler looks up the channel's linked repo from `channel_repos`, renders a preview embed showing the issue title/body/labels, sends it with Approve/Cancel DynamicItem buttons, and stores the pending issue in memory. On Approve, it calls `GitHubClient.create_issue()` and writes an `issue_created` action_log entry. On Cancel, it removes the pending issue.

All patterns are cloned directly from `bot/cogs/server_design.py`:
- `CREATE_ISSUE_TOOL` schema → clone of `PROPOSE_TOOL` (simpler, just title/body/labels)
- `get_tools()` → returns `[CREATE_ISSUE_TOOL]` when configured (like `ServerDesignCog`)
- `handle_tool_call()` → routes "create_issue" to handler (like `handle_propose()`)
- `IssueApproveButton` / `IssueCancelButton` → clone of `DesignApproveButton` / `DesignCancelButton`
- `_pending_issues` dict → clone of `_pending_proposals` dict
- Bot.py DynamicItem registration → add to existing `add_dynamic_items()` call

**Permission model difference from server_design:** Any user who triggered the original tool call can approve (not admin-only). Store `user_id` in pending dict and check in approve callback.

## Steps

1. **Add tool schema and update `get_tools()` / `handle_tool_call()` in `bot/cogs/github.py`**:
   - Define `CREATE_ISSUE_TOOL` dict at module level (Anthropic tool format):
     ```python
     CREATE_ISSUE_TOOL = {
         "name": "create_issue",
         "description": "Create a GitHub issue in the repository linked to this channel. Use when a user describes a bug, feature request, or task that should be tracked as a GitHub issue.",
         "input_schema": {
             "type": "object",
             "properties": {
                 "title": {"type": "string", "description": "A concise issue title"},
                 "body": {"type": "string", "description": "Detailed issue description in markdown"},
                 "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to apply (e.g. 'bug', 'enhancement')"},
             },
             "required": ["title", "body"],
         },
     }
     ```
   - Update `get_tools()`: return `[CREATE_ISSUE_TOOL]` if `self.github_client is not None`, else `[]`
   - Update `handle_tool_call()`: if `name == "create_issue"`, call `self._handle_create_issue(tool_input, message)`, else return "Unknown tool: {name}"
   - Add `_pending_issues: dict[tuple[int, int], dict]` to `__init__` (same pattern as `_pending_proposals`)

2. **Implement `_handle_create_issue()` handler**:
   - Check `message.guild` — if None, return error string
   - Look up `channel_repos` via `self.bot.db.fetchone("SELECT repo_owner, repo_name FROM channel_repos WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id))`
   - If no binding, return `"This channel isn't linked to a GitHub repo. Use /link-repo to set one up."`
   - Extract `title`, `body`, `labels` from tool_input
   - Build preview embed:
     - Title: issue title
     - Description: body (truncated to 200 chars with "..." if longer)
     - Field "Labels": comma-separated labels, or "None" if empty
     - Footer: f"Repository: {repo_owner}/{repo_name}"
     - Colour: `discord.Colour.blue()`
   - Send embed to channel, get `sent` message
   - Store pending: `self._pending_issues[(guild_id, sent.id)] = {"title": title, "body": body, "labels": labels, "repo_owner": repo_owner, "repo_name": repo_name, "user_id": message.author.id}`
   - Create view with `IssueApproveButton(guild_id, sent.id)` and `IssueCancelButton(guild_id, sent.id)`, edit message to add view
   - Return confirmation string for Claude to relay

3. **Add `IssueApproveButton` and `IssueCancelButton` DynamicItem classes**:
   - `IssueApproveButton(DynamicItem[Button], template=r"issue:approve:(?P<guild_id>\d+):(?P<msg_id>\d+)")` — Green button, label "Approve"
     - `from_custom_id()`: parse guild_id and msg_id from regex match
     - `callback()`: look up cog via `bot.get_cog("GitHubCog")`, get pending by key `(guild_id, msg_id)`, check user_id matches (or send ephemeral "Only the requester can approve"), call `cog.github_client.create_issue(owner, repo, title, body, labels)`, write `issue_created` action_log entry (`target="owner/repo#N"`, `details=html_url`), edit message to "✅ Issue created: [title](url)", remove from pending
     - Handle missing pending (expired): send ephemeral "This issue preview has expired."
     - Handle `GitHubAPIError`: edit message with error details
   - `IssueCancelButton(DynamicItem[Button], template=r"issue:cancel:(?P<guild_id>\d+):(?P<msg_id>\d+)")` — Red button, label "Cancel"
     - `callback()`: remove from pending, edit message to "❌ Issue creation cancelled.", remove embed and view
   - Add `make_issue_view(guild_id, msg_id)` helper (clone of `make_design_view`)

4. **Register DynamicItems in `bot/bot.py`**:
   - Add import: `from bot.cogs.github import IssueApproveButton, IssueCancelButton`
   - Add to existing `self.add_dynamic_items(...)` call: `IssueApproveButton, IssueCancelButton`

5. **Add comprehensive tests to `tests/test_github_cog.py`**:
   - **Tool schema tests** (`TestCreateIssueTool`):
     - `test_tool_has_required_keys` — name, description, input_schema
     - `test_tool_name_is_create_issue`
     - `test_input_schema_requires_title_and_body`
     - `test_labels_is_optional_array`
   - **get_tools() tests** (update existing `TestToolProvider`):
     - `test_get_tools_returns_create_issue_tool` — returns `[CREATE_ISSUE_TOOL]` when github_client present
     - `test_get_tools_returns_empty_when_no_client` — returns `[]` when github_client is None
   - **handle_tool_call() tests**:
     - `test_handle_create_issue_routes_correctly` — calls handler
     - `test_handle_unknown_tool_returns_error` — (already exists, keep it)
   - **Handler tests** (`TestHandleCreateIssue`):
     - `test_channel_not_linked_returns_error` — db returns None, result contains "not linked"
     - `test_preview_embed_has_title_body_labels` — verify embed fields
     - `test_preview_embed_truncates_long_body` — body >200 chars truncated
     - `test_pending_issue_stored` — after handler, `_pending_issues` has entry
     - `test_preview_sent_with_buttons` — message.channel.send called, message edited with view
   - **DynamicItem tests** (`TestIssueDynamicItems`):
     - `test_approve_button_custom_id_format` — matches `issue:approve:{guild_id}:{msg_id}`
     - `test_cancel_button_custom_id_format` — matches `issue:cancel:{guild_id}:{msg_id}`
     - `test_approve_button_style_is_green`
     - `test_cancel_button_style_is_red`
     - `test_make_issue_view_has_two_items`
   - **Approve callback tests** (`TestIssueApproveCallback`):
     - `test_approve_calls_create_issue_and_writes_action_log` — pending exists, create_issue mocked returns `{"number": 1, "html_url": "..."}`, verify action_log INSERT and message edit
     - `test_approve_expired_sends_ephemeral` — no pending → ephemeral message
     - `test_approve_wrong_user_rejected` — user_id mismatch → ephemeral "Only the requester..."
     - `test_approve_api_error_reports_to_user` — `GitHubAPIError` → error message
   - **Cancel callback tests** (`TestIssueCancelCallback`):
     - `test_cancel_removes_pending_and_edits_message`
   - **action_log test**:
     - `test_issue_created_action_log_entry` — verify action_type, target, details fields

## Must-Haves

- [ ] `CREATE_ISSUE_TOOL` schema with name, description, input_schema (title, body required; labels optional array)
- [ ] `get_tools()` returns `[CREATE_ISSUE_TOOL]` when `github_client` is present, `[]` when None
- [ ] `handle_tool_call("create_issue", ...)` routes to handler; unknown tools return error string
- [ ] Handler checks `channel_repos` — returns error if channel not linked
- [ ] Preview embed shows title, truncated body, labels, repo name
- [ ] Pending issue stored in `_pending_issues[(guild_id, msg_id)]` with user_id
- [ ] `IssueApproveButton` DynamicItem: calls `create_issue()`, writes `issue_created` action_log, edits message with URL
- [ ] `IssueCancelButton` DynamicItem: removes pending, edits message with cancellation
- [ ] Approve checks user_id matches the requester (not admin-only like server_design)
- [ ] Expired/missing pending → ephemeral "expired" message
- [ ] Bot.py registers both DynamicItems in `add_dynamic_items()`
- [ ] 20+ new tests covering all paths

## Verification

- `.venv/bin/python -m pytest tests/test_github_cog.py -v` — all tests pass (existing + new)
- `.venv/bin/python -m pytest -x -q` — full suite passes (268+ tests, zero regressions)
- `bash scripts/verify-deploy.sh` — all checks pass (github module import covers new code)
- `.venv/bin/python -c "from bot.cogs.github import IssueApproveButton, IssueCancelButton, CREATE_ISSUE_TOOL"` — imports succeed

## Observability Impact

- Signals added/changed: `bot.cogs.github` logger — log.info for tool call routing, preview creation (guild_id, channel_id, issue title), approve/cancel callbacks (guild_id, user_id, msg_id); log.error for GitHubAPIError on create_issue failure
- How a future agent inspects this: `SELECT * FROM action_log WHERE action_type='issue_created'` shows all created issues with repo#number in target and html_url in details; `_pending_issues` dict shows in-flight previews (in-memory, lost on restart — acceptable per D008)
- Failure state exposed: GitHubAPIError status_code + message surfaced to user via embed edit; log.error with full error context

## Inputs

- `bot/cogs/github.py` — existing GitHubCog with tool-provider stubs and `_make_cog()` test pattern
- `bot/github_client.py` — GitHubClient with `create_issue()` method (from T01)
- `bot/cogs/server_design.py` — reference implementation for DynamicItem pattern, tool schema, `handle_propose()`, `_pending_proposals`
- `bot/bot.py` — existing `add_dynamic_items()` call to extend
- `tests/test_github_cog.py` — existing test file with helpers (`_make_cog`, `_make_mock_ctx`, `_make_mock_bot`)
- `tests/test_server_design.py` — reference tests for DynamicItem callbacks and tool schema assertions

## Expected Output

- `bot/cogs/github.py` — modified: tool schema, populated get_tools/handle_tool_call, DynamicItems, pending store, preview embed builder (~250 new lines)
- `bot/bot.py` — modified: 2 lines added (import + registration of IssueApproveButton, IssueCancelButton)
- `tests/test_github_cog.py` — modified: 20+ new tests across multiple test classes (~200 new lines)
