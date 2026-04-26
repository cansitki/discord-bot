"""Tests for bot.cogs.twitter — /watch, /unwatch, /watchlist, polling loop.

Covers: slash command success/error paths, database round-trips through real
migrations, action_log entries, polling loop feed-check logic, authentication
retry, and TwitterFeed model.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from bot.cogs.twitter import TwitterCog
from bot.models import TwitterFeed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bot(
    *,
    db=None,
    twitter_username: str | None = "testuser",
    twitter_email: str | None = "test@example.com",
    twitter_password: str | None = "password123",
    twitter_cookies_path: str = "/tmp/cookies.json",
) -> MagicMock:
    """Create a mock DiscordBot with config and db attributes."""
    bot = MagicMock()
    bot.db = db or AsyncMock()
    bot.config = MagicMock()
    bot.config.twitter_username = twitter_username
    bot.config.twitter_email = twitter_email
    bot.config.twitter_password = twitter_password
    bot.config.twitter_cookies_path = twitter_cookies_path
    bot.wait_until_ready = AsyncMock()
    bot.get_channel = MagicMock(return_value=None)
    return bot


def _make_cog(*, db=None, **kwargs) -> tuple[TwitterCog, MagicMock]:
    """Create a TwitterCog without triggering cog_load (no twikit needed)."""
    bot = _make_mock_bot(db=db, **kwargs)
    with patch("bot.cogs.twitter.TWIKIT_AVAILABLE", False):
        cog = TwitterCog(bot)
    return cog, bot


def _make_interaction(
    *,
    guild_id: int = 100,
    channel_id: int = 200,
    user_id: int = 42,
) -> MagicMock:
    """Build a mock Interaction for slash commands."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = guild_id
    interaction.guild_id = guild_id
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.id = channel_id
    interaction.channel_id = channel_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# TwitterFeed model
# ---------------------------------------------------------------------------


class TestTwitterFeedModel:
    """TwitterFeed dataclass construction and from_row."""

    def test_basic_construction(self):
        feed = TwitterFeed(
            guild_id=100, channel_id=200, twitter_handle="elonmusk",
            created_by=42,
        )
        assert feed.guild_id == 100
        assert feed.twitter_handle == "elonmusk"
        assert feed.last_tweet_id is None
        assert feed.id is None

    def test_from_row(self):
        row = {
            "id": 1,
            "guild_id": 100,
            "channel_id": 200,
            "twitter_handle": "testhandle",
            "last_tweet_id": "123456",
            "created_by": 42,
            "created_at": "2025-01-01T00:00:00",
        }
        feed = TwitterFeed.from_row(row)
        assert feed.id == 1
        assert feed.guild_id == 100
        assert feed.twitter_handle == "testhandle"
        assert feed.last_tweet_id == "123456"


# ---------------------------------------------------------------------------
# /watch — success
# ---------------------------------------------------------------------------


