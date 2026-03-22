"""Integration tests for DiscordBot skeleton."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from bot.bot import DiscordBot
from bot.config import Config


@pytest.fixture()
def mock_config(tmp_path: Path) -> Config:
    """Return a Config instance for testing (no real secrets)."""
    return Config(
        discord_bot_token="test-token-do-not-use",
        anthropic_api_key="test-api-key-do-not-use",
        database_path=str(tmp_path / "test.db"),
        command_prefix="!",
        claude_model="claude-sonnet-4-20250514",
    )


@pytest.fixture()
def bot(mock_config: Config) -> DiscordBot:
    """Create a DiscordBot without connecting to Discord."""
    return DiscordBot(mock_config)


# ── Constructor ───────────────────────────────────────────────────────


class TestBotInit:
    """DiscordBot constructor sets up intents and config correctly."""

    def test_stores_config(self, bot: DiscordBot, mock_config: Config) -> None:
        assert bot.config is mock_config

    def test_command_prefix(self, bot: DiscordBot) -> None:
        assert bot.command_prefix == "!"

    def test_intents_guilds(self, bot: DiscordBot) -> None:
        assert bot.intents.guilds is True

    def test_intents_members(self, bot: DiscordBot) -> None:
        assert bot.intents.members is True

    def test_intents_message_content(self, bot: DiscordBot) -> None:
        assert bot.intents.message_content is True

    def test_intents_guild_messages(self, bot: DiscordBot) -> None:
        assert bot.intents.guild_messages is True

    def test_ready_flag_initially_false(self, bot: DiscordBot) -> None:
        assert bot._on_ready_fired is False


# ── setup_hook ────────────────────────────────────────────────────────


class TestSetupHook:
    """setup_hook wires database and loads cogs."""

    async def test_creates_db_and_connects(
        self, bot: DiscordBot
    ) -> None:
        with (
            patch("bot.bot.DatabaseManager") as MockDB,
            patch.object(type(bot), "tree", new_callable=lambda: property(lambda self: MagicMock(sync=AsyncMock()))),
        ):
            mock_db = AsyncMock()
            MockDB.return_value = mock_db
            bot.load_extension = AsyncMock()
            await bot.setup_hook()
            MockDB.assert_called_once_with(bot.config.database_path)
            mock_db.connect.assert_awaited_once()

    async def test_runs_migrations(self, bot: DiscordBot) -> None:
        with (
            patch("bot.bot.DatabaseManager") as MockDB,
            patch.object(type(bot), "tree", new_callable=lambda: property(lambda self: MagicMock(sync=AsyncMock()))),
        ):
            mock_db = AsyncMock()
            MockDB.return_value = mock_db
            bot.load_extension = AsyncMock()
            await bot.setup_hook()
            mock_db.run_migrations.assert_awaited_once_with("migrations")

    async def test_loads_ping_cog(self, bot: DiscordBot) -> None:
        with (
            patch("bot.bot.DatabaseManager") as MockDB,
            patch.object(type(bot), "tree", new_callable=lambda: property(lambda self: MagicMock(sync=AsyncMock()))),
        ):
            MockDB.return_value = AsyncMock()
            bot.load_extension = AsyncMock()
            await bot.setup_hook()
            bot.load_extension.assert_any_await("bot.cogs.ping")
            bot.load_extension.assert_any_await("bot.cogs.verification")

    async def test_syncs_command_tree_globally(self, bot: DiscordBot) -> None:
        mock_tree = MagicMock()
        mock_tree.sync = AsyncMock()
        with (
            patch("bot.bot.DatabaseManager") as MockDB,
            patch.object(type(bot), "tree", new_callable=lambda: property(lambda self: mock_tree)),
            patch.dict("os.environ", {}, clear=False),
        ):
            import os
            os.environ.pop("DEV_GUILD_ID", None)
            MockDB.return_value = AsyncMock()
            bot.load_extension = AsyncMock()
            await bot.setup_hook()
            mock_tree.sync.assert_awaited_once_with()

    async def test_syncs_to_dev_guild_when_set(self, bot: DiscordBot) -> None:
        mock_tree = MagicMock()
        mock_tree.sync = AsyncMock()
        with (
            patch("bot.bot.DatabaseManager") as MockDB,
            patch.object(type(bot), "tree", new_callable=lambda: property(lambda self: mock_tree)),
            patch.dict("os.environ", {"DEV_GUILD_ID": "123456789"}, clear=False),
        ):
            MockDB.return_value = AsyncMock()
            bot.load_extension = AsyncMock()
            await bot.setup_hook()
            mock_tree.copy_global_to.assert_called_once()
            mock_tree.sync.assert_awaited_once()
            call_kwargs = mock_tree.sync.call_args
            assert call_kwargs.kwargs.get("guild") is not None


# ── on_ready ──────────────────────────────────────────────────────────


class TestOnReady:
    """on_ready fires once and is guarded against reconnect re-fires."""

    async def test_sets_ready_flag(self, bot: DiscordBot) -> None:
        mock_user = MagicMock(spec=discord.ClientUser)
        mock_user.name = "TestBot"
        mock_user.discriminator = "0"
        bot._guilds = {}
        with patch.object(type(bot), "user", new_callable=lambda: property(lambda self: mock_user)):
            await bot.on_ready()
        assert bot._on_ready_fired is True

    async def test_does_not_refire(self, bot: DiscordBot) -> None:
        mock_user = MagicMock(spec=discord.ClientUser)
        mock_user.name = "TestBot"
        mock_user.discriminator = "0"
        bot._guilds = {}
        with patch.object(type(bot), "user", new_callable=lambda: property(lambda self: mock_user)):
            await bot.on_ready()

            # Second call should produce no output (guard prevents re-fire)
            f = io.StringIO()
            with redirect_stdout(f):
                await bot.on_ready()
            assert f.getvalue() == ""


# ── on_message ────────────────────────────────────────────────────────


class TestOnMessage:
    """on_message ignores self and processes commands for others."""

    async def test_ignores_own_messages(self, bot: DiscordBot) -> None:
        mock_user = MagicMock(spec=discord.ClientUser)
        message = MagicMock(spec=discord.Message)
        message.author = mock_user
        bot.process_commands = AsyncMock()
        with patch.object(type(bot), "user", new_callable=lambda: property(lambda self: mock_user)):
            await bot.on_message(message)
        bot.process_commands.assert_not_awaited()

    async def test_processes_other_messages(self, bot: DiscordBot) -> None:
        bot_user = MagicMock(spec=discord.ClientUser)
        other_user = MagicMock(spec=discord.User)
        message = MagicMock(spec=discord.Message)
        message.author = other_user
        bot.process_commands = AsyncMock()
        with patch.object(type(bot), "user", new_callable=lambda: property(lambda self: bot_user)):
            await bot.on_message(message)
        bot.process_commands.assert_awaited_once_with(message)


# ── close ─────────────────────────────────────────────────────────────


class TestClose:
    """close() cleans up database before shutting down bot."""

    async def test_closes_db(self, bot: DiscordBot) -> None:
        bot.db = AsyncMock()
        with patch.object(commands.Bot, "close", new_callable=AsyncMock):
            await bot.close()
        bot.db.close.assert_awaited_once()

    async def test_calls_super_close(self, bot: DiscordBot) -> None:
        bot.db = AsyncMock()
        with patch.object(
            commands.Bot, "close", new_callable=AsyncMock
        ) as mock_super_close:
            await bot.close()
        mock_super_close.assert_awaited_once()

    async def test_handles_no_db(self, bot: DiscordBot) -> None:
        """close() works even if db was never set (setup_hook didn't run)."""
        bot.db = None
        with patch.object(commands.Bot, "close", new_callable=AsyncMock):
            await bot.close()  # Should not raise
