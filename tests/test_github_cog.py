"""Tests for bot.cogs.github — /link-repo, /unlink-repo, and tool-provider protocol.

Covers: link-repo success/error paths, unlink-repo success/error paths,
action_log entries, ChannelRepo database round-trip through real migration,
tool-provider protocol (get_tools, handle_tool_call), create_issue handler,
IssueApproveButton / IssueCancelButton DynamicItem callbacks.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from bot.cogs.github import (
    CREATE_ISSUE_TOOL,
    GitHubCog,
    IssueApproveButton,
    IssueCancelButton,
    make_issue_view,
)
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

    def test_get_tools_returns_create_issue_tool(self):
        """get_tools() returns [CREATE_ISSUE_TOOL] when github_client is present."""
        cog, _ = _make_cog()
        tools = cog.get_tools()
        assert isinstance(tools, list)
        assert len(tools) == 1
        assert tools[0] is CREATE_ISSUE_TOOL

    def test_get_tools_returns_empty_when_no_client(self):
        """get_tools() returns [] when github_client is None."""
        cog, _ = _make_cog(github_client=None)
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

    async def test_handle_create_issue_routes_correctly(self):
        """handle_tool_call('create_issue', ...) routes to _handle_create_issue."""
        cog, _ = _make_cog()
        cog._handle_create_issue = AsyncMock(return_value="routed")
        message = MagicMock(spec=discord.Message)
        tool_input = {"title": "Test", "body": "Test body"}
        result = await cog.handle_tool_call("create_issue", tool_input, message)
        assert result == "routed"
        cog._handle_create_issue.assert_awaited_once_with(tool_input, message)


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

    def test_pending_issues_empty_on_init(self):
        """_pending_issues is an empty dict on init."""
        cog, _ = _make_cog()
        assert cog._pending_issues == {}


# ---------------------------------------------------------------------------
# CREATE_ISSUE_TOOL schema
# ---------------------------------------------------------------------------


class TestCreateIssueTool:
    """CREATE_ISSUE_TOOL dict conforms to Anthropic tool format."""

    def test_tool_has_required_keys(self):
        assert "name" in CREATE_ISSUE_TOOL
        assert "description" in CREATE_ISSUE_TOOL
        assert "input_schema" in CREATE_ISSUE_TOOL

    def test_tool_name_is_create_issue(self):
        assert CREATE_ISSUE_TOOL["name"] == "create_issue"

    def test_input_schema_requires_title_and_body(self):
        schema = CREATE_ISSUE_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "title" in schema["properties"]
        assert "body" in schema["properties"]
        assert "title" in schema["required"]
        assert "body" in schema["required"]

    def test_labels_is_optional_array(self):
        schema = CREATE_ISSUE_TOOL["input_schema"]
        assert "labels" in schema["properties"]
        labels_schema = schema["properties"]["labels"]
        assert labels_schema["type"] == "array"
        assert labels_schema["items"] == {"type": "string"}
        assert "labels" not in schema["required"]


# ---------------------------------------------------------------------------
# _handle_create_issue handler
# ---------------------------------------------------------------------------


def _make_mock_message(
    *,
    guild_id: int = 100,
    channel_id: int = 200,
    author_id: int = 42,
    sent_msg_id: int = 555,
) -> MagicMock:
    """Build a mock discord.Message suitable for _handle_create_issue."""
    message = MagicMock(spec=discord.Message)
    message.guild = MagicMock(spec=discord.Guild)
    message.guild.id = guild_id
    message.channel = MagicMock(spec=discord.TextChannel)
    message.channel.id = channel_id
    message.author = MagicMock(spec=discord.Member)
    message.author.id = author_id

    sent_msg = MagicMock(spec=discord.Message)
    sent_msg.id = sent_msg_id
    sent_msg.edit = AsyncMock()
    message.channel.send = AsyncMock(return_value=sent_msg)
    return message


class TestHandleCreateIssue:
    """_handle_create_issue handler tests."""

    async def test_channel_not_linked_returns_error(self):
        """When no channel_repos row exists, returns 'not linked' error."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value=None)
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        result = await cog._handle_create_issue(
            {"title": "Bug", "body": "It crashes"}, message
        )
        assert "linked" in result.lower()
        assert "/link-repo" in result

    async def test_no_guild_returns_error(self):
        """When message.guild is None, returns server-only error."""
        cog, _ = _make_cog()
        message = _make_mock_message()
        message.guild = None

        result = await cog._handle_create_issue(
            {"title": "Bug", "body": "It crashes"}, message
        )
        assert "server" in result.lower()

    async def test_preview_embed_has_title_body_labels(self):
        """Preview embed contains issue title, body, labels, and repo footer."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "octocat",
            "repo_name": "hello-world",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        await cog._handle_create_issue(
            {"title": "My Bug", "body": "Details here", "labels": ["bug", "urgent"]},
            message,
        )

        # Verify embed sent
        message.channel.send.assert_awaited_once()
        call_kwargs = message.channel.send.call_args.kwargs
        embed = call_kwargs["embed"]
        assert embed.title == "My Bug"
        assert "Details here" in embed.description
        assert embed.colour == discord.Colour.blue()
        # Labels field
        labels_field = next(f for f in embed.fields if f.name == "Labels")
        assert "bug" in labels_field.value
        assert "urgent" in labels_field.value
        # Footer
        assert "octocat/hello-world" in embed.footer.text

    async def test_preview_embed_truncates_long_body(self):
        """Body >200 chars is truncated with '...' in the preview."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "org",
            "repo_name": "repo",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        long_body = "x" * 300
        await cog._handle_create_issue(
            {"title": "Title", "body": long_body},
            message,
        )

        call_kwargs = message.channel.send.call_args.kwargs
        embed = call_kwargs["embed"]
        assert len(embed.description) == 203  # 200 chars + "..."
        assert embed.description.endswith("...")

    async def test_preview_embed_labels_none_when_empty(self):
        """Labels field shows 'None' when no labels provided."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "org",
            "repo_name": "repo",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        await cog._handle_create_issue(
            {"title": "Title", "body": "Body"},
            message,
        )

        call_kwargs = message.channel.send.call_args.kwargs
        embed = call_kwargs["embed"]
        labels_field = next(f for f in embed.fields if f.name == "Labels")
        assert labels_field.value == "None"

    async def test_pending_issue_stored(self):
        """After handler, _pending_issues has the correct entry."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "octocat",
            "repo_name": "hello-world",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message(guild_id=100, sent_msg_id=555, author_id=42)

        await cog._handle_create_issue(
            {"title": "Bug", "body": "Details", "labels": ["bug"]},
            message,
        )

        key = (100, 555)
        assert key in cog._pending_issues
        pending = cog._pending_issues[key]
        assert pending["title"] == "Bug"
        assert pending["body"] == "Details"
        assert pending["labels"] == ["bug"]
        assert pending["repo_owner"] == "octocat"
        assert pending["repo_name"] == "hello-world"
        assert pending["user_id"] == 42

    async def test_preview_sent_with_buttons(self):
        """Message is edited with a View containing buttons after send."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "org",
            "repo_name": "repo",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        await cog._handle_create_issue(
            {"title": "Title", "body": "Body"},
            message,
        )

        # Embed sent first
        message.channel.send.assert_awaited_once()
        # Then edited with view
        sent_msg = message.channel.send.return_value
        sent_msg.edit.assert_awaited_once()
        call_kwargs = sent_msg.edit.call_args.kwargs
        view = call_kwargs["view"]
        assert len(view.children) == 2

    async def test_handler_returns_confirmation_string(self):
        """Handler returns a string mentioning the issue title and repo."""
        db = AsyncMock()
        db.fetchone = AsyncMock(return_value={
            "repo_owner": "octocat",
            "repo_name": "hello-world",
        })
        cog, _ = _make_cog(db=db)
        message = _make_mock_message()

        result = await cog._handle_create_issue(
            {"title": "My Feature", "body": "Details"},
            message,
        )

        assert "My Feature" in result
        assert "octocat/hello-world" in result
        assert "Approve" in result


# ---------------------------------------------------------------------------
# DynamicItem buttons — issue approve/cancel
# ---------------------------------------------------------------------------


class TestIssueDynamicItems:
    """IssueApproveButton and IssueCancelButton use the DynamicItem pattern."""

    def test_approve_button_custom_id_format(self):
        btn = IssueApproveButton(guild_id=123, msg_id=456)
        assert btn.item.custom_id == "issue:approve:123:456"

    def test_cancel_button_custom_id_format(self):
        btn = IssueCancelButton(guild_id=123, msg_id=456)
        assert btn.item.custom_id == "issue:cancel:123:456"

    def test_approve_button_style_is_green(self):
        btn = IssueApproveButton(guild_id=1, msg_id=2)
        assert btn.item.style == discord.ButtonStyle.green

    def test_cancel_button_style_is_red(self):
        btn = IssueCancelButton(guild_id=1, msg_id=2)
        assert btn.item.style == discord.ButtonStyle.red

    def test_make_issue_view_has_two_items(self):
        view = make_issue_view(guild_id=100, msg_id=200)
        assert len(view.children) == 2

    def test_approve_button_label(self):
        btn = IssueApproveButton(guild_id=1, msg_id=2)
        assert btn.item.label == "Approve"

    def test_cancel_button_label(self):
        btn = IssueCancelButton(guild_id=1, msg_id=2)
        assert btn.item.label == "Cancel"


# ---------------------------------------------------------------------------
# Approve callback
# ---------------------------------------------------------------------------


def _make_approve_interaction(
    *,
    guild_id: int = 100,
    user_id: int = 42,
) -> MagicMock:
    """Build a mock Interaction for approve/cancel callbacks."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = guild_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


