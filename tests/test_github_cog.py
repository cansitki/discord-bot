"""Tests for bot.cogs.github — /link-repo, /unlink-repo, and tool-provider protocol.

Covers: link-repo success/error paths, unlink-repo success/error paths,
action_log entries, ChannelRepo database round-trip through real migration,
and tool-provider protocol (get_tools, handle_tool_call).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from bot.cogs.github import GitHubCog
from bot.github_client import GitHubAPIError
from bot.models import ActionLog, ChannelRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bot(
    *,
    db: AsyncMock | None = None,
    github_app_id: str | None = "12345",
    github_private_key: str | None = "fake-key",
) -> MagicMock:
    """Create a mock DiscordBot with config and db attributes.

    The GitHubClient is patched out at the module level so the cog __init__
    doesn't try to create a real client with the fake key.
    """
    bot = MagicMock()
    bot.db = db or AsyncMock()
    bot.config = MagicMock()
    bot.config.github_app_id = github_app_id
    bot.config.github_private_key = github_private_key
    return bot


def _make_mock_ctx(
    *,
    guild_id: int = 100,
    channel_id: int = 200,
    channel_name: str = "dev",
    author_id: int = 42,
) -> MagicMock:
    """Build a mock Context with guild, channel, and author."""
    ctx = MagicMock(spec=commands.Context)
    ctx.guild = MagicMock(spec=discord.Guild)
    ctx.guild.id = guild_id
    ctx.channel = MagicMock(spec=discord.TextChannel)
    ctx.channel.id = channel_id
    ctx.channel.name = channel_name
    ctx.author = MagicMock(spec=discord.Member)
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    return ctx


def _make_cog(
    *,
    db: AsyncMock | None = None,
    github_app_id: str | None = "12345",
    github_private_key: str | None = "fake-key",
    github_client: object | None = "auto",
) -> tuple[GitHubCog, MagicMock]:
    """Create a GitHubCog with a mock bot, patching GitHubClient construction.

    Returns (cog, bot_mock). If github_client is "auto", a mock client
    is created when config is present. Pass None to simulate missing config.
    """
    bot = _make_mock_bot(
        db=db,
        github_app_id=github_app_id,
        github_private_key=github_private_key,
    )

    # Patch GitHubClient so __init__ doesn't try real crypto
    with patch("bot.cogs.github.GitHubClient") as MockClient:
        mock_client_instance = MagicMock()
        MockClient.return_value = mock_client_instance
        cog = GitHubCog(bot)

    if github_client is None:
        cog.github_client = None
    elif github_client != "auto":
        cog.github_client = github_client

    return cog, bot


# ---------------------------------------------------------------------------
# Tool-provider protocol
# ---------------------------------------------------------------------------


class TestToolProvider:
    """GitHubCog implements get_tools() and handle_tool_call()."""

    def test_get_tools_returns_list(self):
        """get_tools() returns a list (empty for S01)."""
        cog, _ = _make_cog()
        tools = cog.get_tools()
        assert isinstance(tools, list)
        assert tools == []

    async def test_handle_tool_call_unknown_returns_error(self):
        """handle_tool_call() returns error string for unknown tools."""
        cog, _ = _make_cog()
        message = MagicMock(spec=discord.Message)
        result = await cog.handle_tool_call("nonexistent_tool", {}, message)
        assert "Unknown tool" in result
        assert "nonexistent_tool" in result

    async def test_handle_tool_call_any_name_returns_error(self):
        """handle_tool_call() returns error for any tool name in S01."""
        cog, _ = _make_cog()
        message = MagicMock(spec=discord.Message)
        result = await cog.handle_tool_call("create_issue", {"title": "test"}, message)
        assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# /link-repo — success
# ---------------------------------------------------------------------------


class TestLinkRepoSuccess:
    """link-repo command success path."""

    async def test_link_repo_success(self, db_with_migrations):
        """Successful link: validates repo, inserts binding, writes action_log, sends embed."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        # Mock get_repo to return repo data (repo exists)
        cog.github_client.get_repo = AsyncMock(return_value={
            "id": 1,
            "full_name": "octocat/hello-world",
            "private": False,
        })

        await cog.link_repo(cog, ctx, repo="octocat/hello-world")

        # Verify get_repo was called
        cog.github_client.get_repo.assert_awaited_once_with("octocat", "hello-world")

        # Verify channel_repos row inserted
        row = await db_with_migrations.fetchone(
            "SELECT * FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (100, 200),
        )
        assert row is not None
        cr = ChannelRepo.from_row(row)
        assert cr.repo_owner == "octocat"
        assert cr.repo_name == "hello-world"
        assert cr.linked_by == 42
        assert cr.repo_full_name == "octocat/hello-world"

        # Verify action_log entry
        log_row = await db_with_migrations.fetchone(
            "SELECT * FROM action_log WHERE action_type = ?",
            ("repo_linked",),
        )
        assert log_row is not None
        action = ActionLog.from_row(log_row)
        assert action.guild_id == 100
        assert action.user_id == 42
        assert action.target == "octocat/hello-world"
        assert "channel_id=200" in action.details

        # Verify confirmation embed sent
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert "octocat/hello-world" in embed.description
        assert "✅" in embed.description
        assert embed.colour == discord.Colour.green()


