"""Tests for the verification gate cog."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import pytest
from discord.ext import commands

from bot.cogs.verification import (
    ApproveButton,
    DenyButton,
    VerificationCog,
    make_verification_view,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bot(db: AsyncMock | None = None) -> MagicMock:
    """Create a mock bot with a db attribute."""
    bot = MagicMock(spec=commands.Bot)
    bot.db = db or AsyncMock()
    return bot


def _make_mock_guild(
    guild_id: int = 100,
    *,
    has_role: bool = True,
    has_channel: bool = True,
    bot_role_position: int = 10,
    unverified_role_position: int = 1,
) -> MagicMock:
    """Build a mock guild with optional role/channel presence."""
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id

    role = MagicMock(spec=discord.Role)
    role.id = 200
    role.name = "Unverified"
    role.mention = "<@&200>"
    role.position = unverified_role_position
    # Support comparison operators for role hierarchy checks
    role.__le__ = lambda self, other: self.position <= other.position
    role.__lt__ = lambda self, other: self.position < other.position
    role.__ge__ = lambda self, other: self.position >= other.position
    role.__gt__ = lambda self, other: self.position > other.position

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 300
    channel.name = "verify"
    channel.mention = "<#300>"
    channel.send = AsyncMock()

    guild.get_role = MagicMock(return_value=role if has_role else None)
    guild.get_channel = MagicMock(return_value=channel if has_channel else None)

    bot_member = MagicMock(spec=discord.Member)
    bot_top_role = MagicMock(spec=discord.Role)
    bot_top_role.position = bot_role_position
    bot_top_role.__le__ = lambda self, other: self.position <= other.position
    bot_top_role.__lt__ = lambda self, other: self.position < other.position
    bot_top_role.__ge__ = lambda self, other: self.position >= other.position
    bot_top_role.__gt__ = lambda self, other: self.position > other.position
    type(bot_member).top_role = PropertyMock(return_value=bot_top_role)
    guild.me = bot_member

    guild.default_role = MagicMock(spec=discord.Role)
    guild.roles = [role]
    guild.text_channels = [channel] if has_channel else []

    guild.create_role = AsyncMock(return_value=role)
    guild.create_text_channel = AsyncMock(return_value=channel)

    return guild


def _make_mock_member(
    member_id: int = 999,
    guild: MagicMock | None = None,
) -> MagicMock:
    """Build a mock member."""
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.guild = guild or _make_mock_guild()
    member.display_avatar = MagicMock()
    member.display_avatar.url = "https://cdn.discordapp.com/avatars/999/test.png"
    member.created_at = discord.utils.utcnow()
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.kick = AsyncMock()
    member.__str__ = lambda self: "TestUser#0000"
    return member


def _make_mock_interaction(
    *,
    guild: MagicMock | None = None,
    user_is_admin: bool = True,
    user_id: int = 500,
    bot: MagicMock | None = None,
) -> MagicMock:
    """Build a mock interaction for button callbacks."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = guild or _make_mock_guild()

    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.mention = f"<@{user_id}>"
    user.__str__ = lambda self: "Admin#0001"
    perms = MagicMock(spec=discord.Permissions)
    perms.administrator = user_is_admin
    type(user).guild_permissions = PropertyMock(return_value=perms)
    interaction.user = user

    interaction.response = AsyncMock()
    interaction.client = bot or _make_mock_bot()

    return interaction


# ---------------------------------------------------------------------------
# VerificationCog — on_member_join
# ---------------------------------------------------------------------------