class TestIssueApproveCallback:
    """IssueApproveButton.callback handles requester check and issue creation."""

    @pytest.mark.asyncio()
    async def test_approve_calls_create_issue_and_writes_action_log(self):
        """Approve creates issue, writes action_log, edits message with URL."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        cog.github_client = AsyncMock()
        cog.github_client.create_issue = AsyncMock(return_value={
            "number": 42,
            "html_url": "https://github.com/octocat/hello-world/issues/42",
        })

        key = (100, 200)
        cog._pending_issues[key] = {
            "title": "Bug report",
            "body": "It crashes",
            "labels": ["bug"],
            "repo_owner": "octocat",
            "repo_name": "hello-world",
            "user_id": 55,
        }

        btn = IssueApproveButton(guild_id=100, msg_id=200)
        interaction = _make_approve_interaction(guild_id=100, user_id=55)
        interaction.client = mock_bot

        await btn.callback(interaction)

        # create_issue called with correct args
        cog.github_client.create_issue.assert_awaited_once_with(
            owner="octocat",
            repo="hello-world",
            title="Bug report",
            body="It crashes",
            labels=["bug"],
        )

        # action_log written
        mock_bot.db.execute.assert_awaited()
        action_log_call = mock_bot.db.execute.call_args_list[-1]
        params = action_log_call[0][1]
        assert params[2] == "issue_created"
        assert params[3] == "octocat/hello-world#42"
        assert "github.com" in params[4]

        # Message edited with success
        interaction.response.edit_message.assert_awaited_once()
        content = interaction.response.edit_message.call_args.kwargs["content"]
        assert "✅" in content
        assert "Bug report" in content
        assert "github.com" in content

        # Removed from pending
        assert key not in cog._pending_issues

    @pytest.mark.asyncio()
    async def test_approve_expired_sends_ephemeral(self):
        """Approve on an expired preview sends ephemeral message."""
        mock_bot = MagicMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        btn = IssueApproveButton(guild_id=100, msg_id=999)
        interaction = _make_approve_interaction(user_id=42)
        interaction.client = mock_bot

        await btn.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_args = interaction.response.send_message.call_args
        assert "expired" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio()
    async def test_approve_wrong_user_rejected(self):
        """Approve by a user who didn't request the issue is rejected."""
        mock_bot = MagicMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        key = (100, 200)
        cog._pending_issues[key] = {
            "title": "Bug",
            "body": "Body",
            "labels": [],
            "repo_owner": "org",
            "repo_name": "repo",
            "user_id": 55,  # original requester
        }

        btn = IssueApproveButton(guild_id=100, msg_id=200)
        interaction = _make_approve_interaction(user_id=99)  # different user
        interaction.client = mock_bot

        await btn.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        call_args = interaction.response.send_message.call_args
        assert "requester" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral") is True

        # Pending NOT removed
        assert key in cog._pending_issues

    @pytest.mark.asyncio()
    async def test_approve_api_error_reports_to_user(self):
        """GitHubAPIError on create_issue edits message with error details."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        cog.github_client = AsyncMock()
        cog.github_client.create_issue = AsyncMock(
            side_effect=GitHubAPIError(422, "Validation Failed")
        )

        key = (100, 200)
        cog._pending_issues[key] = {
            "title": "Bug",
            "body": "Body",
            "labels": ["invalid-label"],
            "repo_owner": "org",
            "repo_name": "repo",
            "user_id": 42,
        }

        btn = IssueApproveButton(guild_id=100, msg_id=200)
        interaction = _make_approve_interaction(user_id=42)
        interaction.client = mock_bot

        await btn.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        content = interaction.response.edit_message.call_args.kwargs["content"]
        assert "❌" in content
        assert "422" in content
        assert "Validation Failed" in content

    @pytest.mark.asyncio()
    async def test_approve_no_labels_passes_none(self):
        """Approve with empty labels list passes None to create_issue."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        cog.github_client = AsyncMock()
        cog.github_client.create_issue = AsyncMock(return_value={
            "number": 1,
            "html_url": "https://github.com/org/repo/issues/1",
        })

        key = (100, 200)
        cog._pending_issues[key] = {
            "title": "Feature",
            "body": "Add X",
            "labels": [],
            "repo_owner": "org",
            "repo_name": "repo",
            "user_id": 42,
        }

        btn = IssueApproveButton(guild_id=100, msg_id=200)
        interaction = _make_approve_interaction(user_id=42)
        interaction.client = mock_bot

        await btn.callback(interaction)

        # labels=[] becomes None (falsy)
        cog.github_client.create_issue.assert_awaited_once_with(
            owner="org",
            repo="repo",
            title="Feature",
            body="Add X",
            labels=None,
        )


