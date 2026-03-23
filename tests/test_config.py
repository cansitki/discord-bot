"""Tests for bot.config.Config."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from bot.config import Config


class TestConfigFromEnv:
    """Config.from_env() loads and validates environment variables."""

    def test_loads_all_vars(self, mock_env_full):
        """Config loads all env vars including optional overrides."""
        cfg = Config.from_env()
        assert cfg.discord_bot_token == "test-token-do-not-use"
        assert cfg.anthropic_api_key == "test-api-key-do-not-use"
        assert "test.db" in cfg.database_path
        assert cfg.command_prefix == "?"

    def test_uses_defaults_for_optional(self, mock_env_required_only):
        """Config uses default values when optional vars are absent."""
        cfg = Config.from_env()
        assert cfg.database_path == "./data/bot.db"
        assert cfg.command_prefix == "!"

    def test_raises_on_missing_token(self):
        """Config raises ValueError naming DISCORD_BOT_TOKEN when missing."""
        env = {"ANTHROPIC_API_KEY": "some-key"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
                Config.from_env()

    def test_warns_on_missing_api_key(self):
        """Config warns but does not raise when ANTHROPIC_API_KEY is missing."""
        env = {"DISCORD_BOT_TOKEN": "some-token"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.anthropic_api_key == ""

    def test_raises_on_empty_token(self):
        """Config treats an empty DISCORD_BOT_TOKEN as missing."""
        env = {"DISCORD_BOT_TOKEN": "", "ANTHROPIC_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
                Config.from_env()

    def test_warns_on_empty_api_key(self):
        """Config treats an empty ANTHROPIC_API_KEY as absent but doesn't raise."""
        env = {"DISCORD_BOT_TOKEN": "token", "ANTHROPIC_API_KEY": ""}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.anthropic_api_key == ""

    def test_config_is_frozen(self, mock_env_full):
        """Config dataclass is immutable (frozen=True)."""
        cfg = Config.from_env()
        with pytest.raises(AttributeError):
            cfg.discord_bot_token = "new-value"  # type: ignore[misc]

    def test_github_vars_none_by_default(self):
        """GitHub vars are None when not set in environment."""
        env = {
            "DISCORD_BOT_TOKEN": "test-token-do-not-use",
            "ANTHROPIC_API_KEY": "test-api-key-do-not-use",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("bot.config.load_dotenv"):
            cfg = Config.from_env()
            assert cfg.github_app_id is None
            assert cfg.github_private_key is None
            assert cfg.github_webhook_secret is None

    def test_github_vars_loaded_when_set(self, tmp_path):
        """GitHub vars load actual values when set."""
        env = {
            "DISCORD_BOT_TOKEN": "test-token-do-not-use",
            "ANTHROPIC_API_KEY": "test-api-key-do-not-use",
            "GITHUB_APP_ID": "12345",
            "GITHUB_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            "GITHUB_WEBHOOK_SECRET": "webhook-secret-value",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.github_app_id == "12345"
        assert cfg.github_private_key == "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        assert cfg.github_webhook_secret == "webhook-secret-value"

    def test_github_vars_empty_string_treated_as_none(self):
        """Empty string GitHub vars are treated as None (K003)."""
        env = {
            "DISCORD_BOT_TOKEN": "test-token-do-not-use",
            "ANTHROPIC_API_KEY": "test-api-key-do-not-use",
            "GITHUB_APP_ID": "",
            "GITHUB_PRIVATE_KEY": "",
            "GITHUB_WEBHOOK_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.github_app_id is None
        assert cfg.github_private_key is None
        assert cfg.github_webhook_secret is None
