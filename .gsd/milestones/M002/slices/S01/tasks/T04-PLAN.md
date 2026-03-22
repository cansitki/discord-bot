---
estimated_steps: 4
estimated_files: 4
skills_used:
  - test
  - review
---

# T04: Comprehensive test suite for GitHub cog and integration verification

**Slice:** S01 — GitHub Client + Channel-Repo Linking
**Milestone:** M002

## Description

Write the comprehensive test suite for the GitHub cog's slash commands, database interactions, error handling, and action_log entries. T02 already tests GitHubClient in isolation; this task tests the cog-level logic: /link-repo and /unlink-repo command flows, database round-trips through the migration, and integration with action_log. Also verify zero regressions across all M001 tests.

## Steps

1. **Update `tests/conftest.py` with GitHub-aware fixtures.** Add a `db_with_migrations` fixture that uses `db_manager` (file-backed) and runs real migrations from the project's migrations/ directory (including the new 003_channel_repos.sql). This ensures tests verify the actual migration SQL. Follow the existing `migrations_dir` fixture pattern but apply the migrations.

2. **Create `tests/test_github_cog.py` with command tests.** Follow the patterns established in `tests/test_server_design.py` and `tests/test_verification.py` for mocking discord.py objects. Test cases:
   - **link-repo success**: Mock GitHubClient.get_repo() to return repo data, mock ctx with guild/channel, verify channel_repos row inserted, action_log entry written with action_type="repo_linked", confirmation embed sent
   - **link-repo repo not found**: Mock get_repo() to raise GitHubAPIError(404), verify error message sent, no database changes
   - **link-repo already linked**: Pre-insert a channel_repos row, verify error message about existing link
   - **link-repo bad format**: Pass "not-a-repo" (no slash), verify format error
   - **link-repo missing config**: GitHubCog with github_client=None, verify config error message
   - **unlink-repo success**: Pre-insert a channel_repos row, call unlink, verify row deleted, action_log entry written with action_type="repo_unlinked", confirmation embed sent
   - **unlink-repo not linked**: No pre-existing row, verify "not linked" error message
   - **get_tools returns list**: Verify GitHubCog.get_tools() returns a list (empty for now)
   - **handle_tool_call returns error for unknown**: Verify handle_tool_call returns error string

3. **Test ChannelRepo database round-trip through migration.** Using the `db_with_migrations` fixture, insert a channel_repos row, fetch it, construct a ChannelRepo via from_row(), verify all fields including repo_full_name property. Test the PRIMARY KEY constraint: inserting two repos for the same (guild_id, channel_id) should fail.

4. **Run the full test suite and verify zero regressions.** Run `.venv/bin/python -m pytest tests/ -v` and confirm all M001 tests (206+) plus all new tests pass. Run `bash scripts/verify-deploy.sh` and confirm all checks pass.

## Must-Haves

- [ ] tests/test_github_cog.py covers all /link-repo and /unlink-repo paths (success, errors, edge cases)
- [ ] action_log entries verified for both link and unlink actions
- [ ] ChannelRepo database round-trip tested through real migration
- [ ] channel_repos PRIMARY KEY constraint tested
- [ ] All M001 tests still pass (no regressions)
- [ ] verify-deploy.sh passes with all checks

## Verification

- `.venv/bin/python -m pytest tests/test_github_cog.py -v` — all new tests pass
- `.venv/bin/python -m pytest tests/test_github_client.py -v` — all GitHub client tests pass
- `.venv/bin/python -m pytest tests/ -v` — 206+ total tests pass (M001 + new)
- `bash scripts/verify-deploy.sh` — all checks pass

## Inputs

- `bot/cogs/github.py` — GitHubCog to test (from T03)
- `bot/github_client.py` — GitHubClient used by the cog (from T02)
- `bot/models.py` — ChannelRepo model (from T01)
- `bot/config.py` — Config with GitHub fields (from T01)
- `migrations/003_channel_repos.sql` — migration to verify (from T01)
- `tests/conftest.py` — existing fixtures to extend
- `tests/test_server_design.py` — reference for cog testing patterns
- `tests/test_verification.py` — reference for cog testing patterns

## Expected Output

- `tests/test_github_cog.py` — comprehensive test file for GitHub cog
- `tests/conftest.py` — updated with db_with_migrations fixture
