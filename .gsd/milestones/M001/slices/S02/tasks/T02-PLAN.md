---
estimated_steps: 4
estimated_files: 1
skills_used:
  - test
---

# T02: Add comprehensive tests for verification cog

**Slice:** S02 — Member Verification Gate
**Milestone:** M001

## Description

Write thorough pytest tests for the verification cog, covering every branch of the verification flow: member join, approve, deny, setup command, and edge cases. Follow the testing patterns established in S01 (`tests/test_ping.py`, `tests/test_bot.py`).

Key testing knowledge from S01 (see KNOWLEDGE.md):
- **K005**: `bot.user` and `bot.tree` are read-only properties. Patch with `patch.object(type(bot), "user", ...)` on the class.
- **K006**: Test hybrid command callbacks directly via `cog.command.callback(cog, ctx)` to bypass `HybridCommand.__call__` routing.
- Use `AsyncMock` for all discord async operations (`member.add_roles()`, `member.kick()`, `channel.send()`, etc.)
- Use `MagicMock(spec=discord.Member)` etc. for type-safe mocks.

## Steps

1. **Create `tests/test_verification.py`** with fixtures:
   - `mock_bot` — MagicMock with `db` attribute set to an `AsyncMock` DatabaseManager
   - `cog` — `VerificationCog(mock_bot)`
   - `mock_member` — MagicMock(spec=discord.Member) with guild, roles, id attributes
   - `mock_guild` — MagicMock(spec=discord.Guild) with id, get_channel, get_member methods

2. **Write `TestOnMemberJoin` tests:**
   - `test_assigns_unverified_role` — verify `member.add_roles()` called with the unverified role
   - `test_sends_embed_to_verify_channel` — verify embed sent to verify channel with member info
   - `test_skips_when_no_config` — when `guild_config` has no verify IDs, no role assignment happens
   - `test_handles_forbidden_permission` — when `member.add_roles()` raises `discord.Forbidden`, error is logged/sent to channel, doesn't crash
   - `test_handles_missing_role` — when `guild.get_role()` returns None (role deleted), handles gracefully

3. **Write `TestVerificationView` tests:**
   - `test_approve_removes_unverified_role` — approve callback removes role from member
   - `test_approve_logs_to_action_log` — verify `db.execute` called with `member_approved` action type
   - `test_approve_edits_message` — original message updated to show approval
   - `test_deny_kicks_member` — deny callback calls `member.kick()`
   - `test_deny_logs_to_action_log` — verify `db.execute` called with `member_denied` action type
   - `test_approve_handles_member_not_found` — when member left before approval, `discord.NotFound` is caught, message updated to say member left
   - `test_deny_handles_member_not_found` — same for deny
   - `test_non_admin_cannot_approve` — interaction from non-admin user is rejected

4. **Write `TestVerifySetup` and `TestCogSetup` tests:**
   - `test_creates_role_and_channel` — verify setup creates role and channel
   - `test_saves_config_to_database` — verify guild_config updated with role/channel IDs
   - `test_requires_admin` — the command has the `has_permissions(administrator=True)` check
   - `test_setup_adds_cog` — module-level `setup()` calls `bot.add_cog`
   - `test_setup_hook_loads_verification` — verify `bot.py` setup_hook loads the verification extension (already tested in T01 implicitly, but add explicit assertion)

## Must-Haves

- [ ] All test classes created: TestOnMemberJoin, TestVerificationView, TestVerifySetup, TestCogSetup
- [ ] Edge cases covered: member leaves before approval, permission errors, missing config
- [ ] Tests follow K005/K006 patterns for discord.py mocking
- [ ] All tests pass with `.venv/bin/python -m pytest tests/test_verification.py -v`

## Verification

- `.venv/bin/python -m pytest tests/test_verification.py -v` — all tests pass, 0 failures
- `.venv/bin/python -m pytest tests/ -v` — full test suite still passes (no regressions)
- `grep -c "async def test_" tests/test_verification.py` returns >= 12 (sufficient coverage)

## Inputs

- `bot/cogs/verification.py` — the cog implementation to test (created in T01)
- `bot/bot.py` — modified bot with verification cog loading (created in T01)
- `tests/test_ping.py` — testing patterns to follow (cog fixture, callback testing via K006)
- `tests/test_bot.py` — bot testing patterns (patching read-only properties via K005)
- `tests/conftest.py` — shared fixtures (db_manager, mock_env)

## Expected Output

- `tests/test_verification.py` — comprehensive test file with 12+ test cases covering all verification flows

## Observability Impact

- **Signals changed:** None — this is a test-only task, no runtime code changes.
- **Inspection:** Test failures produce pytest output with assertion diffs. `grep -c "async def test_" tests/test_verification.py` shows coverage breadth.
- **Failure visibility:** Failing tests surface exact assertion mismatches, making it easy for a future agent to identify which branch or edge case broke.
