"""Environment configuration loader with validation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Bot configuration loaded from environment variables.

    Required vars: DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY.
    Optional vars: DATABASE_PATH (default: ./data/bot.db), COMMAND_PREFIX (default: !).
    """

    discord_bot_token: str
    anthropic_api_key: str
    database_path: str
    command_prefix: str
    claude_model: str

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables.

        Calls ``dotenv.load_dotenv()`` first so a ``.env`` file is picked up in
        development.  Raises ``ValueError`` naming the missing variable when a
        required env var is absent.  Never logs or prints secret values.
        """
        load_dotenv()

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            raise ValueError(
                "Missing required environment variable: DISCORD_BOT_TOKEN"
            )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "Missing required environment variable: ANTHROPIC_API_KEY"
            )

        database_path = os.getenv("DATABASE_PATH", "./data/bot.db")
        command_prefix = os.getenv("COMMAND_PREFIX", "!")
        claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

        # Log loaded variable names (never values) for startup diagnostics
        loaded_vars = ["DISCORD_BOT_TOKEN", "ANTHROPIC_API_KEY"]
        if os.getenv("DATABASE_PATH"):
            loaded_vars.append("DATABASE_PATH")
        if os.getenv("COMMAND_PREFIX"):
            loaded_vars.append("COMMAND_PREFIX")
        if os.getenv("CLAUDE_MODEL"):
            loaded_vars.append("CLAUDE_MODEL")
        print(f"Config loaded: {', '.join(loaded_vars)}")

        return cls(
            discord_bot_token=token,
            anthropic_api_key=api_key,
            database_path=database_path,
            command_prefix=command_prefix,
            claude_model=claude_model,
        )
