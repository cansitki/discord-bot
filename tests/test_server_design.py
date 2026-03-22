"""Tests for bot.cogs.server_design — T01 initial coverage.

Covers: tool schema, sanitize_channel_name, validate_proposal,
render_proposal_embed, DynamicItem button patterns, and basic imports.
Full build executor and integration tests are added in T02.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import discord
import pytest

from bot.cogs.server_design import (
    PROPOSE_TOOL,
    DESIGN_SYSTEM_PROMPT,
    DesignApproveButton,
    DesignCancelButton,
    ServerDesignCog,
    make_design_view,
    render_proposal_embed,
    sanitize_channel_name,
    validate_proposal,
)


# ── Tool schema ──────────────────────────────────────────────────────


class TestProposeTool:
    """PROPOSE_TOOL dict conforms to Anthropic tool format."""

    def test_has_required_keys(self) -> None:
        assert "name" in PROPOSE_TOOL
        assert "description" in PROPOSE_TOOL
        assert "input_schema" in PROPOSE_TOOL

    def test_name_is_propose_server_design(self) -> None:
        assert PROPOSE_TOOL["name"] == "propose_server_design"

    def test_input_schema_requires_summary_and_categories(self) -> None:
        schema = PROPOSE_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "summary" in schema["properties"]
        assert "categories" in schema["properties"]
        assert "summary" in schema["required"]
        assert "categories" in schema["required"]

    def test_categories_items_have_channels(self) -> None:
        cat_props = PROPOSE_TOOL["input_schema"]["properties"]["categories"]["items"]["properties"]
        assert "channels" in cat_props

    def test_channel_type_is_enum(self) -> None:
        ch_props = (
            PROPOSE_TOOL["input_schema"]["properties"]["categories"]["items"]
            ["properties"]["channels"]["items"]["properties"]
        )
        assert ch_props["type"]["enum"] == ["text", "voice"]


# ── sanitize_channel_name ────────────────────────────────────────────


class TestSanitizeChannelName:
    """sanitize_channel_name() edge cases."""

    def test_spaces_to_hyphens(self) -> None:
        assert sanitize_channel_name("General Chat") == "general-chat"

    def test_uppercase_to_lowercase(self) -> None:
        assert sanitize_channel_name("ANNOUNCEMENTS") == "announcements"

    def test_strips_invalid_chars(self) -> None:
        assert sanitize_channel_name("hello@world!") == "helloworld"

    def test_clamps_length_to_100(self) -> None:
        result = sanitize_channel_name("A" * 200)
        assert result == "a" * 100
        assert len(result) == 100

    def test_empty_string_fallback(self) -> None:
        assert sanitize_channel_name("!!!") == "channel"

    def test_collapses_consecutive_hyphens(self) -> None:
        assert sanitize_channel_name("a - - b") == "a-b"

    def test_strips_leading_trailing_hyphens(self) -> None:
        assert sanitize_channel_name("-hello-") == "hello"

    def test_underscores_preserved(self) -> None:
        assert sanitize_channel_name("my_channel") == "my_channel"


# ── validate_proposal ────────────────────────────────────────────────


class TestValidateProposal:
    """validate_proposal() catches invalid structures."""

    def _valid_proposal(self) -> dict[str, Any]:
        return {
            "summary": "A gaming server",
            "categories": [
                {
                    "name": "General",
                    "channels": [
                        {"name": "chat", "type": "text"},
                        {"name": "voice", "type": "voice"},
                    ],
                }
            ],
        }

    def test_valid_proposal_returns_no_errors(self) -> None:
        assert validate_proposal(self._valid_proposal()) == []

    def test_missing_summary(self) -> None:
        p = self._valid_proposal()
        del p["summary"]
        errors = validate_proposal(p)
        assert any("summary" in e.lower() for e in errors)

    def test_empty_summary(self) -> None:
        p = self._valid_proposal()
        p["summary"] = ""
        errors = validate_proposal(p)
        assert any("summary" in e.lower() for e in errors)

    def test_missing_categories(self) -> None:
        p = self._valid_proposal()
        del p["categories"]
        errors = validate_proposal(p)
        assert any("categories" in e.lower() for e in errors)

    def test_empty_categories_list(self) -> None:
        p = self._valid_proposal()
        p["categories"] = []
        errors = validate_proposal(p)
        assert any("at least one" in e.lower() for e in errors)

    def test_category_missing_name(self) -> None:
        p = {"summary": "test", "categories": [{"channels": []}]}
        errors = validate_proposal(p)
        assert any("name" in e.lower() for e in errors)

    def test_channel_missing_type(self) -> None:
        p = {
            "summary": "test",
            "categories": [{"name": "Cat", "channels": [{"name": "ch"}]}],
        }
        errors = validate_proposal(p)
        assert any("type" in e.lower() for e in errors)

    def test_channel_invalid_type(self) -> None:
        p = {
            "summary": "test",
            "categories": [
                {"name": "Cat", "channels": [{"name": "ch", "type": "forum"}]}
            ],
        }
        errors = validate_proposal(p)
        assert any("text" in e or "voice" in e for e in errors)

    def test_non_dict_rejected(self) -> None:
        errors = validate_proposal("not a dict")  # type: ignore[arg-type]
        assert len(errors) > 0

    def test_roles_must_be_list(self) -> None:
        p = self._valid_proposal()
        p["roles"] = "not a list"
        errors = validate_proposal(p)
        assert any("roles" in e.lower() for e in errors)

    def test_roles_as_list_is_valid(self) -> None:
        p = self._valid_proposal()
        p["roles"] = [{"name": "Admin"}]
        assert validate_proposal(p) == []


# ── render_proposal_embed ────────────────────────────────────────────


class TestRenderProposalEmbed:
    """render_proposal_embed() produces a Discord Embed."""

    def _sample_proposal(self) -> dict[str, Any]:
        return {
            "summary": "A gaming server layout",
            "roles": [
                {"name": "Admin", "color": "#FF0000"},
                {"name": "Member"},
            ],
            "categories": [
                {
                    "name": "General",
                    "channels": [
                        {"name": "chat", "type": "text"},
                        {"name": "voice-lobby", "type": "voice"},
                    ],
                },
                {
                    "name": "Gaming",
                    "channels": [
                        {"name": "lfg", "type": "text"},
                    ],
                    "role_access": ["Admin"],
                },
            ],
        }

    def test_returns_embed(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        assert isinstance(embed, discord.Embed)

    def test_embed_has_title(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        assert "Server Design" in embed.title

    def test_embed_has_summary_in_description(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        assert "gaming server layout" in embed.description

    def test_embed_has_roles_field(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        field_names = [f.name for f in embed.fields]
        assert "Roles" in field_names

    def test_embed_has_category_fields(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        field_names = [f.name for f in embed.fields]
        assert any("General" in n for n in field_names)
        assert any("Gaming" in n for n in field_names)

    def test_embed_shows_channel_icons(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        # Find the General category field
        gen_field = next(f for f in embed.fields if "General" in f.name)
        assert "💬" in gen_field.value
        assert "🔊" in gen_field.value

    def test_embed_shows_role_access(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        gaming_field = next(f for f in embed.fields if "Gaming" in f.name)
        assert "Admin" in gaming_field.value

    def test_embed_colour_is_gold(self) -> None:
        embed = render_proposal_embed(self._sample_proposal())
        assert embed.colour == discord.Colour.gold()

    def test_embed_without_roles(self) -> None:
        p = {"summary": "Minimal", "categories": [{"name": "C", "channels": [{"name": "x", "type": "text"}]}]}
        embed = render_proposal_embed(p)
        field_names = [f.name for f in embed.fields]
        assert "Roles" not in field_names


# ── DynamicItem buttons ──────────────────────────────────────────────


class TestDynamicItemButtons:
    """DesignApproveButton and DesignCancelButton use the DynamicItem pattern."""

    def test_approve_button_custom_id_format(self) -> None:
        btn = DesignApproveButton(guild_id=123, msg_id=456)
        assert btn.item.custom_id == "design:approve:123:456"

    def test_cancel_button_custom_id_format(self) -> None:
        btn = DesignCancelButton(guild_id=123, msg_id=456)
        assert btn.item.custom_id == "design:cancel:123:456"

    def test_approve_button_style_is_green(self) -> None:
        btn = DesignApproveButton(guild_id=1, msg_id=2)
        assert btn.item.style == discord.ButtonStyle.green

    def test_cancel_button_style_is_red(self) -> None:
        btn = DesignCancelButton(guild_id=1, msg_id=2)
        assert btn.item.style == discord.ButtonStyle.red

    def test_make_design_view_has_two_items(self) -> None:
        view = make_design_view(guild_id=100, msg_id=200)
        assert len(view.children) == 2


# ── ServerDesignCog init ─────────────────────────────────────────────


class TestServerDesignCogInit:
    """ServerDesignCog basic instantiation."""

    def test_pending_proposals_empty_on_init(self) -> None:
        mock_bot = MagicMock()
        cog = ServerDesignCog(mock_bot)
        assert cog._pending_proposals == {}


# ── Build execution ──────────────────────────────────────────────────


def _make_guild(
    *,
    guild_id: int = 100,
    fail_text_channel: str | None = None,
) -> MagicMock:
    """Build a mock discord.Guild with working create_* methods.

    If *fail_text_channel* is set, ``create_text_channel`` raises
    ``discord.Forbidden`` when that channel name is requested.
    """
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.default_role = MagicMock(spec=discord.Role)

    _role_counter = {"n": 0}
    _cat_counter = {"n": 0}
    _chan_counter = {"n": 0}

    async def _create_role(**kwargs: Any) -> MagicMock:
        _role_counter["n"] += 1
        role = MagicMock(spec=discord.Role)
        role.id = 9000 + _role_counter["n"]
        role.name = kwargs.get("name", f"role-{_role_counter['n']}")
        return role

    async def _create_category(name: str, **kwargs: Any) -> MagicMock:
        _cat_counter["n"] += 1
        cat = MagicMock(spec=discord.CategoryChannel)
        cat.id = 8000 + _cat_counter["n"]
        cat.name = name
        return cat

    async def _create_text_channel(name: str, **kwargs: Any) -> MagicMock:
        if fail_text_channel and name == fail_text_channel:
            raise discord.Forbidden(MagicMock(), "Missing Access")
        _chan_counter["n"] += 1
        ch = MagicMock(spec=discord.TextChannel)
        ch.id = 7000 + _chan_counter["n"]
        ch.name = name
        return ch

    async def _create_voice_channel(name: str, **kwargs: Any) -> MagicMock:
        _chan_counter["n"] += 1
        ch = MagicMock(spec=discord.VoiceChannel)
        ch.id = 7000 + _chan_counter["n"]
        ch.name = name
        return ch

    guild.create_role = _create_role
    guild.create_category = _create_category
    guild.create_text_channel = _create_text_channel
    guild.create_voice_channel = _create_voice_channel
    return guild


class TestBuildExecution:
    """_execute_build creates roles → categories → channels in order."""

    def _cog_with_db(self) -> tuple[ServerDesignCog, MagicMock]:
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)
        return cog, mock_bot.db

    @pytest.mark.asyncio()
    async def test_roles_created_before_categories_and_channels(self) -> None:
        """Roles are created first, then categories, then channels."""
        cog, db = self._cog_with_db()
        guild = _make_guild()

        proposal: dict[str, Any] = {
            "summary": "Test",
            "roles": [{"name": "Moderator"}],
            "categories": [
                {
                    "name": "General",
                    "channels": [{"name": "chat", "type": "text"}],
                }
            ],
        }

        result = await cog._execute_build(guild, proposal, user_id=42)

        assert "role" in result.lower()
        assert "category" in result.lower() or "categor" in result.lower()
        assert "channel" in result.lower()

        # Verify action_log entries written — roles, categories, channels, final build
        action_types = [
            call[0][1][2]  # params tuple index 2 is action_type
            for call in db.execute.call_args_list
        ]
        assert "server_design_role_created" in action_types
        assert "server_design_category_created" in action_types
        assert "server_design_channel_created" in action_types
        assert "server_design_built" in action_types

    @pytest.mark.asyncio()
    async def test_permission_overwrites_use_created_roles(self) -> None:
        """Categories with role_access use the role objects from phase 1."""
        cog, db = self._cog_with_db()
        guild = _make_guild()

        # Capture the overwrites passed to create_category
        create_category_calls: list[dict[str, Any]] = []
        original_create_category = guild.create_category

        async def _tracking_create_category(name: str, **kwargs: Any) -> MagicMock:
            create_category_calls.append({"name": name, **kwargs})
            return await original_create_category(name, **kwargs)

        guild.create_category = _tracking_create_category

        proposal: dict[str, Any] = {
            "summary": "Private server",
            "roles": [{"name": "VIP", "color": "#FFD700"}],
            "categories": [
                {
                    "name": "VIP Zone",
                    "channels": [{"name": "vip-chat", "type": "text"}],
                    "role_access": ["VIP"],
                }
            ],
        }

        await cog._execute_build(guild, proposal, user_id=42)

        # The category should have been created with overwrites
        assert len(create_category_calls) == 1
        overwrites = create_category_calls[0].get("overwrites", {})
        assert len(overwrites) >= 2  # @everyone (hidden) + VIP (visible)

    @pytest.mark.asyncio()
    async def test_text_and_voice_channels_created(self) -> None:
        """Both text and voice channels are created correctly."""
        cog, db = self._cog_with_db()
        guild = _make_guild()

        # Track calls
        text_calls: list[str] = []
        voice_calls: list[str] = []
        original_text = guild.create_text_channel
        original_voice = guild.create_voice_channel

        async def _track_text(name: str, **kw: Any) -> MagicMock:
            text_calls.append(name)
            return await original_text(name, **kw)

        async def _track_voice(name: str, **kw: Any) -> MagicMock:
            voice_calls.append(name)
            return await original_voice(name, **kw)

        guild.create_text_channel = _track_text
        guild.create_voice_channel = _track_voice

        proposal: dict[str, Any] = {
            "summary": "Mixed",
            "categories": [
                {
                    "name": "Lobby",
                    "channels": [
                        {"name": "general", "type": "text"},
                        {"name": "voice-lobby", "type": "voice"},
                    ],
                }
            ],
        }

        await cog._execute_build(guild, proposal, user_id=1)

        assert "general" in text_calls
        assert "voice-lobby" in voice_calls


class TestPartialFailure:
    """Build reports partial failures without crashing."""

    @pytest.mark.asyncio()
    async def test_partial_failure_reports_created_and_errors(self) -> None:
        """When one channel fails, the summary lists what was created and what failed."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)
        guild = _make_guild(fail_text_channel="broken-channel")

        proposal: dict[str, Any] = {
            "summary": "Fail test",
            "categories": [
                {
                    "name": "Main",
                    "channels": [
                        {"name": "good-channel", "type": "text"},
                        {"name": "broken-channel", "type": "text"},
                        {"name": "also-good", "type": "voice"},
                    ],
                }
            ],
        }

        result = await cog._execute_build(guild, proposal, user_id=99)

        # Should report both successes and errors
        assert "✅" in result
        assert "⚠️" in result or "error" in result.lower()
        assert "broken-channel" in result


