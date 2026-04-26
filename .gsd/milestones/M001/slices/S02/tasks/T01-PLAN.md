---
estimated_steps: 5
estimated_files: 2
skills_used: []
---

# T01: Build verification cog with persistent approval buttons and setup command

**Slice:** S02 — Member Verification Gate
**Milestone:** M001

## Description

Implement the full verification gate as a discord.py cog. This covers the on_member_join event handler, the persistent VerificationView with Approve/Deny buttons, the `/verify-setup` admin command, and wiring into bot.py. This is the entire runtime implementation of requirement R003.

The cog follows the pattern established by `bot/cogs/ping.py` — a Cog subclass with a module-level `setup()` function.

## Steps

1. **Create `bot/cogs/verification.py`** with these components:
   - `VerificationView(discord.ui.View)` — persistent view (`timeout=None`) with two buttons:
     - Approve button: `custom_id="verify_approve"`, green style. Callback: check `interaction.user` has admin perms, remove unverified role from target member, log `"member_approved"` to `action_log`, edit the message to show approval. Wrap in try/except for `discord.NotFound` (member left) and `discord.Forbidden` (missing permissions).
     - Deny button: `custom_id="verify_deny"`, red style. Callback: check admin perms, kick member, log `"member_denied"` to `action_log`, edit message. Same error handling.
   - The view needs access to the bot instance to reach `bot.db`. Store member_id in the embed or retrieve it from the message. Use `custom_id` pattern like `verify_approve:{member_id}` to encode the target member, OR store member_id in the embed and parse it from `interaction.message`. The simpler approach: use a fixed `custom_id` per button type and store the `member_id` in the embed footer or a hidden field, then parse it in the callback. **Actually simplest**: subclass the View to accept `member_id` as constructor arg, but for persistence across restarts, the view must be reconstructed without args. So: use `custom_id=f"verify_approve:{member_id}"` pattern — the bot registers a single persistent view with `custom_id` pattern matching. **Correction**: discord.py persistent views require exact `custom_id` match OR the view must be re-added on startup. Best approach: Create the view dynamically per-member with `custom_id=f"verify:approve:{member_id}"`, but register a single `VerificationView` class with no member_id that handles all `custom_id`s starting with `"verify:approve:"` and `"verify:deny:"` by overriding `interaction_check` or using `discord.ui.DynamicItem`. **Simplest working approach**: Use `discord.ui.View` with `timeout=None`, buttons with `custom_id=f"verify:approve:{member_id}"` and `custom_id=f"verify:deny:{member_id}"`. On bot startup, add a catch-all persistent view that matches these patterns.

   - `VerificationCog(commands.Cog)` containing:
     - `on_member_join(self, member)` listener: fetch `guild_config` for `member.guild.id`, if `verify_role_id` and `verify_channel_id` are set, add the unverified role to the member, send an embed to the verify channel with the member info and Approve/Deny buttons. If config not set, log and skip.
     - `verify_setup` hybrid command (admin-only via `@commands.has_permissions(administrator=True)`): Creates an "Unverified" role (or uses existing if name matches) with no permissions. Creates a #verify channel (or uses existing) with permission overwrites: @everyone can't view, unverified role can view + send messages. Saves `verify_role_id` and `verify_channel_id` to `guild_config` via `INSERT OR REPLACE`. Validates that bot's highest role is above the unverified role.
   - Module-level `setup(bot)` function that adds the cog.

2. **Handle persistent views for restart survival**: In the VerificationView, use a pattern where buttons have dynamic `custom_id` containing the member ID. For restart persistence, register the view class in `bot.py`'s `setup_hook`. Discord.py's `Bot.add_view()` with a view that has `timeout=None` and known `custom_id` patterns will reconnect button callbacks after restart. Use `@discord.ui.button(custom_id="verify:approve")` as a base — but since each message has a unique member_id in the custom_id, you need `discord.ui.DynamicItem` or a persistent view that handles the routing. The pragmatic approach: override `View.interaction_check` to parse member_id from the custom_id, or just use simple buttons with `custom_id` containing member_id and a single registered view.

3. **Modify `bot/bot.py`**: In `setup_hook()`, after loading cogs:
   - `await self.load_extension("bot.cogs.verification")` 
   - Register the persistent view: `self.add_view(VerificationView())` so buttons survive restarts.

4. **Ensure logging throughout**: Use `logging.getLogger(__name__)` in the cog. Log: member join events with guild_id/member_id, config not found (warning), approve/deny actions (info), permission errors (error), member-not-found during approve/deny (warning).

5. **Action log entries**: On approve, insert into `action_log`: `action_type="member_approved"`, `user_id=admin_id`, `target=str(member_id)`, `details=f"Approved by {admin}"`. On deny, same with `action_type="member_denied"`.

## Must-Haves

- [ ] `VerificationView` uses `timeout=None` and explicit `custom_id` on buttons for restart persistence
- [ ] `on_member_join` assigns unverified role and sends embed with buttons to verify channel
- [ ] Approve button removes unverified role, logs to `action_log`
- [ ] Deny button kicks member, logs to `action_log`
- [ ] `/verify-setup` creates role + channel, saves to `guild_config`, validates role hierarchy
- [ ] Error handling for `discord.NotFound` (member left) and `discord.Forbidden` (bad permissions)
- [ ] Cog loaded in `bot.py` setup_hook, persistent view registered

## Verification

- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.cogs.verification import VerificationCog, VerificationView; print('imports ok')"`
- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M001 && .venv/bin/python -c "from bot.bot import DiscordBot; print('bot imports ok')"`
- `grep -q "bot.cogs.verification" bot/bot.py` — cog is loaded
- `grep -q "add_view" bot/bot.py` — persistent view is registered

## Observability Impact

- Signals added/changed: Structured log entries for member join, approve, deny, permission errors, config-not-found
- How a future agent inspects this: `action_log` table rows with `action_type` in `("member_approved", "member_denied")`; grep logs for `verification` logger
- Failure state exposed: Permission errors include guild_id and member_id; `discord.Forbidden` and `discord.NotFound` caught and logged with context

## Inputs

- `bot/cogs/ping.py` — cog pattern to follow (Cog subclass, module-level setup function)
- `bot/bot.py` — setup_hook to modify (add cog loading + persistent view registration)
- `bot/models.py` — GuildConfig and ActionLog dataclasses (read-only reference, already has verify_channel_id and verify_role_id fields)
- `bot/database.py` — DatabaseManager API (execute, fetchone, fetchall)
- `migrations/001_initial.sql` — schema reference (guild_config and action_log tables)

## Expected Output

- `bot/cogs/verification.py` — new file containing VerificationCog and VerificationView
- `bot/bot.py` — modified to load verification cog and register persistent view