# ---------------------------------------------------------------------------
# Cancel callback
# ---------------------------------------------------------------------------


class TestIssueCancelCallback:
    """IssueCancelButton.callback removes pending and updates message."""

    @pytest.mark.asyncio()
    async def test_cancel_removes_pending_and_edits_message(self):
        """Cancel removes the pending issue and edits message."""
        mock_bot = MagicMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        key = (100, 200)
        cog._pending_issues[key] = {
            "title": "Bug",
            "body": "Body",
            "labels": [],
            "repo_owner": "org",
            "repo_name": "repo",
            "user_id": 42,
        }

        btn = IssueCancelButton(guild_id=100, msg_id=200)
        interaction = _make_approve_interaction(user_id=42)
        interaction.client = mock_bot

        await btn.callback(interaction)

        # Pending removed
        assert key not in cog._pending_issues
        # Message updated
        interaction.response.edit_message.assert_awaited_once()
        call_kwargs = interaction.response.edit_message.call_args.kwargs
        assert "cancelled" in call_kwargs["content"].lower()
        assert call_kwargs["embed"] is None
        assert call_kwargs["view"] is None


# ---------------------------------------------------------------------------
# action_log for issue_created
# ---------------------------------------------------------------------------


class TestIssueCreatedActionLog:
    """action_log entry for issue_created has correct fields."""

    @pytest.mark.asyncio()
    async def test_issue_created_action_log_entry(self):
        """Verify action_type, target, and details of the action_log INSERT."""
        mock_bot = MagicMock()
        mock_bot.db = AsyncMock()
        cog, _ = _make_cog()
        cog.bot = mock_bot
        mock_bot.get_cog = MagicMock(return_value=cog)

        cog.github_client = AsyncMock()
        cog.github_client.create_issue = AsyncMock(return_value={
            "number": 7,
            "html_url": "https://github.com/my-org/my-repo/issues/7",
        })

        key = (500, 600)
        cog._pending_issues[key] = {
            "title": "Feature request",
            "body": "Please add X",
            "labels": ["enhancement"],
            "repo_owner": "my-org",
            "repo_name": "my-repo",
            "user_id": 77,
        }

        btn = IssueApproveButton(guild_id=500, msg_id=600)
        interaction = _make_approve_interaction(guild_id=500, user_id=77)
        interaction.client = mock_bot

        await btn.callback(interaction)

        # Find the action_log INSERT call
        action_log_call = mock_bot.db.execute.call_args_list[-1]
        sql = action_log_call[0][0]
        params = action_log_call[0][1]

        assert "action_log" in sql
        assert params[0] == 500  # guild_id
        assert params[1] == 77   # user_id
        assert params[2] == "issue_created"  # action_type
        assert params[3] == "my-org/my-repo#7"  # target
        assert params[4] == "https://github.com/my-org/my-repo/issues/7"  # details