class TestBulkBuild10PlusChannels:
    """10+ channel design completes without errors — retires rate-limit risk."""

    @pytest.mark.asyncio()
    async def test_12_channels_across_3_categories(self) -> None:
        """A proposal with 3 categories and 12+ channels builds fully."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)
        guild = _make_guild()

        # Track every create call
        text_calls: list[str] = []
        voice_calls: list[str] = []
        original_text = guild.create_text_channel
        original_voice = guild.create_voice_channel

        async def _track_text(name: str, **kw: Any) -> MagicMock:
            text_calls.append(name)
            return await original_text(name, **kw)

        async def _track_voice(name: str, **kw: Any) -> MagicMock:
            voice_calls.append(name)
            return await original_voice(name, **kw)

        guild.create_text_channel = _track_text
        guild.create_voice_channel = _track_voice

        proposal: dict[str, Any] = {
            "summary": "Gaming server with lots of channels",
            "roles": [{"name": "Gamer"}, {"name": "Admin", "color": "#FF0000"}],
            "categories": [
                {
                    "name": "General",
                    "channels": [
                        {"name": "welcome", "type": "text"},
                        {"name": "announcements", "type": "text"},
                        {"name": "general-chat", "type": "text"},
                        {"name": "lobby", "type": "voice"},
                    ],
                },
                {
                    "name": "Gaming",
                    "channels": [
                        {"name": "lfg", "type": "text"},
                        {"name": "game-talk", "type": "text"},
                        {"name": "clips", "type": "text"},
                        {"name": "game-voice-1", "type": "voice"},
                        {"name": "game-voice-2", "type": "voice"},
                    ],
                },
                {
                    "name": "Admin",
                    "channels": [
                        {"name": "mod-chat", "type": "text"},
                        {"name": "bot-commands", "type": "text"},
                        {"name": "admin-voice", "type": "voice"},
                    ],
                    "role_access": ["Admin"],
                },
            ],
        }

        result = await cog._execute_build(guild, proposal, user_id=1)

        # All 12 channels created (8 text + 4 voice)
        total_channels = len(text_calls) + len(voice_calls)
        assert total_channels == 12, f"Expected 12 channels, got {total_channels}"
        assert len(text_calls) == 8
        assert len(voice_calls) == 4

        # No errors in summary
        assert "⚠️" not in result
        assert "error" not in result.lower()

        # All categories created
        assert "3" in result or "categor" in result.lower()

        # Roles created
        assert "2" in result or "role" in result.lower()


# ── DynamicItem callback tests ───────────────────────────────────────


class TestDesignApproveCallback:
    """DesignApproveButton.callback handles admin check and build trigger."""

    @pytest.mark.asyncio()
    async def test_approve_with_admin_triggers_build(self) -> None:
        """Admin clicking approve removes proposal and starts build."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)
        mock_bot.get_cog = MagicMock(return_value=cog)

        proposal: dict[str, Any] = {
            "summary": "Test server",
            "categories": [
                {"name": "General", "channels": [{"name": "chat", "type": "text"}]}
            ],
        }
        key = (100, 200)
        cog._pending_proposals[key] = proposal

        # Mock build to return a summary
        cog._execute_build = AsyncMock(return_value="✅ Created 1 channel(s)")

        btn = DesignApproveButton(guild_id=100, msg_id=200)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = _make_guild(guild_id=100)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 42
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.client = mock_bot
        interaction.response = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await btn.callback(interaction)

        # Proposal removed
        assert key not in cog._pending_proposals
        # Build was executed
        cog._execute_build.assert_awaited_once()
        # Response edited with build summary
        interaction.edit_original_response.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_approve_without_admin_rejected(self) -> None:
        """Non-admin clicking approve gets an ephemeral rejection."""
        btn = DesignApproveButton(guild_id=100, msg_id=200)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.response = AsyncMock()

        await btn.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_args = interaction.response.send_message.call_args
        assert "administrator" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio()
    async def test_approve_expired_proposal(self) -> None:
        """Approve on an expired proposal sends an ephemeral message."""
        mock_bot = MagicMock()
        cog = ServerDesignCog(mock_bot)
        mock_bot.get_cog = MagicMock(return_value=cog)
        mock_bot.db = AsyncMock()

        btn = DesignApproveButton(guild_id=100, msg_id=999)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 1
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.client = mock_bot
        interaction.response = AsyncMock()

        await btn.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_args = interaction.response.send_message.call_args
        assert "expired" in call_args[0][0].lower() or "already processed" in call_args[0][0].lower()


