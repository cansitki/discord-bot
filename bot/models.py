"""Data models for guild configuration and action logging."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class GuildConfig:
    """Per-guild configuration stored in the ``guild_config`` table."""

    guild_id: int
    prefix: str = "!"
    verify_channel_id: int | None = None
    verify_role_id: int | None = None
    log_channel_id: int | None = None
    ai_channel_id: int | None = None

    @classmethod
    def from_row(cls, row) -> GuildConfig:
        """Construct from an ``aiosqlite.Row`` (or any dict-like/subscriptable)."""
        return cls(
            guild_id=row["guild_id"],
            prefix=row["prefix"],
            verify_channel_id=row["verify_channel_id"],
            verify_role_id=row["verify_role_id"],
            log_channel_id=row["log_channel_id"],
            ai_channel_id=row["ai_channel_id"],
        )


@dataclass
class ActionLog:
    """An auditable action recorded in the ``action_log`` table."""

    guild_id: int
    user_id: int
    action_type: str
    target: str
    id: int | None = None
    details: str | None = None
    timestamp: str | None = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_row(cls, row) -> ActionLog:
        """Construct from an ``aiosqlite.Row`` (or any dict-like/subscriptable)."""
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            action_type=row["action_type"],
            target=row["target"],
            details=row["details"],
            timestamp=row["timestamp"],
        )