class TestOnMemberJoin:
    """on_member_join assigns the unverified role and sends an embed."""

    async def test_assigns_role_and_sends_embed(self) -> None:
        """When config is present, the member gets the role and an embed is sent."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": 200, "verify_channel_id": 300}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()
        member = _make_mock_member(guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        member.add_roles.assert_awaited_once()
        channel = guild.get_channel(300)
        channel.send.assert_awaited_once()

        # Verify embed and view are sent
        call_kwargs = channel.send.call_args
        assert "embed" in call_kwargs.kwargs
        assert "view" in call_kwargs.kwargs

    async def test_skips_when_not_configured(self) -> None:
        """When guild_config has no verify fields, nothing happens."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()
        member = _make_mock_member(guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()

    async def test_skips_when_role_missing(self) -> None:
        """If the role was deleted after config was saved, skip gracefully."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": 200, "verify_channel_id": 300}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild(has_role=False)
        member = _make_mock_member(guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()

    async def test_skips_when_channel_missing(self) -> None:
        """If the verify channel was deleted after config was saved, skip."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": 200, "verify_channel_id": 300}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild(has_channel=False)
        member = _make_mock_member(guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()

    async def test_skips_when_config_has_null_fields(self) -> None:
        """If config row exists but verify fields are None, skip."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": None, "verify_channel_id": None}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()
        member = _make_mock_member(guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()

    async def test_sends_embed_with_member_info(self) -> None:
        """The embed sent to the verify channel contains member details."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": 200, "verify_channel_id": 300}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)

        cog = VerificationCog(bot)
        await cog.on_member_join(member)

        channel = guild.get_channel(300)
        call_kwargs = channel.send.call_args.kwargs
        embed: discord.Embed = call_kwargs["embed"]
        assert embed.title == "New Member Verification"
        assert "<@999>" in embed.description
        # Embed should have Member ID field
        field_names = [f.name for f in embed.fields]
        assert "Member ID" in field_names

    async def test_handles_permission_error_on_role_add(self) -> None:
        """If the bot can't add the role, it logs the error and returns."""
        db = AsyncMock()
        db.fetchone = AsyncMock(
            return_value={"verify_role_id": 200, "verify_channel_id": 300}
        )
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()
        member = _make_mock_member(guild=guild)
        member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "missing perms"))

        cog = VerificationCog(bot)
        # Should not raise
        await cog.on_member_join(member)

        channel = guild.get_channel(300)
        channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# ApproveButton
# ---------------------------------------------------------------------------


class TestApproveButton:
    """Approve button removes the unverified role and logs the action."""

    async def test_approve_removes_role_and_logs(self) -> None:
        """Clicking approve removes the unverified role and writes to action_log."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"verify_role_id": 200})
        bot = _make_mock_bot(db)

        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        member.remove_roles.assert_awaited_once()
        db.execute.assert_awaited_once()
        # Check action_type
        call_args = db.execute.call_args
        assert "member_approved" in call_args[0][1]

        interaction.response.edit_message.assert_awaited_once()

    async def test_approve_non_admin_rejected(self) -> None:
        """Non-admin clicks are rejected with an ephemeral message."""
        interaction = _make_mock_interaction(user_is_admin=False)
        button = ApproveButton(member_id=999)

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_approve_member_left(self) -> None:
        """If the member left before approval, show a warning."""
        guild = _make_mock_guild()
        guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), "not found")
        )
        db = AsyncMock()
        bot = _make_mock_bot(db)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        call_kwargs = interaction.response.edit_message.call_args
        assert "left" in call_kwargs.kwargs["content"].lower()

    async def test_approve_forbidden_on_role_remove(self) -> None:
        """If the bot can't remove the role, report the error."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        member.remove_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "missing perms")
        )
        guild.fetch_member = AsyncMock(return_value=member)
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"verify_role_id": 200})
        bot = _make_mock_bot(db)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_approve_config_not_found(self) -> None:
        """If guild config is missing when approve is clicked, reject."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_mock_bot(db)
        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
        member.remove_roles.assert_not_awaited()

    async def test_approve_role_deleted(self) -> None:
        """If the unverified role was deleted, inform the admin."""
        guild = _make_mock_guild()
        guild.get_role = MagicMock(return_value=None)
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"verify_role_id": 200})
        bot = _make_mock_bot(db)
        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        content = interaction.response.send_message.call_args[0][0]
        assert "no longer exists" in content.lower()

    async def test_approve_edits_message_with_approval_content(self) -> None:
        """Approval message shows who was approved and by whom."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"verify_role_id": 200})
        bot = _make_mock_bot(db)
        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot, user_id=500)

        button = ApproveButton(member_id=999)
        await button.callback(interaction)

        call_kwargs = interaction.response.edit_message.call_args.kwargs
        assert "✅" in call_kwargs["content"]
        assert "<@999>" in call_kwargs["content"]
        assert call_kwargs["view"] is None  # Buttons removed


# ---------------------------------------------------------------------------
# DenyButton
# ---------------------------------------------------------------------------


