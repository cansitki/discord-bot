"""Tests for bot.models (GuildConfig, ActionLog, ChannelRepo)."""

from __future__ import annotations

import pytest

from bot.models import ActionLog, ChannelRepo, GuildConfig


class _FakeRow(dict):
    """Dict subclass that supports subscript access like aiosqlite.Row."""

    def __getitem__(self, key):
        return super().__getitem__(key)


class TestGuildConfig:
    """GuildConfig dataclass and from_row classmethod."""

    def test_from_row_full(self):
        """GuildConfig.from_row constructs from a row with all fields."""
        row = _FakeRow(
            guild_id=123,
            prefix="?",
            verify_channel_id=456,
            verify_role_id=789,
            log_channel_id=101,
            ai_channel_id=202,
        )
        cfg = GuildConfig.from_row(row)
        assert cfg.guild_id == 123
        assert cfg.prefix == "?"
        assert cfg.verify_channel_id == 456
        assert cfg.verify_role_id == 789
        assert cfg.log_channel_id == 101
        assert cfg.ai_channel_id == 202

    def test_from_row_nullable_fields(self):
        """GuildConfig.from_row handles None for nullable fields."""
        row = _FakeRow(
            guild_id=1,
            prefix="!",
            verify_channel_id=None,
            verify_role_id=None,
            log_channel_id=None,
            ai_channel_id=None,
        )
        cfg = GuildConfig.from_row(row)
        assert cfg.verify_channel_id is None
        assert cfg.verify_role_id is None
        assert cfg.log_channel_id is None
        assert cfg.ai_channel_id is None

    def test_default_values(self):
        """GuildConfig defaults prefix to '!' and channels to None."""
        cfg = GuildConfig(guild_id=42)
        assert cfg.prefix == "!"
        assert cfg.verify_channel_id is None
        assert cfg.verify_role_id is None
        assert cfg.log_channel_id is None


class TestActionLog:
    """ActionLog dataclass and from_row classmethod."""

    def test_from_row_full(self):
        """ActionLog.from_row constructs from a row with all fields."""
        row = _FakeRow(
            id=1,
            guild_id=100,
            user_id=200,
            action_type="kick",
            target="user#1234",
            details="Reason: spam",
            timestamp="2025-01-01T00:00:00",
        )
        log = ActionLog.from_row(row)
        assert log.id == 1
        assert log.guild_id == 100
        assert log.user_id == 200
        assert log.action_type == "kick"
        assert log.target == "user#1234"
        assert log.details == "Reason: spam"
        assert log.timestamp == "2025-01-01T00:00:00"

    def test_from_row_nullable_details(self):
        """ActionLog.from_row handles None for optional details."""
        row = _FakeRow(
            id=2,
            guild_id=100,
            user_id=200,
            action_type="ban",
            target="user#5678",
            details=None,
            timestamp="2025-01-01T00:00:00",
        )
        log = ActionLog.from_row(row)
        assert log.details is None

    def test_default_timestamp(self):
        """ActionLog gets a default ISO timestamp when not provided."""
        log = ActionLog(guild_id=1, user_id=2, action_type="test", target="t")
        assert log.timestamp is not None
        # Should be a valid ISO-format string
        assert "T" in log.timestamp

    def test_default_id_none(self):
        """ActionLog defaults id to None (assigned by database)."""
        log = ActionLog(guild_id=1, user_id=2, action_type="test", target="t")
        assert log.id is None


class TestChannelRepo:
    """ChannelRepo dataclass, from_row, and repo_full_name."""

    def test_from_row_full(self):
        """ChannelRepo.from_row constructs from a row with all fields."""
        row = _FakeRow(
            guild_id=111,
            channel_id=222,
            repo_owner="octocat",
            repo_name="hello-world",
            linked_by=333,
            linked_at="2025-06-01T12:00:00",
        )
        cr = ChannelRepo.from_row(row)
        assert cr.guild_id == 111
        assert cr.channel_id == 222
        assert cr.repo_owner == "octocat"
        assert cr.repo_name == "hello-world"
        assert cr.linked_by == 333
        assert cr.linked_at == "2025-06-01T12:00:00"

    def test_from_row_nullable_linked_at(self):
        """ChannelRepo.from_row handles None for linked_at."""
        row = _FakeRow(
            guild_id=1,
            channel_id=2,
            repo_owner="owner",
            repo_name="repo",
            linked_by=3,
            linked_at=None,
        )
        cr = ChannelRepo.from_row(row)
        assert cr.linked_at is None

    def test_repo_full_name(self):
        """ChannelRepo.repo_full_name returns 'owner/repo'."""
        cr = ChannelRepo(
            guild_id=1,
            channel_id=2,
            repo_owner="octocat",
            repo_name="hello-world",
            linked_by=3,
        )
        assert cr.repo_full_name == "octocat/hello-world"

    def test_repo_full_name_various(self):
        """repo_full_name works with different owner/repo combinations."""
        cr = ChannelRepo(
            guild_id=1,
            channel_id=2,
            repo_owner="my-org",
            repo_name="my-project.js",
            linked_by=3,
        )
        assert cr.repo_full_name == "my-org/my-project.js"

    def test_default_linked_at_none(self):
        """ChannelRepo defaults linked_at to None."""
        cr = ChannelRepo(
            guild_id=1,
            channel_id=2,
            repo_owner="a",
            repo_name="b",
            linked_by=3,
        )
        assert cr.linked_at is None


class TestChannelRepoDatabase:
    """ChannelRepo round-trip through the database after migration 003."""

    async def test_insert_and_retrieve(self, db_in_memory, migrations_dir):
        """ChannelRepo round-trips through the channel_repos table."""
        await db_in_memory.run_migrations(migrations_dir)

        await db_in_memory.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (111, 222, "octocat", "hello-world", 333),
        )

        row = await db_in_memory.fetchone(
            "SELECT * FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (111, 222),
        )
        assert row is not None
        cr = ChannelRepo.from_row(row)
        assert cr.guild_id == 111
        assert cr.channel_id == 222
        assert cr.repo_owner == "octocat"
        assert cr.repo_name == "hello-world"
        assert cr.linked_by == 333
        assert cr.linked_at is not None  # DB default sets datetime('now')
        assert cr.repo_full_name == "octocat/hello-world"

    async def test_primary_key_enforced(self, db_in_memory, migrations_dir):
        """channel_repos PK on (guild_id, channel_id) rejects duplicates."""
        import sqlite3

        await db_in_memory.run_migrations(migrations_dir)

        await db_in_memory.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, 2, "owner", "repo", 3),
        )

        with pytest.raises(sqlite3.IntegrityError):
            await db_in_memory.execute(
                "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, 2, "other-owner", "other-repo", 4),
            )

    async def test_channel_repos_schema_columns(self, db_in_memory, migrations_dir):
        """Migration 003 creates channel_repos with expected columns."""
        await db_in_memory.run_migrations(migrations_dir)

        rows = await db_in_memory.fetchall("PRAGMA table_info(channel_repos)")
        col_names = [row["name"] for row in rows]
        assert col_names == [
            "guild_id",
            "channel_id",
            "repo_owner",
            "repo_name",
            "linked_by",
            "linked_at",
        ]