class TestDesignCancelCallback:
    """DesignCancelButton.callback removes proposal and updates message."""

    @pytest.mark.asyncio()
    async def test_cancel_removes_proposal(self) -> None:
        """Cancel button removes the pending proposal and updates the message."""
        mock_bot = MagicMock()
        cog = ServerDesignCog(mock_bot)
        mock_bot.get_cog = MagicMock(return_value=cog)

        key = (100, 200)
        cog._pending_proposals[key] = {"summary": "test", "categories": []}

        btn = DesignCancelButton(guild_id=100, msg_id=200)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.guild.id = 100
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 42
        interaction.client = mock_bot
        interaction.response = AsyncMock()

        await btn.callback(interaction)

        # Proposal removed
        assert key not in cog._pending_proposals
        # Message updated
        interaction.response.edit_message.assert_awaited_once()
        call_kwargs = interaction.response.edit_message.call_args.kwargs
        assert "cancelled" in call_kwargs["content"].lower()


# ── Large embed test ─────────────────────────────────────────────────


class TestLargeProposalEmbed:
    """Embed rendering handles large proposals within Discord limits."""

    def test_large_proposal_under_6000_chars(self) -> None:
        """A proposal with 20+ channels stays within the 6000-char embed limit."""
        categories = []
        for i in range(5):
            channels = [
                {"name": f"channel-{i}-{j}", "type": "text" if j % 2 == 0 else "voice"}
                for j in range(6)
            ]
            categories.append({"name": f"Category {i}", "channels": channels})

        proposal: dict[str, Any] = {
            "summary": "Large server with many channels",
            "roles": [{"name": f"Role {i}"} for i in range(5)],
            "categories": categories,
        }

        embed = render_proposal_embed(proposal)

        # Calculate total embed length
        total = len(embed.title or "") + len(embed.description or "")
        for field in embed.fields:
            total += len(field.name) + len(str(field.value))
        if embed.footer and embed.footer.text:
            total += len(embed.footer.text)

        assert total <= 6000, f"Embed total chars {total} exceeds 6000"


