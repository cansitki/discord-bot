# S02 Research: Natural Language Issue Creation

**Researched:** 2026-03-23
**Depth:** Light — all patterns proven in S01 and M001/S04-S05; this is assembly, not invention

## Requirements Targeted

- **R005** (primary) — Users describe bugs/features in natural language, Claude creates formatted GitHub issues with title/body/labels, preview embed with Confirm/Cancel buttons
- **R014** (supporting) — action_log entries for issue_created events (fifth consumer after verification, server_design, repo_linked, repo_unlinked)

## Summary

S02 wires together three proven patterns into the GitHubCog that S01 established:

1. **Tool-provider protocol** (D009) — GitHubCog already has `get_tools()` / `handle_tool_call()` stubs from S01. S02 populates them with a `create_issue` tool schema and handler.
2. **DynamicItem Confirm/Cancel buttons** (D011) — `IssueApproveButton` / `IssueCancelButton` follow the exact same pattern as `DesignApproveButton` / `DesignCancelButton` from server_design.py.
3. **In-memory pending store** (D008) — Pending issue previews stored in `_pending_issues: dict[tuple[int, int], dict]` keyed by `(guild_id, message_id)`, same as `_pending_proposals` in server_design.

The only new production code is:
- **`GitHubClient.create_issue()`** — one new API method (~25 lines), following `get_repo()` pattern exactly.
- **`create_issue` tool schema** — Anthropic tool format dict with title, body, labels fields.
- **`handle_tool_call()`** — route `create_issue` to a handler that looks up the channel's linked repo, renders a preview embed, sends it with DynamicItem buttons.
- **`IssueApproveButton` / `IssueCancelButton`** — DynamicItem subclasses (~80 lines total, clone of design buttons).
- **Registration** — `bot.py` adds the new DynamicItems to `add_dynamic_items()`.

No new migrations. No new dependencies. No new config. No new models.

## Recommendation

**Proceed** — this is straightforward assembly of proven patterns. Risk is low.

## Implementation Landscape

### Files to Change

| File | Change | Lines (est.) |
|------|--------|-------------|
| `bot/github_client.py` | Add `create_issue(owner, repo, title, body, labels)` method | ~30 |
| `bot/cogs/github.py` | Add tool schema, populate `get_tools()`, implement `handle_tool_call()`, add `_pending_issues` dict, issue preview embed builder, `IssueApproveButton` / `IssueCancelButton` DynamicItems with callbacks | ~250 |
| `bot/bot.py` | Import and register `IssueApproveButton` / `IssueCancelButton` in `add_dynamic_items()` | ~4 |
| `tests/test_github_client.py` | Add `TestCreateIssue` class — success, validation, API error | ~60 |
| `tests/test_github_cog.py` | Add tool schema tests, handle_tool_call routing, preview embed, DynamicItem callbacks, action_log entries, channel-not-linked error | ~200 |

### Pattern Map

Each component maps 1:1 to a proven pattern:

| Component | Pattern Source | Key Difference |
|-----------|---------------|----------------|
| `create_issue` tool schema | `PROPOSE_TOOL` in server_design.py | Simpler schema — just title, body, labels (strings + array) |
| `get_tools()` returning tool list | `ServerDesignCog.get_tools()` | Returns `[CREATE_ISSUE_TOOL]` instead of `[PROPOSE_TOOL]` |
| `handle_tool_call()` dispatch | `ServerDesignCog.handle_tool_call()` | Routes "create_issue" to handler, same if/else pattern |
| Issue preview embed | `render_proposal_embed()` in server_design.py | Shows title, body preview (truncated), labels, repo name |
| `IssueApproveButton` | `DesignApproveButton` in server_design.py | custom_id: `issue:approve:{guild_id}:{msg_id}`, calls `create_issue()` on approve |
| `IssueCancelButton` | `DesignCancelButton` in server_design.py | custom_id: `issue:cancel:{guild_id}:{msg_id}` |
| `_pending_issues` dict | `_pending_proposals` dict | Same `dict[tuple[int, int], dict]` pattern |
| `GitHubClient.create_issue()` | `GitHubClient.get_repo()` | POST instead of GET, sends JSON body, returns issue URL |
| action_log for issue_created | action_log for repo_linked | Same INSERT pattern, action_type="issue_created" |
| MockTransport for create_issue API | MockTransport for get_repo API (D019) | Handler returns `{"html_url": "...", "number": N}` |

### Key Implementation Details

#### 1. GitHubClient.create_issue()

```python
async def create_issue(self, owner: str, repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
    token = await self._ensure_token()
    url = f"/repos/{owner}/{repo}/issues"
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    resp = await self._client.post(url, headers={"Authorization": f"token {token}"}, json=payload)
    if resp.status_code == 201:
        return resp.json()
    raise GitHubAPIError(resp.status_code, f"Failed to create issue in {owner}/{repo}")
```

