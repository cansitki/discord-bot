# S02: Natural Language Issue Creation

**Goal:** Users describe bugs/features in a linked channel, Claude generates a formatted issue preview with Confirm/Cancel buttons. On confirm, the issue is created on GitHub.
**Demo:** Claude tool call produces a preview embed with title, body, labels, and repo name. User clicks Approve → `GitHubClient.create_issue()` is called (mocked), action_log entry written, confirmation with issue URL sent. Cancel removes the pending issue.

## Must-Haves

- `create_issue` tool schema in Anthropic tool format with title, body, labels fields
- `GitHubClient.create_issue()` API method (POST /repos/{owner}/{repo}/issues)
- `GitHubCog.get_tools()` returns `[CREATE_ISSUE_TOOL]` when github_client is present, `[]` when None
- `GitHubCog.handle_tool_call()` routes "create_issue" to handler that looks up channel_repos, renders preview embed, stores pending issue
- `IssueApproveButton` / `IssueCancelButton` DynamicItems following server_design pattern (D011)
- Approve callback: calls `create_issue()`, writes `issue_created` action_log entry, edits message with issue URL
- Cancel callback: removes pending, edits message with cancellation
- Channel-not-linked guard returns error string when no binding exists
- Config guard: `get_tools()` returns `[]` when `github_client is None`
- Bot registers `IssueApproveButton` / `IssueCancelButton` in `add_dynamic_items()`

## Proof Level

- This slice proves: contract
- Real runtime required: no — GitHub API mocked via httpx.MockTransport, Discord mocked
- Human/UAT required: no — live issue creation requires real GitHub App credentials

## Verification

- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest tests/test_github_client.py tests/test_github_cog.py -v` — all new tests pass
- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest -x -q` — all 261+ tests pass (zero regressions)
- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M002 && bash scripts/verify-deploy.sh` — all checks pass
- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest tests/test_github_client.py tests/test_github_cog.py -v -k "error or 422 or 500 or not_linked or guard"` — failure-path and error-handling tests pass (GitHubAPIError raised with correct status codes, channel-not-linked guard returns error string)

## Observability / Diagnostics

- Runtime signals: `bot.cogs.github` logger — tool call routing, issue preview creation, approve/cancel callbacks with guild_id/channel_id/user_id
- Inspection surfaces: `action_log` table with `action_type="issue_created"` entries; `_pending_issues` in-memory dict for active previews
- Failure visibility: GitHubAPIError status code + message on create_issue failure (422 for invalid labels, 500 for API error); log entries for all error paths
- Redaction constraints: none — no secrets in issue content

## Integration Closure

- Upstream surfaces consumed: `bot/github_client.py` (GitHubClient), `bot/cogs/github.py` (GitHubCog with tool-provider stubs), `bot/bot.py` (DynamicItem registration), `bot/cogs/server_design.py` (DynamicItem pattern reference)
- New wiring introduced in this slice: `IssueApproveButton` / `IssueCancelButton` registered in `bot.py:add_dynamic_items()`; `get_tools()` returns `[CREATE_ISSUE_TOOL]`; AICog auto-discovers the tool via duck typing (D009)
- What remains before the milestone is truly usable end-to-end: S03 (repo status commands), S04 (webhook receiver)

## Tasks

- [x] **T01: Add GitHubClient.create_issue() API method with tests** `est:25m`
  - Why: The cog needs a `create_issue()` method on GitHubClient to call the GitHub REST API. This is a standalone API method that follows the existing `get_repo()` pattern exactly.
  - Files: `bot/github_client.py`, `tests/test_github_client.py`
  - Do: Add `create_issue(owner, repo, title, body, labels=None)` method — POST `/repos/{owner}/{repo}/issues` expecting 201, raises `GitHubAPIError` on failure. Add tests: success (201), validation error (422), server error (500), auth header present, labels included/omitted.
  - Verify: `.venv/bin/python -m pytest tests/test_github_client.py -v -k create_issue` — all new tests pass; `.venv/bin/python -m pytest tests/test_github_client.py -v` — all existing tests still pass
  - Done when: `GitHubClient.create_issue()` returns parsed JSON on 201, raises `GitHubAPIError` on non-201, and 6+ new tests pass

- [x] **T02: Implement create_issue tool, preview embed, DynamicItem buttons, and bot wiring** `est:50m`
  - Why: This is the core of S02 — wiring the tool-provider protocol, preview embed, Confirm/Cancel buttons, pending store, action_log, and bot.py registration. All patterns clone directly from `server_design.py` (D011).
  - Files: `bot/cogs/github.py`, `bot/bot.py`, `tests/test_github_cog.py`
  - Do: (1) Add `CREATE_ISSUE_TOOL` schema dict with title/body/labels fields. (2) Populate `get_tools()` to return `[CREATE_ISSUE_TOOL]` when `github_client` is present. (3) Implement `handle_tool_call()` routing for "create_issue" — look up channel_repos, build preview embed, store in `_pending_issues`, send with DynamicItem buttons. (4) Add `IssueApproveButton` / `IssueCancelButton` DynamicItem classes. (5) Approve callback: look up pending, call `create_issue()`, write `issue_created` action_log entry, edit message with URL. (6) Cancel callback: remove pending, edit message. (7) Register DynamicItems in `bot.py`. (8) Add comprehensive tests.
  - Verify: `.venv/bin/python -m pytest tests/test_github_cog.py -v` — all tests pass; `.venv/bin/python -m pytest -x -q` — all 261+ tests pass (zero regressions); `bash scripts/verify-deploy.sh` — all checks pass
  - Done when: Full tool → preview → approve → create flow tested at contract level; `get_tools()` returns tool when configured, `[]` when not; channel-not-linked guard works; action_log entries for issue_created verified; DynamicItems registered in bot.py; 20+ new tests pass

## Files Likely Touched

- `bot/github_client.py` — add `create_issue()` method
- `bot/cogs/github.py` — tool schema, handler, DynamicItems, preview embed, pending store
- `bot/bot.py` — register IssueApproveButton/IssueCancelButton
- `tests/test_github_client.py` — create_issue API tests
- `tests/test_github_cog.py` — tool schema, handler, DynamicItem, action_log tests
