"""Tests for bot.cogs.help — /help command and command listing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from bot.cogs.help import COMMAND_CATEGORIES, HelpCog
from bot.config import Config


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def mock_config(tmp_path: Path) -> Config:
    """Return a Config instance for testing."""
    return Config(
        discord_bot_token="test-token-do-not-use",
        anthropic_api_key="test-api-key-do-not-use",
        database_path=str(tmp_path / "test.db"),
        command_prefix="!",
        claude_model="claude-sonnet-4-20250514",
    )


@pytest.fixture()
def mock_bot(mock_config: Config) -> MagicMock:
    """Create a mock bot for HelpCog."""
    bot = MagicMock(spec=commands.Bot)
    bot.config = mock_config
    return bot


@pytest.fixture()
def cog(mock_bot: MagicMock) -> HelpCog:
    """Create a HelpCog instance."""
    return HelpCog(mock_bot)


# ── Command categories metadata ─────────────────────────────────────


class TestCommandCategories:
    """COMMAND_CATEGORIES is a well-formed list of categories."""

    def test_categories_is_list(self) -> None:
        """Categories metadata is a non-empty list."""
        assert isinstance(COMMAND_CATEGORIES, list)
        assert len(COMMAND_CATEGORIES) > 0

    def test_each_category_has_required_keys(self) -> None:
        """Each category has 'name' and 'commands' keys."""
        for cat in COMMAND_CATEGORIES:
            assert "name" in cat
            assert "commands" in cat
            assert isinstance(cat["commands"], list)
            assert len(cat["commands"]) > 0

    def test_each_command_has_required_keys(self) -> None:
        """Each command in each category has name, description, permissions."""
        for cat in COMMAND_CATEGORIES:
            for cmd in cat["commands"]:
                assert "name" in cmd, f"Command missing 'name' in {cat['name']}"
                assert "description" in cmd, f"Command {cmd['name']} missing 'description'"
                assert "permissions" in cmd, f"Command {cmd['name']} missing 'permissions'"

    def test_ai_category_contains_ask(self) -> None:
        """The AI & Chat category includes the /ask command."""
        ai_cat = next(c for c in COMMAND_CATEGORIES if "AI" in c["name"])
        cmd_names = [cmd["name"] for cmd in ai_cat["commands"]]
        assert "/ask" in cmd_names

    def test_auth_category_contains_github_login(self) -> None:
        """The Authentication category includes /github-login."""
        auth_cat = next(c for c in COMMAND_CATEGORIES if "Auth" in c["name"])
        cmd_names = [cmd["name"] for cmd in auth_cat["commands"]]
        assert "/github-login" in cmd_names

    def test_utility_category_contains_help(self) -> None:
        """The Utility category includes /help."""
        util_cat = next(c for c in COMMAND_CATEGORIES if "Utility" in c["name"])
        cmd_names = [cmd["name"] for cmd in util_cat["commands"]]
        assert "/help" in cmd_names


# ── HelpCog ──────────────────────────────────────────────────────────


class TestHelpCog:
    """HelpCog registers a hybrid /help command."""

    def test_has_help_command(self, cog: HelpCog) -> None:
        """Cog has a 'help' command registered."""
        cmds = [c.name for c in cog.get_commands()]
        assert "help" in cmds

    def test_help_command_description(self, cog: HelpCog) -> None:
        """Help command has the expected description."""
        cmd = next(c for c in cog.get_commands() if c.name == "help")
        assert "commands" in cmd.description.lower()

    async def test_help_sends_embed(self, cog: HelpCog) -> None:
        """Help command sends an embed with command categories."""
        ctx = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123
        ctx.author = MagicMock()
        ctx.author.id = 456

        await cog.help_command.callback(cog, ctx)

        ctx.send.assert_called_once()
        call_kwargs = ctx.send.call_args
        assert "embed" in call_kwargs.kwargs
        embed = call_kwargs.kwargs["embed"]
        assert isinstance(embed, discord.Embed)

    async def test_help_embed_has_all_categories(self, cog: HelpCog) -> None:
        """Help embed includes a field for each command category."""
        ctx = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123
        ctx.author = MagicMock()
        ctx.author.id = 456

        await cog.help_command.callback(cog, ctx)

        embed = ctx.send.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        for cat in COMMAND_CATEGORIES:
            assert cat["name"] in field_names

    async def test_help_is_ephemeral(self, cog: HelpCog) -> None:
        """Help response is sent as ephemeral."""
        ctx = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123
        ctx.author = MagicMock()
        ctx.author.id = 456

        await cog.help_command.callback(cog, ctx)

        call_kwargs = ctx.send.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True


class TestHelpSetup:
    """setup() adds the cog to the bot."""

    async def test_setup_adds_cog(self) -> None:
        bot = AsyncMock(spec=commands.Bot)
        from bot.cogs.help import setup
        await setup(bot)
        bot.add_cog.assert_called_once()
