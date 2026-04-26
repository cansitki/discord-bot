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


@dataclass
class ChannelRepo:
    """A channel-to-repository binding stored in the ``channel_repos`` table."""

    guild_id: int
    channel_id: int
    repo_owner: str
    repo_name: str
    linked_by: int
    linked_at: str | None = None

    @property
    def repo_full_name(self) -> str:
        """Return the full repository name as 'owner/repo'."""
        return f"{self.repo_owner}/{self.repo_name}"

    @classmethod
    def from_row(cls, row) -> ChannelRepo:
        """Construct from an ``aiosqlite.Row`` (or any dict-like/subscriptable)."""
        return cls(
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            repo_owner=row["repo_owner"],
            repo_name=row["repo_name"],
            linked_by=row["linked_by"],
            linked_at=row["linked_at"],
        )


@dataclass
class TwitterFeed:
    """A watched Twitter/X profile bound to a Discord channel."""

    guild_id: int
    channel_id: int
    twitter_handle: str
    created_by: int
    id: int | None = None
    last_tweet_id: str | None = None
    created_at: str | None = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_row(cls, row) -> TwitterFeed:
        """Construct from an ``aiosqlite.Row`` (or any dict-like/subscriptable)."""
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            twitter_handle=row["twitter_handle"],
            last_tweet_id=row["last_tweet_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