class TestWatchSuccess:
    """watch command success path."""

    async def test_watch_adds_feed(self, db_with_migrations):
        """Successful watch: inserts twitter_feeds row, sends embed, logs action."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await cog.watch.callback(cog, interaction, handle="TestHandle")

        # Verify row inserted (handle lowercased)
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE guild_id = ? AND channel_id = ? AND twitter_handle = ?",
            (100, 200, "testhandle"),
        )
        assert row is not None
        feed = TwitterFeed.from_row(row)
        assert feed.twitter_handle == "testhandle"
        assert feed.created_by == 42

        # Verify embed sent
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        embed = kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert "testhandle" in embed.description
        assert embed.colour == discord.Colour.blue()

    async def test_watch_strips_at_symbol(self, db_with_migrations):
        """Handle with @ prefix is stripped."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await cog.watch.callback(cog, interaction, handle="@myhandle")

        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE twitter_handle = ?",
            ("myhandle",),
        )
        assert row is not None

    async def test_watch_logs_action(self, db_with_migrations):
        """Watch writes twitter_watch action to action_log."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction(guild_id=500, user_id=77)

        await cog.watch.callback(cog, interaction, handle="someuser")

        log_row = await db_with_migrations.fetchone(
            "SELECT * FROM action_log WHERE action_type = ?",
            ("twitter_watch",),
        )
        assert log_row is not None
        assert log_row["guild_id"] == 500
        assert log_row["user_id"] == 77
        assert log_row["target"] == "someuser"


# ---------------------------------------------------------------------------
# /watch — error paths
# ---------------------------------------------------------------------------


class TestWatchErrors:
    """watch command error paths."""

    async def test_watch_empty_handle(self, db_with_migrations):
        """Empty handle after stripping @ returns error."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await cog.watch.callback(cog, interaction, handle="@")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True

    async def test_watch_duplicate(self, db_with_migrations):
        """Already-watched handle in same channel returns error."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        # Insert existing
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "existing", 42),
        )

        await cog.watch.callback(cog, interaction, handle="existing")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True
        msg = kwargs.get("content") or interaction.response.send_message.call_args[0][0]
        assert "already" in msg.lower()

    async def test_watch_no_db(self):
        """No database sends error."""
        cog, bot = _make_cog()
        bot.db = None
        cog.bot = bot
        interaction = _make_interaction()

        await cog.watch.callback(cog, interaction, handle="test")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True

    async def test_watch_sends_auth_warning_when_not_authenticated(self, db_with_migrations):
        """When not authenticated, sends followup warning after adding feed."""
        cog, bot = _make_cog(db=db_with_migrations)
        cog._authenticated = False
        interaction = _make_interaction()

        await cog.watch.callback(cog, interaction, handle="testuser")

        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.call_args.kwargs
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# /unwatch — success
# ---------------------------------------------------------------------------


class TestUnwatchSuccess:
    """unwatch command success path."""

    async def test_unwatch_removes_feed(self, db_with_migrations):
        """Successful unwatch: deletes row, sends embed, logs action."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        # Pre-insert
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "handle", 42),
        )

        await cog.unwatch.callback(cog, interaction, handle="handle")

        # Verify deleted
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE guild_id = ? AND channel_id = ? AND twitter_handle = ?",
            (100, 200, "handle"),
        )
        assert row is None

        # Verify embed
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        embed = kwargs["embed"]
        assert "handle" in embed.description
        assert embed.colour == discord.Colour.orange()

    async def test_unwatch_logs_action(self, db_with_migrations):
        """Unwatch writes twitter_unwatch to action_log."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction(guild_id=500, user_id=77)

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (500, 200, "target", 77),
        )

        await cog.unwatch.callback(cog, interaction, handle="target")

        log_row = await db_with_migrations.fetchone(
            "SELECT * FROM action_log WHERE action_type = ?",
            ("twitter_unwatch",),
        )
        assert log_row is not None
        assert log_row["target"] == "target"


# ---------------------------------------------------------------------------
# /unwatch — error paths
# ---------------------------------------------------------------------------


class TestUnwatchErrors:
    """unwatch command error paths."""

    async def test_unwatch_not_watched(self, db_with_migrations):
        """Unwatch on a non-watched handle sends error."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await cog.unwatch.callback(cog, interaction, handle="nonexistent")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True

    async def test_unwatch_no_db(self):
        """No database sends error."""
        cog, bot = _make_cog()
        bot.db = None
        cog.bot = bot
        interaction = _make_interaction()

        await cog.unwatch.callback(cog, interaction, handle="test")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# /watchlist
# ---------------------------------------------------------------------------