class TestDenyButton:
    """Deny button kicks the member and logs the action."""

    async def test_deny_kicks_and_logs(self) -> None:
        """Clicking deny kicks the member and writes to action_log."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        bot = _make_mock_bot(db)

        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = DenyButton(member_id=999)
        await button.callback(interaction)

        member.kick.assert_awaited_once()
        db.execute.assert_awaited_once()
        call_args = db.execute.call_args
        assert "member_denied" in call_args[0][1]

        interaction.response.edit_message.assert_awaited_once()

    async def test_deny_non_admin_rejected(self) -> None:
        """Non-admin clicks are rejected."""
        interaction = _make_mock_interaction(user_is_admin=False)
        button = DenyButton(member_id=999)

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_deny_member_left(self) -> None:
        """If the member left before denial, show a warning."""
        guild = _make_mock_guild()
        guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), "not found")
        )
        db = AsyncMock()
        bot = _make_mock_bot(db)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = DenyButton(member_id=999)
        await button.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        call_kwargs = interaction.response.edit_message.call_args
        assert "left" in call_kwargs.kwargs["content"].lower()

    async def test_deny_forbidden_on_kick(self) -> None:
        """If the bot can't kick, report the error."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        member.kick = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "missing perms")
        )
        guild.fetch_member = AsyncMock(return_value=member)
        db = AsyncMock()
        bot = _make_mock_bot(db)
        interaction = _make_mock_interaction(guild=guild, bot=bot)

        button = DenyButton(member_id=999)
        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_deny_edits_message_with_denial_content(self) -> None:
        """Denial message shows who was denied and by whom."""
        guild = _make_mock_guild()
        member = _make_mock_member(member_id=999, guild=guild)
        db = AsyncMock()
        bot = _make_mock_bot(db)
        guild.fetch_member = AsyncMock(return_value=member)
        interaction = _make_mock_interaction(guild=guild, bot=bot, user_id=500)

        button = DenyButton(member_id=999)
        await button.callback(interaction)

        call_kwargs = interaction.response.edit_message.call_args.kwargs
        assert "🚫" in call_kwargs["content"]
        assert "<@999>" in call_kwargs["content"]
        assert call_kwargs["view"] is None  # Buttons removed


# ---------------------------------------------------------------------------
# Setup command
# ---------------------------------------------------------------------------


