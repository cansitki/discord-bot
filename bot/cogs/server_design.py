"""Server design cog — conversational server layout via Claude tool use.

Users describe a Discord server layout in natural language.  Claude
proposes a structured design (roles, categories, channels) via the
``propose_server_design`` tool.  The proposal is rendered as a rich
embed with Approve / Cancel buttons (DynamicItem pattern — survives
bot restarts).  On approval the bot creates all resources sequentially:
roles → categories → channels, with permission overwrites.

Pending proposals are stored in-memory — they are short-lived and
acceptable to lose on restart (users simply re-request).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import Interaction
from discord.ext import commands

log = logging.getLogger("server_design")

# ---------------------------------------------------------------------------
# Tool schema — Anthropic tool format for Claude
# ---------------------------------------------------------------------------

PROPOSE_TOOL: dict[str, Any] = {
    "name": "propose_server_design",
    "description": (
        "Propose a Discord server layout.  Returns a structured design with "
        "roles, categories, and channels.  The user will see the proposal as "
        "an embed and can approve or cancel it.  Only call this tool when the "
        "user explicitly asks to create or set up server structure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A short summary of the proposed server design.",
            },
            "roles": {
                "type": "array",
                "description": "Roles to create.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Role name.",
                        },
                        "color": {
                            "type": "string",
                            "description": "Hex color code (e.g. '#FF5733').",
                        },
                        "permissions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Permission names to enable for this role.",
                        },
                    },
                    "required": ["name"],
                },
            },
            "categories": {
                "type": "array",
                "description": "Categories, each containing channels.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Category name.",
                        },
                        "channels": {
                            "type": "array",
                            "description": "Channels in this category.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Channel name.",
                                    },
                                    "type": {
                                        "type": "string",
                                        "enum": ["text", "voice"],
                                        "description": "Channel type.",
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Channel topic (text channels only).",
                                    },
                                },
                                "required": ["name", "type"],
                            },
                        },
                        "role_access": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Role names that should have access to this "
                                "category.  If empty, all roles can see it."
                            ),
                        },
                    },
                    "required": ["name", "channels"],
                },
            },
        },
        "required": ["summary", "categories"],
    },
}

# ---------------------------------------------------------------------------
# System prompt fragment for Claude (appended when tools are active)
# ---------------------------------------------------------------------------

DESIGN_SYSTEM_PROMPT = (
    "When the user asks you to set up, design, or create a server layout, "
    "use the propose_server_design tool.  Build a comprehensive but sensible "
    "design based on the user's description.  Include appropriate roles, "
    "categories, and channels.  Use text channels for discussion and voice "
    "channels for real-time communication.  Keep channel names lowercase "
    "with hyphens (e.g. 'general-chat').  Do NOT create the resources "
    "yourself — the tool will present the proposal for the user to approve."
)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def sanitize_channel_name(name: str) -> str:
    """Sanitise a string into a valid Discord channel name.

    Rules applied:
    - lowercase
    - spaces → hyphens
    - strip characters that are not alphanumeric, hyphens, or underscores
    - collapse consecutive hyphens
    - strip leading/trailing hyphens
    - clamp to 1–100 characters
    - fallback to ``"channel"`` if result is empty
    """
    result = name.lower()
    result = result.replace(" ", "-")
    result = re.sub(r"[^a-z0-9\-_]", "", result)
    result = re.sub(r"-{2,}", "-", result)
    result = result.strip("-")
    result = result[:100]
    return result if result else "channel"


def validate_proposal(proposal: dict[str, Any]) -> list[str]:
    """Validate a proposal dict, returning a list of error messages.

    An empty list means the proposal is valid.
    """
    errors: list[str] = []

    if not isinstance(proposal, dict):
        return ["Proposal must be a dictionary."]

    if "summary" not in proposal or not proposal["summary"]:
        errors.append("Missing or empty 'summary'.")

    if "categories" not in proposal:
        errors.append("Missing 'categories'.")
    elif not isinstance(proposal["categories"], list):
        errors.append("'categories' must be a list.")
    elif len(proposal["categories"]) == 0:
        errors.append("'categories' must contain at least one category.")
    else:
        for i, cat in enumerate(proposal["categories"]):
            if not isinstance(cat, dict):
                errors.append(f"Category {i} must be a dictionary.")
                continue
            if "name" not in cat or not cat["name"]:
                errors.append(f"Category {i} missing 'name'.")
            if "channels" not in cat:
                errors.append(f"Category {i} missing 'channels'.")
            elif not isinstance(cat["channels"], list):
                errors.append(f"Category {i} 'channels' must be a list.")
            else:
                for j, ch in enumerate(cat["channels"]):
                    if not isinstance(ch, dict):
                        errors.append(f"Category {i} channel {j} must be a dictionary.")
                        continue
                    if "name" not in ch or not ch["name"]:
                        errors.append(f"Category {i} channel {j} missing 'name'.")
                    if "type" not in ch:
                        errors.append(f"Category {i} channel {j} missing 'type'.")
                    elif ch["type"] not in ("text", "voice"):
                        errors.append(
                            f"Category {i} channel {j} type must be 'text' or 'voice', "
                            f"got '{ch['type']}'."
                        )

    # Roles are optional but must be a list if present
    if "roles" in proposal and not isinstance(proposal.get("roles"), list):
        errors.append("'roles' must be a list.")

    return errors


# ---------------------------------------------------------------------------
# Embed rendering
# ---------------------------------------------------------------------------

_EMBED_CHAR_LIMIT = 6000
_CHANNEL_ICON = {"text": "💬", "voice": "🔊"}


def render_proposal_embed(proposal: dict[str, Any]) -> discord.Embed:
    """Build a Discord Embed visualising the server design proposal.

    Handles the 6000-char embed limit by truncating with
    ``"... and N more"`` when necessary.
    """
    embed = discord.Embed(
        title="📐 Server Design Proposal",
        description=proposal.get("summary", ""),
        colour=discord.Colour.gold(),
    )

    # -- Roles field -------------------------------------------------------
    roles = proposal.get("roles", [])
    if roles:
        role_lines: list[str] = []
        for role in roles:
            color_str = f" ({role['color']})" if role.get("color") else ""
            role_lines.append(f"• **{role['name']}**{color_str}")
        roles_text = "\n".join(role_lines)
        embed.add_field(name="Roles", value=roles_text[:1024], inline=False)

    # -- Categories + channels fields --------------------------------------
    categories = proposal.get("categories", [])
    chars_used = len(embed.title or "") + len(embed.description or "")
    for field in embed.fields:
        chars_used += len(field.name) + len(field.value)  # type: ignore[arg-type]

    for cat in categories:
        cat_name = cat.get("name", "Unnamed")
        channels = cat.get("channels", [])
        channel_lines: list[str] = []
        omitted = 0

        for ch in channels:
            icon = _CHANNEL_ICON.get(ch.get("type", "text"), "💬")
            line = f"{icon} {ch.get('name', 'unnamed')}"
            # Check if adding this line would exceed the limit
            candidate_text = "\n".join(channel_lines + [line])
            field_chars = len(cat_name) + len(candidate_text)
            if chars_used + field_chars > _EMBED_CHAR_LIMIT - 200:
                omitted = len(channels) - len(channel_lines)
                break
            channel_lines.append(line)

        if omitted > 0:
            channel_lines.append(f"*... and {omitted} more*")

        field_value = "\n".join(channel_lines) if channel_lines else "*empty*"

        # Role access note
        role_access = cat.get("role_access", [])
        if role_access:
            access_str = ", ".join(role_access)
            field_value += f"\n🔒 Access: {access_str}"

        embed.add_field(
            name=f"📁 {cat_name}",
            value=field_value[:1024],
            inline=False,
        )
        chars_used += len(cat_name) + len(field_value[:1024])

    embed.set_footer(text="Click Approve to build this layout, or Cancel to discard.")
    return embed


# ---------------------------------------------------------------------------
# DynamicItem buttons — survive bot restarts
# ---------------------------------------------------------------------------


class DesignApproveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"design:approve:(?P<guild_id>\d+):(?P<msg_id>\d+)",
):
    """Green approve button for server design proposals."""

    def __init__(self, guild_id: int, msg_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Approve",
                style=discord.ButtonStyle.green,
                custom_id=f"design:approve:{guild_id}:{msg_id}",
            )
        )
        self.guild_id = guild_id
        self.msg_id = msg_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DesignApproveButton:
        guild_id = int(match.group("guild_id"))
        msg_id = int(match.group("msg_id"))
        return cls(guild_id, msg_id)

    async def callback(self, interaction: Interaction) -> None:
        """Validate permissions, look up proposal, and start the build."""
        assert interaction.guild is not None
        assert interaction.user is not None

        # Admin-only guard
        if not interaction.user.guild_permissions.administrator:  # type: ignore[union-attr]
            await interaction.response.send_message(
                "Only administrators can approve server designs.",
                ephemeral=True,
            )
            return

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        cog: ServerDesignCog | None = bot.get_cog("ServerDesignCog")  # type: ignore[assignment]

        if cog is None:
            await interaction.response.send_message(
                "Server design system is not available.", ephemeral=True
            )
            return

        key = (self.guild_id, self.msg_id)
        proposal = cog._pending_proposals.get(key)

        if proposal is None:
            await interaction.response.send_message(
                "This proposal has expired or was already processed. "
                "Please request a new design.",
                ephemeral=True,
            )
            return

        # Remove from pending
        del cog._pending_proposals[key]

        # Acknowledge — build may take a while
        await interaction.response.edit_message(
            content="⏳ Building server layout... please wait.",
            view=None,
        )

        log.info(
            "server_design.approve: guild_id=%s user_id=%s msg_id=%s",
            self.guild_id,
            interaction.user.id,
            self.msg_id,
        )

        # Log approval to action_log
        db = bot.db  # type: ignore[attr-defined]
        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self.guild_id,
                interaction.user.id,
                "server_design_approved",
                str(self.msg_id),
                f"Approved by {interaction.user} ({interaction.user.id})",
            ),
        )

        # Execute the build
        result = await cog._execute_build(
            interaction.guild, proposal, interaction.user.id
        )

        # Edit the original message with the result
        await interaction.edit_original_response(content=result)


class DesignCancelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"design:cancel:(?P<guild_id>\d+):(?P<msg_id>\d+)",
):
    """Red cancel button for server design proposals."""

    def __init__(self, guild_id: int, msg_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.red,
                custom_id=f"design:cancel:{guild_id}:{msg_id}",
            )
        )
        self.guild_id = guild_id
        self.msg_id = msg_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DesignCancelButton:
        guild_id = int(match.group("guild_id"))
        msg_id = int(match.group("msg_id"))
        return cls(guild_id, msg_id)

    async def callback(self, interaction: Interaction) -> None:
        """Remove the proposal from pending and update the message."""
        assert interaction.guild is not None
        assert interaction.user is not None

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        cog: ServerDesignCog | None = bot.get_cog("ServerDesignCog")  # type: ignore[assignment]

        if cog is not None:
            key = (self.guild_id, self.msg_id)
            cog._pending_proposals.pop(key, None)

        log.info(
            "server_design.cancel: guild_id=%s user_id=%s msg_id=%s",
            self.guild_id,
            interaction.user.id,
            self.msg_id,
        )

        await interaction.response.edit_message(
            content="❌ Server design proposal cancelled.",
            embed=None,
            view=None,
        )


def make_design_view(guild_id: int, msg_id: int) -> discord.ui.View:
    """Create a persistent View with Approve/Cancel buttons."""
    view = discord.ui.View(timeout=None)
    view.add_item(DesignApproveButton(guild_id, msg_id))
    view.add_item(DesignCancelButton(guild_id, msg_id))
    return view


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ServerDesignCog(commands.Cog):
    """Conversational server design — propose, approve, and build layouts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_proposals: dict[tuple[int, int], dict[str, Any]] = {}

    # -- Tool-provider protocol --------------------------------------------

    def get_tools(self) -> list[dict[str, Any]]:
        """Return tool schemas this cog provides."""
        return [PROPOSE_TOOL]

    async def handle_tool_call(
        self,
        name: str,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Dispatch a tool call to the correct handler.

        Returns the tool result as a string.  Unknown tool names return
        an error string (never raises).
        """
        if name == "propose_server_design":
            return await self.handle_propose(tool_input, message)
        return f"Unknown tool: {name}"

    # -- Proposal handling --------------------------------------------------

    async def handle_propose(
        self, proposal: dict[str, Any], message: discord.Message
    ) -> str:
        """Store the proposal and send it as an embed with action buttons.

        Returns a confirmation string that Claude can relay to the user.
        """
        # Validate the proposal
        errors = validate_proposal(proposal)
        if errors:
            error_text = "\n".join(f"• {e}" for e in errors)
            log.warning(
                "server_design.propose: validation failed guild_id=%s errors=%s",
                message.guild.id if message.guild else "DM",
                errors,
            )
            return f"The proposal has validation errors:\n{error_text}"

        embed = render_proposal_embed(proposal)

        # Send embed — we need the sent message's ID for the button custom_id,
        # so we send first, then edit with the view.
        guild_id = message.guild.id if message.guild else 0
        sent = await message.channel.send(embed=embed)

        # Store pending proposal
        key = (guild_id, sent.id)
        self._pending_proposals[key] = proposal

        # Now add the buttons with the correct message ID
        view = make_design_view(guild_id, sent.id)
        await sent.edit(view=view)

        log.info(
            "server_design.propose: guild_id=%s msg_id=%s categories=%d roles=%d",
            guild_id,
            sent.id,
            len(proposal.get("categories", [])),
            len(proposal.get("roles", [])),
        )

        # Log to action_log
        db = self.bot.db  # type: ignore[attr-defined]
        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild_id,
                message.author.id,
                "server_design_proposed",
                str(sent.id),
                proposal.get("summary", ""),
            ),
        )

        total_channels = sum(
            len(cat.get("channels", []))
            for cat in proposal.get("categories", [])
        )
        return (
            f"I've posted a server design proposal with "
            f"{len(proposal.get('roles', []))} roles, "
            f"{len(proposal.get('categories', []))} categories, and "
            f"{total_channels} channels. "
            f"An administrator can click **Approve** to build it or **Cancel** to discard."
        )

    # -- Build executor -----------------------------------------------------

    async def _execute_build(
        self,
        guild: discord.Guild,
        proposal: dict[str, Any],
        user_id: int,
    ) -> str:
        """Create roles → categories → channels sequentially.

        Returns a summary string.  On partial failure, reports what was
        created and what failed — does NOT roll back.
        """
        db = self.bot.db  # type: ignore[attr-defined]
        created_roles: dict[str, discord.Role] = {}
        created_categories: dict[str, discord.CategoryChannel] = {}
        created_channels: list[str] = []
        errors: list[str] = []

        log.info(
            "server_design.build: guild_id=%s user_id=%s starting",
            guild.id,
            user_id,
        )

        # -- Phase 1: Create roles ------------------------------------------
        for role_spec in proposal.get("roles", []):
            role_name = role_spec.get("name", "Unnamed Role")
            try:
                color = discord.Colour.default()
                if role_spec.get("color"):
                    try:
                        color = discord.Colour.from_str(role_spec["color"])
                    except (ValueError, TypeError):
                        pass  # fall back to default colour

                role = await guild.create_role(
                    name=role_name,
                    colour=color,
                    reason=f"Server design by user {user_id}",
                )
                created_roles[role_name] = role

                log.info(
                    "server_design.build.step: guild_id=%s phase=role name=%s role_id=%s",
                    guild.id,
                    role_name,
                    role.id,
                )

                await db.execute(
                    "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        guild.id,
                        user_id,
                        "server_design_role_created",
                        str(role.id),
                        role_name,
                    ),
                )
            except Exception as exc:
                error_msg = f"Failed to create role '{role_name}': {type(exc).__name__}: {exc}"
                errors.append(error_msg)
                log.error(
                    "server_design.build: guild_id=%s phase=role name=%s error=%s",
                    guild.id,
                    role_name,
                    type(exc).__name__,
                )

        # -- Phase 2: Create categories -------------------------------------
        for cat_spec in proposal.get("categories", []):
            cat_name = cat_spec.get("name", "Unnamed Category")
            try:
                # Build permission overwrites
                overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}

                role_access = cat_spec.get("role_access", [])
                if role_access:
                    # Hide from @everyone, grant to specified roles
                    overwrites[guild.default_role] = discord.PermissionOverwrite(
                        view_channel=False
                    )
                    for rname in role_access:
                        role_obj = created_roles.get(rname)
                        if role_obj:
                            overwrites[role_obj] = discord.PermissionOverwrite(
                                view_channel=True,
                                send_messages=True,
                                read_message_history=True,
                                connect=True,
                                speak=True,
                            )

                category = await guild.create_category(
                    cat_name,
                    overwrites=overwrites,
                    reason=f"Server design by user {user_id}",
                )
                created_categories[cat_name] = category

                log.info(
                    "server_design.build.step: guild_id=%s phase=category name=%s cat_id=%s",
                    guild.id,
                    cat_name,
                    category.id,
                )

                await db.execute(
                    "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        guild.id,
                        user_id,
                        "server_design_category_created",
                        str(category.id),
                        cat_name,
                    ),
                )
            except Exception as exc:
                error_msg = f"Failed to create category '{cat_name}': {type(exc).__name__}: {exc}"
                errors.append(error_msg)
                log.error(
                    "server_design.build: guild_id=%s phase=category name=%s error=%s",
                    guild.id,
                    cat_name,
                    type(exc).__name__,
                )
                continue  # skip channels in this category

            # -- Phase 3: Create channels in this category ------------------
            for ch_spec in cat_spec.get("channels", []):
                ch_name = sanitize_channel_name(ch_spec.get("name", "unnamed"))
                ch_type = ch_spec.get("type", "text")
                try:
                    if ch_type == "voice":
                        channel = await guild.create_voice_channel(
                            ch_name,
                            category=category,
                            reason=f"Server design by user {user_id}",
                        )
                    else:
                        topic = ch_spec.get("topic")
                        channel = await guild.create_text_channel(
                            ch_name,
                            category=category,
                            topic=topic,
                            reason=f"Server design by user {user_id}",
                        )

                    created_channels.append(f"{ch_type}:{ch_name}")

                    log.info(
                        "server_design.build.step: guild_id=%s phase=channel name=%s "
                        "type=%s channel_id=%s",
                        guild.id,
                        ch_name,
                        ch_type,
                        channel.id,
                    )

                    await db.execute(
                        "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            guild.id,
                            user_id,
                            "server_design_channel_created",
                            str(channel.id),
                            f"{ch_type}:{ch_name} in {cat_name}",
                        ),
                    )
                except Exception as exc:
                    error_msg = (
                        f"Failed to create {ch_type} channel '{ch_name}' "
                        f"in '{cat_name}': {type(exc).__name__}: {exc}"
                    )
                    errors.append(error_msg)
                    log.error(
                        "server_design.build: guild_id=%s phase=channel name=%s error=%s",
                        guild.id,
                        ch_name,
                        type(exc).__name__,
                    )

        # -- Build summary --------------------------------------------------
        parts: list[str] = []
        if created_roles:
            parts.append(f"✅ Created {len(created_roles)} role(s)")
        if created_categories:
            parts.append(f"✅ Created {len(created_categories)} category/ies")
        if created_channels:
            parts.append(f"✅ Created {len(created_channels)} channel(s)")
        if errors:
            parts.append(f"\n⚠️ {len(errors)} error(s):")
            for err in errors:
                parts.append(f"  • {err}")

        summary = "\n".join(parts) if parts else "No resources were created."

        # Log final build status
        status = "completed" if not errors else "completed_with_errors"
        log.info(
            "server_design.build: guild_id=%s user_id=%s status=%s "
            "roles=%d categories=%d channels=%d errors=%d",
            guild.id,
            user_id,
            status,
            len(created_roles),
            len(created_categories),
            len(created_channels),
            len(errors),
        )

        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild.id,
                user_id,
                "server_design_built",
                str(guild.id),
                f"roles={len(created_roles)} categories={len(created_categories)} "
                f"channels={len(created_channels)} errors={len(errors)}",
            ),
        )

        return summary


async def setup(bot: commands.Bot) -> None:
    """Load the ServerDesignCog into the bot."""
    await bot.add_cog(ServerDesignCog(bot))
