"""Environment configuration loader with validation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Bot configuration loaded from environment variables.

    Required vars: DISCORD_BOT_TOKEN.
    Conditionally required: ANTHROPIC_API_KEY (not required if OAuth is used).
    Optional vars: DATABASE_PATH (default: ./data/bot.db), COMMAND_PREFIX (default: !).
    Optional GitHub vars: GITHUB_APP_ID, GITHUB_PRIVATE_KEY, GITHUB_WEBHOOK_SECRET.
    """

    discord_bot_token: str
    anthropic_api_key: str  # May be empty string when OAuth is used
    database_path: str
    command_prefix: str
    claude_model: str
    github_app_id: str | None = None
    github_private_key: str | None = None
    github_webhook_secret: str | None = None
    twitter_username: str | None = None
    twitter_email: str | None = None
    twitter_password: str | None = None
    twitter_cookies_path: str = "./data/twitter_cookies.json"

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables.

        Calls ``dotenv.load_dotenv()`` first so a ``.env`` file is picked up in
        development.  Raises ``ValueError`` naming the missing variable when a
        required env var is absent.  Never logs or prints secret values.

        ``ANTHROPIC_API_KEY`` is no longer strictly required — when absent, the
        bot can still authenticate via OAuth (``/claude-login``).  A warning is
        printed but startup continues.
        """
        load_dotenv()

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            raise ValueError(
                "Missing required environment variable: DISCORD_BOT_TOKEN"
            )

        api_key = os.getenv("ANTHROPIC_API_KEY", "")

        database_path = os.getenv("DATABASE_PATH", "./data/bot.db")
        command_prefix = os.getenv("COMMAND_PREFIX", "!")
        claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

        # Optional GitHub App credentials — None when missing or empty
        github_app_id = os.getenv("GITHUB_APP_ID") or None
        github_private_key = os.getenv("GITHUB_PRIVATE_KEY") or None
        github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET") or None

        # Optional Twitter/X feed watcher credentials
        twitter_username = os.getenv("TWITTER_USERNAME") or None
        twitter_email = os.getenv("TWITTER_EMAIL") or None
        twitter_password = os.getenv("TWITTER_PASSWORD") or None
        twitter_cookies_path = os.getenv(
            "TWITTER_COOKIES_PATH", "./data/twitter_cookies.json"
        )

        # Log loaded variable names (never values) for startup diagnostics
        loaded_vars = ["DISCORD_BOT_TOKEN"]
        if api_key:
            loaded_vars.append("ANTHROPIC_API_KEY")
        else:
            print(
                "Warning: ANTHROPIC_API_KEY not set. "
                "Use /claude-login to authenticate with your Claude account, "
                "or set ANTHROPIC_API_KEY for API key auth."
            )
        if os.getenv("DATABASE_PATH"):
            loaded_vars.append("DATABASE_PATH")
        if os.getenv("COMMAND_PREFIX"):
            loaded_vars.append("COMMAND_PREFIX")
        if os.getenv("CLAUDE_MODEL"):
            loaded_vars.append("CLAUDE_MODEL")
        if github_app_id:
            loaded_vars.append("GITHUB_APP_ID")
        if github_private_key:
            loaded_vars.append("GITHUB_PRIVATE_KEY")
        if github_webhook_secret:
            loaded_vars.append("GITHUB_WEBHOOK_SECRET")
        if twitter_username:
            loaded_vars.append("TWITTER_USERNAME")
        print(f"Config loaded: {', '.join(loaded_vars)}")

        return cls(
            discord_bot_token=token,
            anthropic_api_key=api_key,
            database_path=database_path,
            command_prefix=command_prefix,
            claude_model=claude_model,
            github_app_id=github_app_id,
            github_private_key=github_private_key,
            github_webhook_secret=github_webhook_secret,
            twitter_username=twitter_username,
            twitter_email=twitter_email,
            twitter_password=twitter_password,
            twitter_cookies_path=twitter_cookies_path,
        )