# ---------------------------------------------------------------------------
# /link-repo — error paths
# ---------------------------------------------------------------------------


class TestLinkRepoErrors:
    """link-repo command error paths."""

    async def test_link_repo_bad_format_no_slash(self):
        """Invalid format (no slash) sends error, no DB changes."""
        cog, bot = _make_cog()
        ctx = _make_mock_ctx()

        await cog.link_repo(cog, ctx, repo="not-a-repo")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "Invalid format" in msg
        assert "owner/repo" in msg

    async def test_link_repo_bad_format_empty_parts(self):
        """Invalid format (empty owner or repo) sends error."""
        cog, bot = _make_cog()
        ctx = _make_mock_ctx()

        await cog.link_repo(cog, ctx, repo="/repo-only")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "Invalid format" in msg

    async def test_link_repo_bad_format_too_many_slashes(self):
        """Invalid format (too many slashes) sends error."""
        cog, bot = _make_cog()
        ctx = _make_mock_ctx()

        await cog.link_repo(cog, ctx, repo="a/b/c")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "Invalid format" in msg

    async def test_link_repo_missing_config(self):
        """Missing GitHub config sends configuration error."""
        cog, bot = _make_cog(github_app_id=None, github_private_key=None, github_client=None)
        ctx = _make_mock_ctx()

        await cog.link_repo(cog, ctx, repo="octocat/hello-world")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "not configured" in msg

    async def test_link_repo_repo_not_found(self, db_with_migrations):
        """Repo not found (404) sends error, no DB changes."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        cog.github_client.get_repo = AsyncMock(
            side_effect=GitHubAPIError(404, "Not Found")
        )

        await cog.link_repo(cog, ctx, repo="octocat/nonexistent")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "not found" in msg

        # No database changes
        row = await db_with_migrations.fetchone(
            "SELECT * FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (100, 200),
        )
        assert row is None

    async def test_link_repo_api_error_non_404(self, db_with_migrations):
        """Non-404 API error sends error message with status code."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        cog.github_client.get_repo = AsyncMock(
            side_effect=GitHubAPIError(500, "Internal Server Error")
        )

        await cog.link_repo(cog, ctx, repo="octocat/hello-world")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "500" in msg

    async def test_link_repo_already_linked(self, db_with_migrations):
        """Already-linked channel sends error with existing repo name."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        cog.github_client.get_repo = AsyncMock(return_value={
            "id": 1,
            "full_name": "octocat/new-repo",
        })

        # Pre-insert existing binding
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "octocat", "existing-repo", 99),
        )

        await cog.link_repo(cog, ctx, repo="octocat/new-repo")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "already linked" in msg
        assert "octocat/existing-repo" in msg

    async def test_link_repo_no_guild(self):
        """Command outside a guild sends server-only message."""
        cog, bot = _make_cog()
        ctx = _make_mock_ctx()
        ctx.guild = None

        await cog.link_repo(cog, ctx, repo="octocat/hello-world")

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "server" in msg.lower()


# ---------------------------------------------------------------------------
# /unlink-repo — success
# ---------------------------------------------------------------------------


class TestUnlinkRepoSuccess:
    """unlink-repo command success path."""

    async def test_unlink_repo_success(self, db_with_migrations):
        """Successful unlink: deletes binding, writes action_log, sends embed."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        # Pre-insert a binding to unlink
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "octocat", "hello-world", 99),
        )

        await cog.unlink_repo(cog, ctx)

        # Verify binding deleted
        row = await db_with_migrations.fetchone(
            "SELECT * FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (100, 200),
        )
        assert row is None

        # Verify action_log entry
        log_row = await db_with_migrations.fetchone(
            "SELECT * FROM action_log WHERE action_type = ?",
            ("repo_unlinked",),
        )
        assert log_row is not None
        action = ActionLog.from_row(log_row)
        assert action.guild_id == 100
        assert action.user_id == 42
        assert action.target == "octocat/hello-world"
        assert "channel_id=200" in action.details

        # Verify confirmation embed sent
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert "octocat/hello-world" in embed.description
        assert "✅" in embed.description
        assert embed.colour == discord.Colour.green()


