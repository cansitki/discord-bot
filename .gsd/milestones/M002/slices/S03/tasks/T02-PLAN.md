---
estimated_steps: 4
estimated_files: 2
skills_used:
  - test
---

# T02: Add /repo-status command to GitHubCog with tests

**Slice:** S03 — Repo Status Commands
**Milestone:** M002

## Description

Add the `/repo-status` hybrid command to `GitHubCog` in `bot/cogs/github.py`. This is the user-facing command that fulfils requirement R006. It looks up the channel's linked repository from `channel_repos`, calls `GitHubClient.list_pulls()` and `list_commits()`, and renders a Discord embed with the results. No `manage_channels` permission is required — this is a read-only operation.

## Steps

1. Add the `repo_status` hybrid command to `GitHubCog` in `bot/cogs/github.py`:
   - Decorator: `@commands.hybrid_command(name="repo-status", description="Show open PRs and recent commits for the linked repo")`
   - `@commands.guild_only()`
   - No permission decorator (read-only, anyone can use)
   - Guard: check `ctx.guild is None` → send error, return
   - Guard: check `self.github_client is None` → send "GitHub integration is not configured" error, return
   - Look up `channel_repos` for `(guild_id, channel_id)` using `self.bot.db.fetchone()` — same SQL as `_handle_create_issue` line 289-292
   - If no binding → send "This channel isn't linked to a GitHub repo. Use /link-repo to set one up."
   - Call `self.github_client.list_pulls(repo_owner, repo_name)` and `self.github_client.list_commits(repo_owner, repo_name)`
   - Wrap in try/except `GitHubAPIError` → send "❌ Failed to fetch repo status: {exc.message} (HTTP {exc.status_code})"

2. Build the embed:
   - Title: `📊 Status: {repo_owner}/{repo_name}`
   - Colour: `discord.Colour.blue()`
   - Field "Open Pull Requests" (`inline=False`): format each PR as `[#{number} {title}]({html_url}) — @{user.login} ({relative_time})`. Use `discord.utils.format_dt(dt, 'R')` for relative timestamps. Parse GitHub's ISO 8601 `created_at` field via `datetime.fromisoformat()` (replace trailing `Z` with `+00:00` if needed). If no PRs, show "No open pull requests".
   - Field "Recent Commits" (`inline=False`): format each commit as `` [`{sha[:7]}`]({html_url}) {first_line_of_message} — {author_name} ({relative_time}) ``. Parse `commit.author.date`. If no commits, show "No recent commits".
   - Send the embed via `ctx.send(embed=embed)`

3. Add a `_parse_github_dt` module-level helper in `bot/cogs/github.py`:
   ```python
   def _parse_github_dt(iso_str: str) -> datetime:
       """Parse a GitHub ISO 8601 timestamp into a timezone-aware datetime."""
       return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
   ```

4. Add `TestRepoStatus` class to `tests/test_github_cog.py` with these tests:
   - `test_repo_status_success` — both PRs and commits present, verify embed title, colour, both fields have content
   - `test_repo_status_empty_prs` — no PRs, verify "No open pull requests" in the embed field
   - `test_repo_status_empty_commits` — no commits, verify "No recent commits" in the embed field
   - `test_repo_status_both_empty` — no PRs and no commits, verify both empty-state messages
   - `test_repo_status_not_linked` — channel not in `channel_repos`, verify error message about linking
   - `test_repo_status_no_config` — `github_client` is None, verify config error message
   - `test_repo_status_no_guild` — `ctx.guild` is None, verify server-only error message
   - `test_repo_status_api_error` — `list_pulls` raises `GitHubAPIError`, verify error embed with status code
   - `test_repo_status_embed_format` — verify PR lines contain `#number`, `title`, `@author`; commit lines contain short SHA, message, author name

   Use the established `_make_cog()` and `_make_mock_ctx()` helpers. Mock `list_pulls` and `list_commits` on the cog's `github_client` as `AsyncMock(return_value=[...])`. For the linked-channel tests, mock `bot.db.fetchone` to return a row with `repo_owner` and `repo_name`.

## Must-Haves

- [ ] `/repo-status` renders embed with "Open Pull Requests" and "Recent Commits" fields
- [ ] Relative timestamps via `discord.utils.format_dt(dt, 'R')` 
- [ ] Empty states handled: "No open pull requests" / "No recent commits"
- [ ] Error paths: not linked, no config, no guild, API error — all with user-friendly messages
- [ ] No `manage_channels` permission required
- [ ] ~8-10 new tests all passing

## Verification

- `.venv/bin/python -m pytest tests/test_github_cog.py -v` — all tests pass including new `TestRepoStatus`
- `.venv/bin/python -m pytest -q` — total test count ≥ 314, zero failures, zero regressions

## Inputs

- `bot/github_client.py` — GitHubClient with `list_pulls()` and `list_commits()` methods (from T01)
- `bot/cogs/github.py` — existing GitHubCog with `link_repo` command pattern to follow
- `tests/test_github_cog.py` — existing test file with `_make_cog`, `_make_mock_ctx` helpers

## Expected Output

- `bot/cogs/github.py` — modified with `repo_status` hybrid command and `_parse_github_dt` helper added
- `tests/test_github_cog.py` — modified with `TestRepoStatus` test class added