GitHub REST API: `POST /repos/{owner}/{repo}/issues` returns `201` with `{"number": N, "html_url": "..."}`.

#### 2. Tool Schema

```python
CREATE_ISSUE_TOOL = {
    "name": "create_issue",
    "description": "Create a GitHub issue in the repository linked to this channel. ...",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Issue title"},
            "body": {"type": "string", "description": "Issue body in markdown"},
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to apply"},
        },
        "required": ["title", "body"],
    },
}
```

#### 3. Handle Flow

`handle_tool_call("create_issue", input, message)` →
1. Look up `channel_repos` for `(guild_id, channel_id)` — error if not linked
2. Build preview embed (title, body truncated to ~200 chars, labels as tags, repo name in footer)
3. Send embed to channel
4. Store `{title, body, labels, repo_owner, repo_name, user_id}` in `_pending_issues[(guild_id, msg.id)]`
5. Edit message to add `IssueApproveButton` / `IssueCancelButton` view
6. Return "I've posted an issue preview. Click Approve to create it on GitHub."

#### 4. DynamicItem Callbacks

**IssueApproveButton.callback:**
1. Check user permissions (any user who can see the channel can approve — or restrict to message author; roadmap says "User clicks Confirm" implying the requesting user)
2. Look up pending issue by `(guild_id, msg_id)`
3. Call `GitHubClient.create_issue(owner, repo, title, body, labels)`
4. Write action_log entry: `action_type="issue_created"`, `target="owner/repo#N"`, details with issue URL
5. Edit message: "✅ Issue created: [title](url)"
6. Remove from `_pending_issues`

**IssueCancelButton.callback:**
1. Remove from `_pending_issues`
2. Edit message: "❌ Issue creation cancelled."

#### 5. Channel-Not-Linked Guard

The handler checks `channel_repos` before creating a preview. If no binding exists, returns `"This channel isn't linked to a GitHub repo. Use /link-repo to set one up."` — Claude relays this to the user.

#### 6. GitHub Config Guard

If `self.github_client is None`, `get_tools()` returns `[]` (no tools advertised). This means Claude never sees the `create_issue` tool when GitHub isn't configured. Clean degradation, consistent with S01's config guard pattern.

### Task Decomposition (Natural Seams)

1. **T01: GitHubClient.create_issue()** — Add API method + tests. Independent, small, unblocks T02.
2. **T02: Tool schema + handler + DynamicItems + preview embed** — The main work. Populates `get_tools()`, implements `handle_tool_call()`, adds DynamicItem classes, preview embed rendering, pending store, channel-not-linked guard.
3. **T03: Bot wiring + verify-deploy** — Register DynamicItems in bot.py, update verify-deploy.sh if needed (github cog import already checked). Run full test suite to confirm no regressions.

T01 is independent. T02 depends on T01's `create_issue()` method. T03 depends on T02.

### Risks

**Low risk overall.** All patterns are cloned from working code.

1. **Permission model for approve button** — server_design requires administrator permission. Issue creation should be more permissive (any user who triggered the tool call can approve). Decision: allow the requesting user (stored in pending dict) to approve. This is a minor difference from the server_design pattern.

2. **Body truncation in preview embed** — Discord embed field values max 1024 chars, description max 4096. Body should be truncated in the preview but sent in full to GitHub. Straightforward.

3. **Labels validation** — GitHub returns 422 if labels don't exist on the repo. The handler should catch this and report "Label 'X' doesn't exist." Consider whether to pre-validate labels or handle the error. Recommendation: handle the error — simpler, avoids an extra API call.

### Test Strategy

Follow existing patterns exactly:

- **GitHubClient.create_issue()**: `_make_client()` + `_make_transport()` with MockTransport. Test success (201), validation error (422), server error (500), auth header presence.
- **Tool schema**: Verify `CREATE_ISSUE_TOOL` has correct Anthropic format (name, description, input_schema with required fields).
- **get_tools()**: Returns `[CREATE_ISSUE_TOOL]` when github_client is present, `[]` when None.
- **handle_tool_call()**: Route "create_issue" to handler. Unknown tool returns error string.
- **Preview embed**: Verify embed has title, body snippet, labels, repo name.
- **Channel-not-linked**: Handler returns error string when no channel_repos binding.
- **DynamicItem custom_id format**: Verify `issue:approve:{guild_id}:{msg_id}` pattern.
- **Approve callback**: Removes pending, calls create_issue (mocked), writes action_log, edits message with URL.
- **Cancel callback**: Removes pending, edits message with cancellation.
- **Expired/missing proposal**: Approve on missing key sends ephemeral "expired" message.
- **action_log**: Verify issue_created entry with correct fields.

Estimated: ~25-30 new tests.
