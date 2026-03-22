"""Verification gate cog — gates new members behind admin approval."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import Interaction
from discord.ext import commands

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dynamic items — persistent buttons that survive bot restarts
# ---------------------------------------------------------------------------


class ApproveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"verify:approve:(?P<member_id>\d+)",
):
    """Green approve button. ``custom_id`` encodes the target member ID."""

    def __init__(self, member_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Approve",
                style=discord.ButtonStyle.green,
                custom_id=f"verify:approve:{member_id}",
            )
        )
        self.member_id = member_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ApproveButton:
        member_id = int(match.group("member_id"))
        return cls(member_id)

    async def callback(self, interaction: Interaction) -> None:
        """Remove the unverified role and log the approval."""
        assert interaction.guild is not None
        assert interaction.user is not None

        # Admin-only guard
        if not interaction.user.guild_permissions.administrator:  # type: ignore[union-attr]
            await interaction.response.send_message(
                "Only administrators can approve members.", ephemeral=True
            )
            return

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        db = bot.db  # type: ignore[attr-defined]
        guild = interaction.guild

        try:
            member = await guild.fetch_member(self.member_id)
        except discord.NotFound:
            log.warning(
                "Approve failed — member left: guild_id=%s member_id=%s",
                guild.id,
                self.member_id,
            )
            await interaction.response.edit_message(
                content=f"⚠️ Member (<@{self.member_id}>) has already left the server.",
                view=None,
            )
            return

        # Fetch config to get the unverified role
        row = await db.fetchone(
            "SELECT verify_role_id FROM guild_config WHERE guild_id = ?",
            (guild.id,),
        )
        if not row or not row["verify_role_id"]:
            await interaction.response.send_message(
                "Verification is not configured for this server.", ephemeral=True
            )
            return

        role = guild.get_role(row["verify_role_id"])
        if role is None:
            await interaction.response.send_message(
                "The unverified role no longer exists. Re-run `/verify-setup`.",
                ephemeral=True,
            )
            return

        try:
            await member.remove_roles(role, reason=f"Approved by {interaction.user}")
        except discord.Forbidden:
            log.error(
                "Missing permissions to remove role: guild_id=%s member_id=%s role_id=%s",
                guild.id,
                self.member_id,
                role.id,
            )
            await interaction.response.send_message(
                "❌ I don't have permission to manage that role.", ephemeral=True
            )
            return

        # Log to action_log
        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild.id,
                interaction.user.id,
                "member_approved",
                str(self.member_id),
                f"Approved by {interaction.user} ({interaction.user.id})",
            ),
        )

        log.info(
            "Member approved: guild_id=%s member_id=%s approved_by=%s",
            guild.id,
            self.member_id,
            interaction.user.id,
        )

        await interaction.response.edit_message(
            content=f"✅ {member.mention} has been approved by {interaction.user.mention}.",
            view=None,
        )


class DenyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"verify:deny:(?P<member_id>\d+)",
):
    """Red deny button. ``custom_id`` encodes the target member ID."""

    def __init__(self, member_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Deny",
                style=discord.ButtonStyle.red,
                custom_id=f"verify:deny:{member_id}",
            )
        )
        self.member_id = member_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DenyButton:
        member_id = int(match.group("member_id"))
        return cls(member_id)

    async def callback(self, interaction: Interaction) -> None:
        """Kick the member and log the denial."""
        assert interaction.guild is not None
        assert interaction.user is not None

        if not interaction.user.guild_permissions.administrator:  # type: ignore[union-attr]
            await interaction.response.send_message(
                "Only administrators can deny members.", ephemeral=True
            )
            return

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        db = bot.db  # type: ignore[attr-defined]
        guild = interaction.guild

        try:
            member = await guild.fetch_member(self.member_id)
        except discord.NotFound:
            log.warning(
                "Deny failed — member left: guild_id=%s member_id=%s",
                guild.id,
                self.member_id,
            )
            await interaction.response.edit_message(
                content=f"⚠️ Member (<@{self.member_id}>) has already left the server.",
                view=None,
            )
            return

        try:
            await member.kick(reason=f"Denied by {interaction.user}")
        except discord.Forbidden:
            log.error(
                "Missing permissions to kick member: guild_id=%s member_id=%s",
                guild.id,
                self.member_id,
            )
            await interaction.response.send_message(
                "❌ I don't have permission to kick that member.", ephemeral=True
            )
            return

        # Log to action_log
        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild.id,
                interaction.user.id,
                "member_denied",
                str(self.member_id),
                f"Denied by {interaction.user} ({interaction.user.id})",
            ),
        )

        log.info(
            "Member denied: guild_id=%s member_id=%s denied_by=%s",
            guild.id,
            self.member_id,
            interaction.user.id,
        )

        await interaction.response.edit_message(
            content=f"🚫 {member.mention} has been denied and kicked by {interaction.user.mention}.",
            view=None,
        )


# ---------------------------------------------------------------------------
# View factory — builds a view with both buttons for a specific member
# ---------------------------------------------------------------------------


def make_verification_view(member_id: int) -> discord.ui.View:
    """Create a persistent View with Approve/Deny buttons for *member_id*."""
    view = discord.ui.View(timeout=None)
    view.add_item(ApproveButton(member_id))
    view.add_item(DenyButton(member_id))
    return view


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class VerificationCog(commands.Cog):
    """Gates new members behind an admin-approved verification flow."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Events -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Assign the unverified role and post an approval embed."""
        guild = member.guild
        db = self.bot.db  # type: ignore[attr-defined]

        row = await db.fetchone(
            "SELECT verify_role_id, verify_channel_id FROM guild_config WHERE guild_id = ?",
            (guild.id,),
        )
        if not row or not row["verify_role_id"] or not row["verify_channel_id"]:
            log.warning(
                "Verification not configured, skipping: guild_id=%s", guild.id
            )
            return

        role = guild.get_role(row["verify_role_id"])
        channel = guild.get_channel(row["verify_channel_id"])

        if role is None or channel is None:
            log.warning(
                "Verify role or channel missing: guild_id=%s role_id=%s channel_id=%s",
                guild.id,
                row["verify_role_id"],
                row["verify_channel_id"],
            )
            return

        # Assign the unverified role
        try:
            await member.add_roles(role, reason="New member — pending verification")
        except discord.Forbidden:
            log.error(
                "Cannot assign unverified role: guild_id=%s member_id=%s role_id=%s",
                guild.id,
                member.id,
                role.id,
            )
            return

        log.info(
            "Member joined — assigned unverified role: guild_id=%s member_id=%s",
            guild.id,
            member.id,
        )

        # Build approval embed
        embed = discord.Embed(
            title="New Member Verification",
            description=f"{member.mention} ({member}) has joined the server.",
            colour=discord.Colour.gold(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.add_field(name="Member ID", value=str(member.id))

        view = make_verification_view(member.id)

        assert isinstance(channel, discord.TextChannel)
        await channel.send(embed=embed, view=view)

    # -- Commands -----------------------------------------------------------

    @commands.hybrid_command(
        name="verify-setup",
        description="Set up the member verification gate for this server",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def verify_setup(self, ctx: commands.Context) -> None:
        """Create (or reuse) the unverified role and verification channel."""
        assert ctx.guild is not None
        guild = ctx.guild
        db = self.bot.db  # type: ignore[attr-defined]
        bot_member = guild.me

        # -- Role -----------------------------------------------------------
        role = discord.utils.get(guild.roles, name="Unverified")
        if role is None:
            role = await guild.create_role(
                name="Unverified",
                permissions=discord.Permissions.none(),
                reason="Verification gate setup",
            )
            log.info("Created Unverified role: guild_id=%s role_id=%s", guild.id, role.id)
        else:
            log.info("Reusing existing Unverified role: guild_id=%s role_id=%s", guild.id, role.id)

        # Validate hierarchy — bot's top role must be above the unverified role
        if bot_member.top_role <= role:
            await ctx.send(
                "⚠️ My highest role is not above the **Unverified** role. "
                "Please move my role higher in Server Settings → Roles so I can manage it.",
                ephemeral=True,
            )
            return

        # -- Channel --------------------------------------------------------
        channel = discord.utils.get(guild.text_channels, name="verify")
        if channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    embed_links=True,
                ),
            }
            channel = await guild.create_text_channel(
                "verify",
                overwrites=overwrites,
                reason="Verification gate setup",
            )
            log.info("Created #verify channel: guild_id=%s channel_id=%s", guild.id, channel.id)
        else:
            log.info("Reusing existing #verify channel: guild_id=%s channel_id=%s", guild.id, channel.id)

        # -- Persist config -------------------------------------------------
        await db.execute(
            "INSERT INTO guild_config (guild_id, verify_role_id, verify_channel_id) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET verify_role_id = excluded.verify_role_id, "
            "verify_channel_id = excluded.verify_channel_id",
            (guild.id, role.id, channel.id),
        )

        log.info(
            "Verification config saved: guild_id=%s role_id=%s channel_id=%s",
            guild.id,
            role.id,
            channel.id,
        )

        await ctx.send(
            f"✅ Verification gate configured!\n"
            f"• Role: {role.mention}\n"
            f"• Channel: {channel.mention}\n\n"
            f"New members will be assigned **{role.name}** and prompted for approval in {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the VerificationCog into the bot (called by load_extension)."""
    await bot.add_cog(VerificationCog(bot))
