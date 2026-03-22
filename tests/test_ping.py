"""Tests for the PingCog hybrid command."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from discord.ext import commands

from bot.cogs.ping import PingCog


@pytest.fixture()
def mock_bot() -> MagicMock:
    """Create a mock bot with a latency property."""
    bot = MagicMock(spec=commands.Bot)
    type(bot).latency = PropertyMock(return_value=0.042)  # 42ms
    return bot


@pytest.fixture()
def cog(mock_bot: MagicMock) -> PingCog:
    """Create a PingCog instance with a mock bot."""
    return PingCog(mock_bot)


class TestPingCog:
    """PingCog registers a hybrid ping command and reports latency."""

    def test_has_ping_command(self, cog: PingCog) -> None:
        """The cog exposes a 'ping' command."""
        cmd = cog.ping
        assert isinstance(cmd, commands.HybridCommand)
        assert cmd.name == "ping"

    def test_ping_description(self, cog: PingCog) -> None:
        """The ping command has a user-facing description."""
        assert cog.ping.description == "Check bot latency"

    async def test_ping_responds_with_pong(self, cog: PingCog) -> None:
        """Calling ping sends a message containing 'Pong!' and latency."""
        ctx = AsyncMock()
        # Call the underlying callback directly to avoid __call__ routing issues
        await cog.ping.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        response = ctx.send.call_args[0][0]
        assert "Pong!" in response

    async def test_ping_includes_latency_ms(self, cog: PingCog) -> None:
        """The response includes the latency in milliseconds."""
        ctx = AsyncMock()
        await cog.ping.callback(cog, ctx)
        response = ctx.send.call_args[0][0]
        # bot.latency = 0.042 → 42ms
        assert "42ms" in response

    async def test_ping_rounds_latency(self) -> None:
        """Latency is rounded to the nearest integer ms."""
        bot = MagicMock(spec=commands.Bot)
        type(bot).latency = PropertyMock(return_value=0.0567)  # 56.7ms → 57ms
        cog = PingCog(bot)
        ctx = AsyncMock()
        await cog.ping.callback(cog, ctx)
        response = ctx.send.call_args[0][0]
        assert "57ms" in response


class TestPingCogSetup:
    """Module-level setup function adds the cog to the bot."""

    async def test_setup_adds_cog(self) -> None:
        """The setup() function calls bot.add_cog with a PingCog instance."""
        from bot.cogs.ping import setup

        bot = AsyncMock(spec=commands.Bot)
        await setup(bot)
        bot.add_cog.assert_awaited_once()
        added_cog = bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, PingCog)