# ---------------------------------------------------------------------------
# /unlink-repo — error paths
# ---------------------------------------------------------------------------


class TestUnlinkRepoErrors:
    """unlink-repo command error paths."""

    async def test_unlink_repo_not_linked(self, db_with_migrations):
        """Unlink on a non-linked channel sends error."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx()

        await cog.unlink_repo(cog, ctx)

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "❌" in msg
        assert "not linked" in msg

    async def test_unlink_repo_no_guild(self):
        """Command outside a guild sends server-only message."""
        cog, bot = _make_cog()
        ctx = _make_mock_ctx()
        ctx.guild = None

        await cog.unlink_repo(cog, ctx)

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "server" in msg.lower()

    async def test_unlink_repo_no_db(self):
        """Command with no database sends server-only message."""
        cog, bot = _make_cog()
        bot.db = None
        cog.bot = bot
        ctx = _make_mock_ctx()

        await cog.unlink_repo(cog, ctx)

        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        assert "server" in msg.lower()


# ---------------------------------------------------------------------------
# Action log entries — verified through integration
# ---------------------------------------------------------------------------


class TestActionLogEntries:
    """action_log entries are written for both link and unlink actions."""

    async def test_link_writes_repo_linked_action(self, db_with_migrations):
        """link-repo writes action_type='repo_linked' with correct fields."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx(guild_id=500, channel_id=600, author_id=77)

        cog.github_client.get_repo = AsyncMock(return_value={"id": 1})

        await cog.link_repo(cog, ctx, repo="my-org/my-repo")

        rows = await db_with_migrations.fetchall(
            "SELECT * FROM action_log WHERE action_type = 'repo_linked'"
        )
        assert len(rows) == 1
        action = ActionLog.from_row(rows[0])
        assert action.guild_id == 500
        assert action.user_id == 77
        assert action.action_type == "repo_linked"
        assert action.target == "my-org/my-repo"
        assert "channel_id=600" in action.details
        assert action.timestamp is not None

    async def test_unlink_writes_repo_unlinked_action(self, db_with_migrations):
        """unlink-repo writes action_type='repo_unlinked' with correct fields."""
        cog, bot = _make_cog(db=db_with_migrations)
        ctx = _make_mock_ctx(guild_id=500, channel_id=600, author_id=77)

        # Pre-insert binding
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (500, 600, "my-org", "my-repo", 99),
        )

        await cog.unlink_repo(cog, ctx)

        rows = await db_with_migrations.fetchall(
            "SELECT * FROM action_log WHERE action_type = 'repo_unlinked'"
        )
        assert len(rows) == 1
        action = ActionLog.from_row(rows[0])
        assert action.guild_id == 500
        assert action.user_id == 77
        assert action.action_type == "repo_unlinked"
        assert action.target == "my-org/my-repo"
        assert "channel_id=600" in action.details


