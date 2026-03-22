"""Tests for bot.models (GuildConfig, ActionLog)."""

from __future__ import annotations

from bot.models import ActionLog, GuildConfig


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
