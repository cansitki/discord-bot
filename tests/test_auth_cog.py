"""Tests for bot.cogs.auth — Claude OAuth login/logout/status commands."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.auth import AuthCog
from bot.oauth import OAuthError, OAuthManager, OAuthTokens


def _make_bot(*, oauth_manager=None, api_key="test-key", db=None):
    """Create a mock DiscordBot for testing AuthCog."""
    bot = MagicMock()
    bot.oauth_manager = oauth_manager
    bot.config = MagicMock()
    bot.config.anthropic_api_key = api_key
    bot.db = db
    return bot


def _make_ctx(*, guild_id=123, user_id=456, is_guild=True):
    """Create a mock Context for testing commands."""
    ctx = AsyncMock()
    if is_guild:
        ctx.guild = MagicMock()
        ctx.guild.id = guild_id
    else:
        ctx.guild = None
    ctx.author = MagicMock()
    ctx.author.id = user_id
    ctx.interaction = MagicMock()  # Slash command (no message.delete needed)
    return ctx


class TestClaudeLogin:
    """Tests for /claude-login command."""

    @pytest.mark.asyncio
    async def test_login_starts_flow(self) -> None:
        """Login generates an auth URL and sends it as an embed."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False
        manager.start_flow.return_value = ("flow-123", "https://claude.ai/oauth/authorize?...")

        bot = _make_bot(oauth_manager=manager)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_login.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs.get("embed")
        assert isinstance(embed, discord.Embed)

    @pytest.mark.asyncio
    async def test_login_rejects_if_already_authenticated(self) -> None:
        """Login refuses if tokens already exist."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = True

        bot = _make_bot(oauth_manager=manager)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_login.callback(cog, ctx)

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "already configured" in message.lower()

    @pytest.mark.asyncio
    async def test_login_rejects_in_dm(self) -> None:
        """Login requires a guild context."""
        bot = _make_bot(oauth_manager=AsyncMock())
        cog = AuthCog(bot)
        ctx = _make_ctx(is_guild=False)

        await cog.claude_login.callback(cog, ctx)

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else ""
        assert "server" in message.lower()

    @pytest.mark.asyncio
    async def test_login_rejects_without_oauth_manager(self) -> None:
        """Login reports error when OAuth manager is not available."""
        bot = _make_bot(oauth_manager=None)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_login.callback(cog, ctx)

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else ""
        assert "not available" in message.lower()


class TestClaudeCallback:
    """Tests for /claude-callback command."""

    @pytest.mark.asyncio
    async def test_callback_completes_flow(self) -> None:
        """Callback exchanges code and reports success."""
        manager = AsyncMock(spec=OAuthManager)
        mock_tokens = OAuthTokens("sk-ant-oat-new", "refresh", time.time() + 3600)
        manager.complete_flow.return_value = mock_tokens

        db = AsyncMock()
        bot = _make_bot(oauth_manager=manager, db=db)
        cog = AuthCog(bot)

        # Start a flow first
        ctx_login = _make_ctx()
        manager.has_tokens.return_value = False
        manager.start_flow.return_value = ("flow-id", "https://...")
        await cog.claude_login.callback(cog, ctx_login)

        # Now complete it
        ctx_callback = _make_ctx()
        await cog.claude_callback.callback(cog, ctx_callback, "code123#state456")

        manager.complete_flow.assert_awaited_once()
        call_args = ctx_callback.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "successful" in message.lower()

    @pytest.mark.asyncio
    async def test_callback_no_pending_flow(self) -> None:
        """Callback rejects when no flow is pending."""
        manager = AsyncMock(spec=OAuthManager)
        bot = _make_bot(oauth_manager=manager)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_callback.callback(cog, ctx, "code#state")

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "no pending" in message.lower()

    @pytest.mark.asyncio
    async def test_callback_handles_exchange_failure(self) -> None:
        """Callback reports error when token exchange fails."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False
        manager.start_flow.return_value = ("flow-id", "https://...")
        manager.complete_flow.side_effect = OAuthError("Bad code")

        bot = _make_bot(oauth_manager=manager)
        cog = AuthCog(bot)

        # Start flow
        ctx_login = _make_ctx()
        await cog.claude_login.callback(cog, ctx_login)

        # Fail callback
        ctx_callback = _make_ctx()
        await cog.claude_callback.callback(cog, ctx_callback, "bad-code#state")

        call_args = ctx_callback.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "failed" in message.lower()

    @pytest.mark.asyncio
    async def test_callback_logs_action(self) -> None:
        """Callback writes an action_log entry on success."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False
        manager.start_flow.return_value = ("flow-id", "https://...")
        mock_tokens = OAuthTokens("token", "refresh", time.time() + 3600)
        manager.complete_flow.return_value = mock_tokens

        db = AsyncMock()
        bot = _make_bot(oauth_manager=manager, db=db)
        cog = AuthCog(bot)

        ctx_login = _make_ctx()
        await cog.claude_login.callback(cog, ctx_login)

        ctx_callback = _make_ctx()
        await cog.claude_callback.callback(cog, ctx_callback, "code#state")

        # Check action_log write
        db.execute.assert_awaited()
        sql_call = db.execute.call_args_list[-1]
        assert "action_log" in sql_call.args[0]
        assert "claude_oauth_login" in sql_call.args[1]


class TestClaudeLogout:
    """Tests for /claude-logout command."""

    @pytest.mark.asyncio
    async def test_logout_clears_tokens(self) -> None:
        """Logout removes tokens and reports success."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = True

        db = AsyncMock()
        bot = _make_bot(oauth_manager=manager, db=db)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_logout.callback(cog, ctx)

        manager.clear_tokens.assert_awaited_once()
        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "removed" in message.lower()

    @pytest.mark.asyncio
    async def test_logout_no_tokens(self) -> None:
        """Logout reports when no tokens exist."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False

        bot = _make_bot(oauth_manager=manager)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_logout.callback(cog, ctx)

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "no oauth tokens" in message.lower()

    @pytest.mark.asyncio
    async def test_logout_mentions_api_key_fallback(self) -> None:
        """Logout mentions API key fallback when one is configured."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = True

        db = AsyncMock()
        bot = _make_bot(oauth_manager=manager, api_key="sk-ant-api-key", db=db)
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_logout.callback(cog, ctx)

        call_args = ctx.send.call_args
        message = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "api key" in message.lower()


class TestClaudeStatus:
    """Tests for /claude-status command."""

    @pytest.mark.asyncio
    async def test_status_shows_oauth(self) -> None:
        """Status shows OAuth when tokens are present."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = True
        manager.get_access_token.return_value = "sk-ant-oat-valid"

        bot = _make_bot(oauth_manager=manager, api_key="")
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_status.callback(cog, ctx)

        call_kwargs = ctx.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "OAuth" in str(embed.fields[0].value)

    @pytest.mark.asyncio
    async def test_status_shows_api_key(self) -> None:
        """Status shows API key when no OAuth but key is set."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False

        bot = _make_bot(oauth_manager=manager, api_key="sk-ant-api-key")
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_status.callback(cog, ctx)

        call_kwargs = ctx.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "API Key" in str(embed.fields[0].value)

    @pytest.mark.asyncio
    async def test_status_shows_none(self) -> None:
        """Status shows not authenticated when nothing is configured."""
        manager = AsyncMock(spec=OAuthManager)
        manager.has_tokens.return_value = False

        bot = _make_bot(oauth_manager=manager, api_key="")
        cog = AuthCog(bot)
        ctx = _make_ctx()

        await cog.claude_status.callback(cog, ctx)

        call_kwargs = ctx.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert embed.color == discord.Color.red()
