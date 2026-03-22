# S02: Member Verification Gate

**Goal:** New members are automatically gated behind a verification channel until an admin approves them.
**Demo:** User joins server → bot assigns unverified role, locks them to #verify, posts approval embed → admin clicks Approve → member gets full access. Deny → member is kicked. Buttons survive bot restarts.

## Must-Haves

- `on_member_join` assigns unverified role and restricts member to the verify channel
- Persistent `discord.ui.View` with Approve/Deny buttons (custom_id-based, survives restarts)
- `/verify-setup` admin command that creates or adopts the unverified role and verify channel, saves IDs to `guild_config`
- All approve/deny actions logged to `action_log` table
- Edge cases handled: member leaves before approval, bot lacks permissions, role hierarchy issues
- Cog loaded in `bot.py` setup_hook, persistent view registered via `bot.add_view()`

## Proof Level

- This slice proves: contract
- Real runtime required: no (full integration is S06; contract tests with mocked discord objects prove logic)
- Human/UAT required: no (manual Discord test is S06)

## Verification

- `.venv/bin/python -m pytest tests/test_verification.py -v` — all tests pass
- Tests cover: on_member_join assigns role + sends embed, approve button grants access + removes unverified role, deny button kicks + logs, setup command creates role/channel and persists config, permission error handling, member-left-before-approval edge case, persistent view registration in setup_hook

## Observability / Diagnostics

- Runtime signals: `logging.getLogger(__name__)` entries for join events, approve/deny actions, permission errors
- Inspection surfaces: `action_log` table rows with `action_type` in `("member_approved", "member_denied")`, `guild_config` table for verify channel/role IDs
- Failure visibility: Permission errors logged with guild_id and member_id context; `discord.Forbidden` caught and reported to channel
- Redaction constraints: none (no secrets in verification flow)

## Integration Closure

- Upstream surfaces consumed: `bot/bot.py` (DiscordBot class, setup_hook, cog loading), `bot/database.py` (DatabaseManager), `bot/models.py` (GuildConfig, ActionLog), `migrations/001_initial.sql` (schema with verify columns)
- New wiring introduced in this slice: `bot.cogs.verification` loaded in setup_hook, `VerificationView` registered as persistent view
- What remains before the milestone is truly usable end-to-end: Claude integration (S03), server design (S04), assistant features (S05), Railway deployment (S06)

## Tasks

- [x] **T01: Build verification cog with persistent approval buttons and setup command** `est:1h`
  - Why: This is the entire runtime implementation of R003 — the on_member_join flow, the approval UI, the admin setup, and the wiring into bot.py
  - Files: `bot/cogs/verification.py`, `bot/bot.py`
  - Do: Create `verification.py` cog with: `VerificationView` (persistent `discord.ui.View`, `timeout=None`, explicit `custom_id` on Approve/Deny buttons), `on_member_join` listener that fetches guild config, assigns unverified role, sends embed with approval buttons to verify channel. `/verify-setup` hybrid command (admin-only) that creates or adopts unverified role and verify channel, validates role hierarchy, saves IDs to `guild_config`. Approve callback: remove unverified role, log to `action_log`. Deny callback: kick member, log to `action_log`. Wrap role operations in try/except for `discord.NotFound` (member left) and `discord.Forbidden` (permissions). Wire into `bot.py`: load `bot.cogs.verification` in `setup_hook`, register `VerificationView` via `self.add_view()`.
  - Verify: `python -c "from bot.cogs.verification import VerificationCog, VerificationView; print('imports ok')"`
  - Done when: `bot/cogs/verification.py` exists with VerificationCog class containing on_member_join, verify-setup command, and VerificationView with Approve/Deny buttons; `bot/bot.py` loads the verification cog and registers the persistent view

- [x] **T02: Add comprehensive tests for verification cog** `est:45m`
  - Why: Proves the verification gate contract works correctly — every branch of the flow is tested including edge cases
  - Files: `tests/test_verification.py`
  - Do: Write pytest tests following patterns from `tests/test_ping.py` and `tests/test_bot.py`. Test classes: `TestOnMemberJoin` (assigns role, sends embed with buttons, handles missing config, handles permission errors), `TestVerificationView` (approve grants role + removes unverified + logs, deny kicks + logs, member-not-found graceful handling), `TestVerifySetup` (creates role/channel, saves to guild_config, admin-only check, role hierarchy validation), `TestVerificationCogSetup` (setup function adds cog, persistent view registered). Use `AsyncMock` for discord objects, `patch.object(type(bot), ...)` for read-only properties per K005, test callbacks directly via `.callback()` per K006.
  - Verify: `.venv/bin/python -m pytest tests/test_verification.py -v` — all tests pass
  - Done when: All tests pass, covering on_member_join, approve, deny, setup command, edge cases (member left, permission errors), and cog/view registration

## Files Likely Touched

- `bot/cogs/verification.py` (new)
- `bot/bot.py` (modify — add cog load + persistent view)
- `tests/test_verification.py` (new)