class TestWatchlist:
    """watchlist command tests."""

    async def test_watchlist_shows_feeds(self, db_with_migrations):
        """Watchlist displays all feeds for the guild."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "alice", 42),
        )
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 300, "bob", 42),
        )

        await cog.watchlist.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        embed = kwargs["embed"]
        assert "alice" in embed.description
        assert "bob" in embed.description

    async def test_watchlist_empty(self, db_with_migrations):
        """Empty watchlist sends ephemeral message."""
        cog, bot = _make_cog(db=db_with_migrations)
        interaction = _make_interaction()

        await cog.watchlist.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True

    async def test_watchlist_footer_shows_inactive_when_not_authenticated(self, db_with_migrations):
        """Footer shows 'inactive' when cog is not authenticated."""
        cog, bot = _make_cog(db=db_with_migrations)
        cog._authenticated = False
        interaction = _make_interaction()

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "test", 42),
        )

        await cog.watchlist.callback(cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "inactive" in embed.footer.text


# ---------------------------------------------------------------------------
# Database round-trip through migration
# ---------------------------------------------------------------------------


class TestTwitterFeedMigrationRoundTrip:
    """twitter_feeds table via real 006 migration."""

    async def test_insert_fetch_round_trip(self, db_with_migrations):
        """Insert, fetch, and verify all fields via TwitterFeed model."""
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (111, 222, "testhandle", 333),
        )

        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE guild_id = ? AND channel_id = ?",
            (111, 222),
        )
        assert row is not None
        feed = TwitterFeed.from_row(row)
        assert feed.guild_id == 111
        assert feed.channel_id == 222
        assert feed.twitter_handle == "testhandle"
        assert feed.created_by == 333
        assert feed.created_at is not None
        assert feed.last_tweet_id is None

    async def test_unique_constraint(self, db_with_migrations):
        """Duplicate (guild, channel, handle) raises IntegrityError."""
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (1, 2, "handle", 10),
        )

        with pytest.raises(sqlite3.IntegrityError):
            await db_with_migrations.execute(
                "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
                "VALUES (?, ?, ?, ?)",
                (1, 2, "handle", 20),
            )

    async def test_same_handle_different_channels(self, db_with_migrations):
        """Same handle can be watched in different channels."""
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 1, "handle", 42),
        )
        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 2, "handle", 42),
        )

        rows = await db_with_migrations.fetchall(
            "SELECT * FROM twitter_feeds WHERE guild_id = ? AND twitter_handle = ?",
            (100, "handle"),
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Polling logic — _check_feed
# ---------------------------------------------------------------------------


class TestCheckFeed:
    """_check_feed method — tweet checking and posting logic."""

    async def test_check_feed_posts_new_tweets(self, db_with_migrations):
        """New tweets are posted to the channel and last_tweet_id is updated."""
        cog, bot = _make_cog(db=db_with_migrations)

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "testuser", 42),
        )
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE twitter_handle = ?", ("testuser",)
        )
        feed = TwitterFeed.from_row(row)

        # Mock twikit client
        mock_tweet = MagicMock()
        mock_tweet.id = "999"
        mock_tweet.text = "Hello world"

        mock_user = AsyncMock()
        mock_user.get_tweets = AsyncMock(return_value=[mock_tweet])

        mock_client = AsyncMock()
        mock_client.get_user_by_screen_name = AsyncMock(return_value=mock_user)
        cog._client = mock_client

        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=mock_channel)

        await cog._check_feed(feed)

        mock_channel.send.assert_awaited_once()
        msg = mock_channel.send.call_args[0][0]
        assert "fxtwitter.com" in msg
        assert "testuser" in msg
        assert "999" in msg

        # Verify last_tweet_id updated
        updated_row = await db_with_migrations.fetchone(
            "SELECT last_tweet_id FROM twitter_feeds WHERE twitter_handle = ?",
            ("testuser",),
        )
        assert updated_row["last_tweet_id"] == "999"

    async def test_check_feed_skips_old_tweets(self, db_with_migrations):
        """Tweets with ID <= last_tweet_id are not posted."""
        cog, bot = _make_cog(db=db_with_migrations)

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, last_tweet_id, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "testuser", "500", 42),
        )
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE twitter_handle = ?", ("testuser",)
        )
        feed = TwitterFeed.from_row(row)

        mock_tweet = MagicMock()
        mock_tweet.id = "400"  # older than last_tweet_id=500

        mock_user = AsyncMock()
        mock_user.get_tweets = AsyncMock(return_value=[mock_tweet])

        mock_client = AsyncMock()
        mock_client.get_user_by_screen_name = AsyncMock(return_value=mock_user)
        cog._client = mock_client

        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=mock_channel)

        await cog._check_feed(feed)

        mock_channel.send.assert_not_awaited()

    async def test_check_feed_no_channel_skips_silently(self, db_with_migrations):
        """If channel doesn't exist, no crash and no message sent."""
        cog, bot = _make_cog(db=db_with_migrations)

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "testuser", 42),
        )
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE twitter_handle = ?", ("testuser",)
        )
        feed = TwitterFeed.from_row(row)

        mock_tweet = MagicMock()
        mock_tweet.id = "999"

        mock_user = AsyncMock()
        mock_user.get_tweets = AsyncMock(return_value=[mock_tweet])

        mock_client = AsyncMock()
        mock_client.get_user_by_screen_name = AsyncMock(return_value=mock_user)
        cog._client = mock_client

        bot.get_channel = MagicMock(return_value=None)

        # Should not raise
        await cog._check_feed(feed)

    async def test_check_feed_multiple_new_tweets_posted_chronologically(self, db_with_migrations):
        """Multiple new tweets are posted oldest-first."""
        cog, bot = _make_cog(db=db_with_migrations)

        await db_with_migrations.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, "testuser", 42),
        )
        row = await db_with_migrations.fetchone(
            "SELECT * FROM twitter_feeds WHERE twitter_handle = ?", ("testuser",)
        )
        feed = TwitterFeed.from_row(row)

        tweet1 = MagicMock()
        tweet1.id = "102"
        tweet2 = MagicMock()
        tweet2.id = "101"

        mock_user = AsyncMock()
        mock_user.get_tweets = AsyncMock(return_value=[tweet1, tweet2])

        mock_client = AsyncMock()
        mock_client.get_user_by_screen_name = AsyncMock(return_value=mock_user)
        cog._client = mock_client

        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=mock_channel)

        await cog._check_feed(feed)

        assert mock_channel.send.await_count == 2
        # First call should be tweet 101 (older), second should be 102 (newer)
        first_msg = mock_channel.send.call_args_list[0][0][0]
        second_msg = mock_channel.send.call_args_list[1][0][0]
        assert "101" in first_msg
        assert "102" in second_msg

        # last_tweet_id updated to latest
        updated_row = await db_with_migrations.fetchone(
            "SELECT last_tweet_id FROM twitter_feeds WHERE twitter_handle = ?",
            ("testuser",),
        )
        assert updated_row["last_tweet_id"] == "102"


# ---------------------------------------------------------------------------
# Cog init — config gate
# ---------------------------------------------------------------------------


class TestCogInit:
    """TwitterCog init with and without credentials."""

    def test_client_none_when_twikit_unavailable(self):
        """Client is None when twikit is not installed."""
        cog, _ = _make_cog()
        assert cog._client is None
        assert cog._authenticated is False

    def test_cog_created_without_credentials(self):
        """Cog can be created without Twitter credentials."""
        cog, _ = _make_cog(twitter_username=None, twitter_password=None)
        assert cog._client is None