# ── Handle propose tests ─────────────────────────────────────────────


class TestHandlePropose:
    """ServerDesignCog.handle_propose stores proposal and sends embed."""

    @pytest.mark.asyncio()
    async def test_valid_proposal_stored_and_sent(self) -> None:
        """Valid proposal is stored in pending and embed is sent."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)

        message = MagicMock(spec=discord.Message)
        message.guild = MagicMock(spec=discord.Guild)
        message.guild.id = 100
        message.author = MagicMock(spec=discord.Member)
        message.author.id = 42
        sent_msg = MagicMock(spec=discord.Message)
        sent_msg.id = 555
        message.channel = MagicMock(spec=discord.TextChannel)
        message.channel.send = AsyncMock(return_value=sent_msg)
        sent_msg.edit = AsyncMock()

        proposal: dict[str, Any] = {
            "summary": "Gaming server",
            "categories": [
                {"name": "General", "channels": [{"name": "chat", "type": "text"}]}
            ],
        }

        result = await cog.handle_propose(proposal, message)

        # Stored in pending
        assert (100, 555) in cog._pending_proposals
        assert cog._pending_proposals[(100, 555)] == proposal
        # Embed sent
        message.channel.send.assert_awaited_once()
        # View attached
        sent_msg.edit.assert_awaited_once()
        # Returns confirmation string
        assert "1 categories" in result or "1 category" in result
        assert "1 channels" in result or "1 channel" in result

    @pytest.mark.asyncio()
    async def test_invalid_proposal_returns_errors(self) -> None:
        """Invalid proposal returns error text without storing or sending."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog = ServerDesignCog(mock_bot)

        message = MagicMock(spec=discord.Message)
        message.guild = MagicMock(spec=discord.Guild)
        message.guild.id = 100
        message.channel = MagicMock(spec=discord.TextChannel)
        message.channel.send = AsyncMock()

        result = await cog.handle_propose({"summary": ""}, message)

        assert "validation errors" in result.lower()
        assert len(cog._pending_proposals) == 0
        message.channel.send.assert_not_awaited()
