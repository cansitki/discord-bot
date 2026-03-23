"""Tests for bot.cogs.github_auth — /github-login, /github-logout, /github-status."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx
import pytest
from discord.ext import commands

from bot.cogs.github_auth import GitHubAuthCog


# ── Helpers ──────────────────────────────────────────────────────────


def _make_bot(*, github_app_id=None, github_private_key=None, db=None):
    """Create a mock DiscordBot for testing GitHubAuthCog."""
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.github_app_id = github_app_id
    bot.config.github_private_key = github_private_key
    bot.db = db or AsyncMock()
    return bot


def _make_ctx(*, guild_id=123, user_id=456, is_guild=True):
    """Create a mock Context."""
    ctx = AsyncMock()
    if is_guild:
        ctx.guild = MagicMock()
        ctx.guild.id = guild_id
    else:
        ctx.guild = None
    ctx.author = MagicMock()
    ctx.author.id = user_id
    ctx.interaction = MagicMock()  # Slash command — don't try to delete
    ctx.message = MagicMock()
    return ctx


# ── /github-login tests ─────────────────────────────────────────────


class TestGitHubLogin:
    """Tests for the /github-login command."""

    @pytest.mark.asyncio()
    async def test_login_validates_and_stores_token(self) -> None:
        """Successful login validates the token against GitHub and stores it."""
        db = AsyncMock()
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"login": "testuser"}

        with patch("bot.cogs.github_auth.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            await cog.github_login.callback(cog, ctx, token="ghp_testtoken123")

        # Token stored in DB
        db.execute.assert_any_call(
            "INSERT INTO github_tokens (guild_id, token, github_username, set_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "token = excluded.token, "
            "github_username = excluded.github_username, "
            "set_by = excluded.set_by",
            (123, "ghp_testtoken123", "testuser", 456),
        )

        # Confirmation sent
        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "testuser" in msg
        assert "✅" in msg

    @pytest.mark.asyncio()
    async def test_login_rejects_invalid_token(self) -> None:
        """Invalid token (non-200 from GitHub) → error message."""
        db = AsyncMock()
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("bot.cogs.github_auth.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            await cog.github_login.callback(cog, ctx, token="bad-token")

        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "401" in msg

    @pytest.mark.asyncio()
    async def test_login_handles_network_error(self) -> None:
        """Network error → appropriate error message."""
        db = AsyncMock()
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        with patch("bot.cogs.github_auth.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            await cog.github_login.callback(cog, ctx, token="ghp_testtoken123")

        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "GitHub API" in msg

    @pytest.mark.asyncio()
    async def test_login_rejects_in_dm(self) -> None:
        """/github-login in a DM → error message."""
        bot = _make_bot()
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx(is_guild=False)

        await cog.github_login.callback(cog, ctx, token="ghp_test")

        ctx.send.assert_awaited_once()
        assert "server" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_login_logs_action(self) -> None:
        """Successful login writes to action_log."""
        db = AsyncMock()
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"login": "octocat"}

        with patch("bot.cogs.github_auth.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            await cog.github_login.callback(cog, ctx, token="ghp_test")

        # Find action_log insert
        action_log_calls = [
            c for c in db.execute.call_args_list
            if "action_log" in str(c)
        ]
        assert len(action_log_calls) >= 1
        args = action_log_calls[0].args[1]
        assert args[2] == "github_pat_login"


# ── /github-logout tests ────────────────────────────────────────────


class TestGitHubLogout:
    """Tests for the /github-logout command."""

    @pytest.mark.asyncio()
    async def test_logout_clears_token(self) -> None:
        """Logout removes the stored token."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"github_username": "testuser"})
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_logout.callback(cog, ctx)

        db.execute.assert_any_call(
            "DELETE FROM github_tokens WHERE guild_id = ?",
            (123,),
        )

        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "✅" in msg
        assert "testuser" in msg

    @pytest.mark.asyncio()
    async def test_logout_no_token(self) -> None:
        """Logout when no token is stored."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_logout.callback(cog, ctx)

        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "No GitHub token" in msg

    @pytest.mark.asyncio()
    async def test_logout_mentions_app_fallback(self) -> None:
        """Logout mentions GitHub App fallback when app is configured."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"github_username": "testuser"})
        bot = _make_bot(
            github_app_id="12345",
            github_private_key="key",
            db=db,
        )
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_logout.callback(cog, ctx)

        msg = ctx.send.call_args[0][0]
        assert "GitHub App" in msg


# ── /github-status tests ────────────────────────────────────────────


class TestGitHubStatus:
    """Tests for the /github-status command."""

    @pytest.mark.asyncio()
    async def test_status_shows_app_and_pat(self) -> None:
        """Status shows both App and PAT when both are configured."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"github_username": "testuser"})
        bot = _make_bot(
            github_app_id="12345",
            github_private_key="key",
            db=db,
        )
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_status.callback(cog, ctx)

        ctx.send.assert_awaited()
        embed = ctx.send.call_args.kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        # Both should show as configured
        field_values = " ".join(f.value for f in embed.fields)
        assert "✅" in field_values
        assert "testuser" in field_values

    @pytest.mark.asyncio()
    async def test_status_shows_none(self) -> None:
        """Status shows nothing configured when both are absent."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_status.callback(cog, ctx)

        ctx.send.assert_awaited()
        embed = ctx.send.call_args.kwargs["embed"]
        field_values = " ".join(f.value for f in embed.fields)
        assert "❌" in field_values
        assert "How to connect" in " ".join(f.name for f in embed.fields)

    @pytest.mark.asyncio()
    async def test_status_app_only(self) -> None:
        """Status shows App configured but no PAT."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_bot(
            github_app_id="12345",
            github_private_key="key",
            db=db,
        )
        cog = GitHubAuthCog(bot)
        ctx = _make_ctx()

        await cog.github_status.callback(cog, ctx)

        embed = ctx.send.call_args.kwargs["embed"]
        assert embed.colour == discord.Colour.green()


# ── get_guild_token helper ───────────────────────────────────────────


class TestGetGuildToken:
    """Tests for the get_guild_token helper method."""

    @pytest.mark.asyncio()
    async def test_returns_token_when_present(self) -> None:
        """Returns the stored token for a guild."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={"token": "ghp_abc123"})
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)

        result = await cog.get_guild_token(123)
        assert result == "ghp_abc123"

    @pytest.mark.asyncio()
    async def test_returns_none_when_absent(self) -> None:
        """Returns None when no token is stored for the guild."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        bot = _make_bot(db=db)
        cog = GitHubAuthCog(bot)

        result = await cog.get_guild_token(123)
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_when_no_db(self) -> None:
        """Returns None when database is not initialized."""
        bot = MagicMock()
        bot.db = None
        cog = GitHubAuthCog(bot)

        result = await cog.get_guild_token(123)
        assert result is None


# ── setup ────────────────────────────────────────────────────────────


class TestGitHubAuthSetup:
    """setup() adds the cog to the bot."""

    @pytest.mark.asyncio()
    async def test_setup_adds_cog(self) -> None:
        bot = AsyncMock(spec=commands.Bot)
        from bot.cogs.github_auth import setup
        await setup(bot)
        bot.add_cog.assert_called_once()
