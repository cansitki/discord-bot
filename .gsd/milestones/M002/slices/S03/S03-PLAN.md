# S03: Repo Status Commands

**Goal:** `/repo-status` slash command shows open PRs and recent commits for the linked repo as a Discord embed.
**Demo:** User runs `/repo-status` in a linked channel and sees an embed with open PRs (title, author, age) and recent commits (short SHA, message, author, age). Unlinked channels get a helpful error. Empty states are handled gracefully.

## Must-Haves

- `list_pulls(owner, repo)` and `list_commits(owner, repo)` methods on GitHubClient
- `/repo-status` hybrid command in GitHubCog — no permission requirement (read-only)
- Embed with "Open Pull Requests" and "Recent Commits" fields using `discord.utils.format_dt()` for relative timestamps
- Empty states: "No open pull requests" / "No recent commits"
- Error paths: channel not linked, GitHub config missing, API error, no guild
- Contract-level test coverage for all API methods and command paths

## Proof Level

- This slice proves: contract
- Real runtime required: no
- Human/UAT required: no

## Verification

- `.venv/bin/python -m pytest tests/test_github_client.py tests/test_github_cog.py -v` — all existing tests pass + new `TestListPulls`, `TestListCommits`, `TestRepoStatus` classes pass
- `.venv/bin/python -m pytest -q` — total test count ≥ 314 with zero failures, zero regressions

## Observability / Diagnostics

- **Runtime signals:** `list_pulls` and `list_commits` log `GET {url} -> {status_code}` at INFO level, matching existing API method conventions. Token acquisition/refresh logs are inherited from `_ensure_token()`.
- **Inspection surfaces:** `GitHubAPIError.status_code` and `.message` expose failure details programmatically. Error embeds in the `/repo-status` command surface user-visible diagnostics.
- **Failure visibility:** Non-200 responses raise `GitHubAPIError` with the HTTP status code and a descriptive message including `owner/repo`. These propagate to the command error handler, which renders a user-facing error embed.
- **Redaction constraints:** Installation tokens (`ghs_*`) are never logged. Private key material is excluded from all log output. The `TestLoggingSafety` class enforces this contractually.

## Tasks

- [x] **T01: Add list_pulls and list_commits to GitHubClient with tests** `est:30m`
  - Why: The `/repo-status` command needs GitHub API methods to fetch open PRs and recent commits. These follow the exact same pattern as `get_repo()` and `create_issue()`.
  - Files: `bot/github_client.py`, `tests/test_github_client.py`
  - Do: Add `list_pulls(owner, repo, *, limit=5)` hitting `GET /repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc&per_page={limit}` and `list_commits(owner, repo, *, limit=5)` hitting `GET /repos/{owner}/{repo}/commits?per_page={limit}`. Both return `list[dict]`, use `_ensure_token()`, send `Authorization: token {token}`, and raise `GitHubAPIError` on non-200 status. Add `TestListPulls` and `TestListCommits` test classes using the established `_make_client`/`_make_transport` helpers.
  - Verify: `.venv/bin/python -m pytest tests/test_github_client.py -v` — all existing + new tests pass
  - Done when: `list_pulls` and `list_commits` tested for success, empty list, 404, 500, and auth header — ~10 new tests

- [ ] **T02: Add /repo-status command to GitHubCog with tests** `est:45m`
  - Why: This is the user-facing command that fulfils R006. It looks up the channel's linked repo, calls the new API methods, and renders a Discord embed.
  - Files: `bot/cogs/github.py`, `tests/test_github_cog.py`
  - Do: Add `repo_status` hybrid command (no `manage_channels` permission). Look up `channel_repos` for the channel. Call `list_pulls()` and `list_commits()`. Render embed with title "📊 Status: owner/repo", blue colour, "Open Pull Requests" field (PR lines: `#N title — @author (relative_time)` or "No open pull requests"), "Recent Commits" field (commit lines: `` `sha7` message — author (relative_time) `` or "No recent commits"). Use `discord.utils.format_dt(dt, 'R')` for relative timestamps. Parse GitHub ISO 8601 dates with `datetime.fromisoformat()`. Handle error paths: not linked, no config, no guild, API error. Add `TestRepoStatus` class with ~8-10 tests.
  - Verify: `.venv/bin/python -m pytest tests/test_github_cog.py -v` — all existing + new tests pass. `.venv/bin/python -m pytest -q` — total ≥ 314, zero failures.
  - Done when: `/repo-status` produces correct embeds from mocked data, handles all error paths, and total test count is ≥ 314

## Files Likely Touched

- `bot/github_client.py`
- `bot/cogs/github.py`
- `tests/test_github_client.py`
- `tests/test_github_cog.py`