# ---------------------------------------------------------------------------
# ChannelRepo database round-trip through migration
# ---------------------------------------------------------------------------


class TestChannelRepoMigrationRoundTrip:
    """ChannelRepo round-trip verified through the real 003 migration."""

    async def test_insert_fetch_round_trip(self, db_with_migrations):
        """Insert a channel_repos row, fetch it, verify all fields via ChannelRepo."""
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (111, 222, "test-org", "test-repo", 333),
        )

        row = await db_with_migrations.fetchone(
            "SELECT * FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (111, 222),
        )
        assert row is not None
        cr = ChannelRepo.from_row(row)
        assert cr.guild_id == 111
        assert cr.channel_id == 222
        assert cr.repo_owner == "test-org"
        assert cr.repo_name == "test-repo"
        assert cr.linked_by == 333
        assert cr.linked_at is not None  # DB default sets datetime('now')
        assert cr.repo_full_name == "test-org/test-repo"

    async def test_primary_key_constraint(self, db_with_migrations):
        """channel_repos PK on (guild_id, channel_id) rejects duplicates."""
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, 2, "owner-a", "repo-a", 10),
        )

        with pytest.raises(sqlite3.IntegrityError):
            await db_with_migrations.execute(
                "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, 2, "owner-b", "repo-b", 20),
            )

    async def test_different_channels_same_guild(self, db_with_migrations):
        """Different channels in the same guild can link different repos."""
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 1, "org", "repo-1", 42),
        )
        await db_with_migrations.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 2, "org", "repo-2", 42),
        )

        rows = await db_with_migrations.fetchall(
            "SELECT * FROM channel_repos WHERE guild_id = ?",
            (100,),
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# GitHubCog init — config gate
# ---------------------------------------------------------------------------


class TestCogInit:
    """GitHubCog init behaviour with and without GitHub config."""

    def test_client_created_when_config_present(self):
        """GitHubClient is created when both app_id and private_key are set."""
        with patch("bot.cogs.github.GitHubClient") as MockClient:
            bot = _make_mock_bot(github_app_id="123", github_private_key="key")
            cog = GitHubCog(bot)
            MockClient.assert_called_once_with(app_id="123", private_key="key")
            assert cog.github_client is not None

    def test_client_none_when_app_id_missing(self):
        """GitHubClient is None when github_app_id is missing."""
        with patch("bot.cogs.github.GitHubClient") as MockClient:
            bot = _make_mock_bot(github_app_id=None, github_private_key="key")
            cog = GitHubCog(bot)
            MockClient.assert_not_called()
            assert cog.github_client is None

    def test_client_none_when_private_key_missing(self):
        """GitHubClient is None when github_private_key is missing."""
        with patch("bot.cogs.github.GitHubClient") as MockClient:
            bot = _make_mock_bot(github_app_id="123", github_private_key=None)
            cog = GitHubCog(bot)
            MockClient.assert_not_called()
            assert cog.github_client is None

    def test_client_none_when_both_missing(self):
        """GitHubClient is None when both config values are missing."""
        with patch("bot.cogs.github.GitHubClient") as MockClient:
            bot = _make_mock_bot(github_app_id=None, github_private_key=None)
            cog = GitHubCog(bot)
            MockClient.assert_not_called()
            assert cog.github_client is None