class TestVerifySetup:
    """verify-setup creates role, channel, and persists config."""

    async def test_creates_role_and_channel(self) -> None:
        """When neither exist, both are created and config is saved."""
        db = AsyncMock()
        bot = _make_mock_bot(db)
        guild = _make_mock_guild(has_role=False, has_channel=False)
        guild.text_channels = []

        # get_role returns None for nonexistent roles, get by name also returns None
        with patch("discord.utils.get", side_effect=[None, None]):
            cog = VerificationCog(bot)
            ctx = AsyncMock(spec=commands.Context)
            ctx.guild = guild
            ctx.send = AsyncMock()

            await cog.verify_setup.callback(cog, ctx)

        guild.create_role.assert_awaited_once()
        guild.create_text_channel.assert_awaited_once()
        db.execute.assert_awaited_once()
        ctx.send.assert_awaited_once()

    async def test_reuses_existing_role_and_channel(self) -> None:
        """When both already exist, they are reused."""
        db = AsyncMock()
        bot = _make_mock_bot(db)
        guild = _make_mock_guild()

        role = MagicMock(spec=discord.Role)
        role.id = 200
        role.name = "Unverified"
        role.mention = "<@&200>"
        role.position = 1
        role.__le__ = lambda self, other: self.position <= other.position

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 300
        channel.name = "verify"
        channel.mention = "<#300>"

        with patch("discord.utils.get", side_effect=[role, channel]):
            cog = VerificationCog(bot)
            ctx = AsyncMock(spec=commands.Context)
            ctx.guild = guild
            ctx.send = AsyncMock()

            await cog.verify_setup.callback(cog, ctx)

        guild.create_role.assert_not_awaited()
        guild.create_text_channel.assert_not_awaited()
        db.execute.assert_awaited_once()

    async def test_warns_on_role_hierarchy(self) -> None:
        """If the bot's role is below the unverified role, warn and abort."""
        db = AsyncMock()
        bot = _make_mock_bot(db)
        guild = _make_mock_guild(bot_role_position=1, unverified_role_position=10)

        role = MagicMock(spec=discord.Role)
        role.id = 200
        role.name = "Unverified"
        role.position = 10
        role.__le__ = lambda self, other: self.position <= other.position
        role.__ge__ = lambda self, other: self.position >= other.position

        with patch("discord.utils.get", side_effect=[role]):
            cog = VerificationCog(bot)
            ctx = AsyncMock(spec=commands.Context)
            ctx.guild = guild
            ctx.send = AsyncMock()

            await cog.verify_setup.callback(cog, ctx)

        # Should warn but NOT create channel or save config
        db.execute.assert_not_awaited()

    async def test_saves_config_with_correct_ids(self) -> None:
        """Config is persisted with the correct role and channel IDs."""
        db = AsyncMock()
        bot = _make_mock_bot(db)
        guild = _make_mock_guild(has_role=False, has_channel=False)
        guild.text_channels = []

        role = MagicMock(spec=discord.Role)
        role.id = 777
        role.name = "Unverified"
        role.mention = "<@&777>"
        role.position = 1
        role.__le__ = lambda self, other: self.position <= other.position
        guild.create_role = AsyncMock(return_value=role)

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 888
        channel.name = "verify"
        channel.mention = "<#888>"
        guild.create_text_channel = AsyncMock(return_value=channel)

        with patch("discord.utils.get", side_effect=[None, None]):
            cog = VerificationCog(bot)
            ctx = AsyncMock(spec=commands.Context)
            ctx.guild = guild
            ctx.send = AsyncMock()

            await cog.verify_setup.callback(cog, ctx)

        # Verify the SQL parameters contain the correct IDs
        call_args = db.execute.call_args[0]
        assert guild.id in call_args[1]
        assert 777 in call_args[1]
        assert 888 in call_args[1]

    def test_verify_setup_has_admin_check(self) -> None:
        """The verify-setup command requires administrator permission."""
        bot = _make_mock_bot()
        cog = VerificationCog(bot)
        cmd = cog.verify_setup
        # has_permissions adds a check to the command's checks list
        check_names = [c.__qualname__ for c in cmd.checks]
        assert any("has_permissions" in name for name in check_names)

    def test_verify_setup_is_guild_only(self) -> None:
        """The verify-setup command can only be used in a guild."""
        bot = _make_mock_bot()
        cog = VerificationCog(bot)
        cmd = cog.verify_setup
        check_names = [c.__qualname__ for name in cmd.checks for c in [name]]
        # guild_only decorator also adds a check
        assert any("guild_only" in c.__qualname__ for c in cmd.checks)


# ---------------------------------------------------------------------------
# Persistent view / DynamicItem registration
# ---------------------------------------------------------------------------


class TestPersistentViewRegistration:
    """Persistent views are registered in setup_hook via add_dynamic_items."""

    def test_approve_button_custom_id_pattern(self) -> None:
        """ApproveButton custom_id matches the expected pattern."""
        btn = ApproveButton(member_id=12345)
        assert btn.custom_id == "verify:approve:12345"
        # Template should match
        assert re.match(r"verify:approve:(?P<member_id>\d+)", btn.custom_id)

    def test_deny_button_custom_id_pattern(self) -> None:
        """DenyButton custom_id matches the expected pattern."""
        btn = DenyButton(member_id=67890)
        assert btn.custom_id == "verify:deny:67890"
        assert re.match(r"verify:deny:(?P<member_id>\d+)", btn.custom_id)

    def test_make_verification_view_timeout(self) -> None:
        """The view has no timeout (persistent)."""
        view = make_verification_view(42)
        assert view.timeout is None

    def test_make_verification_view_has_two_items(self) -> None:
        """The view contains an approve and deny button."""
        view = make_verification_view(42)
        assert len(view.children) == 2

    def test_setup_hook_registers_dynamic_items(self) -> None:
        """bot.py registers ApproveButton and DenyButton as dynamic items."""
        import bot.bot as bot_module
        import inspect

        source = inspect.getsource(bot_module.DiscordBot.setup_hook)
        assert "add_dynamic_items" in source
        assert "ApproveButton" in source
        assert "DenyButton" in source


class TestCogSetup:
    """Module-level setup function adds the cog to the bot."""

    async def test_setup_adds_cog(self) -> None:
        """The setup() function calls bot.add_cog with a VerificationCog."""
        from bot.cogs.verification import setup

        bot = AsyncMock(spec=commands.Bot)
        await setup(bot)
        bot.add_cog.assert_awaited_once()
        added_cog = bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, VerificationCog)
