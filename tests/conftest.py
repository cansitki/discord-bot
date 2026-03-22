"""Shared pytest fixtures for bot tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.database import DatabaseManager


@pytest.fixture()
def mock_env_full(tmp_path: Path):
    """Patch environment with all required and optional config vars."""
    env = {
        "DISCORD_BOT_TOKEN": "test-token-do-not-use",
        "ANTHROPIC_API_KEY": "test-api-key-do-not-use",
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "COMMAND_PREFIX": "?",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture()
def mock_env_required_only(tmp_path: Path):
    """Patch environment with only the required vars (defaults for optional)."""
    env = {
        "DISCORD_BOT_TOKEN": "test-token-do-not-use",
        "ANTHROPIC_API_KEY": "test-api-key-do-not-use",
    }
    with patch.dict(os.environ, env, clear=False):
        # Remove optional vars so os.getenv falls through to defaults
        for key in ("DATABASE_PATH", "COMMAND_PREFIX"):
            os.environ.pop(key, None)
        yield env


@pytest.fixture()
async def db_manager(tmp_path: Path):
    """Provide a connected DatabaseManager using a temporary database file."""
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture()
async def db_in_memory():
    """Provide a connected in-memory DatabaseManager."""
    db = DatabaseManager(":memory:")
    await db.connect()
    yield db
    await db.close()


@pytest.fixture()
def migrations_dir(tmp_path: Path):
    """Copy the real migrations into a temp directory for isolated testing."""
    src = Path(__file__).resolve().parent.parent / "migrations"
    dest = tmp_path / "migrations"
    dest.mkdir()
    for f in sorted(src.glob("*.sql")):
        (dest / f.name).write_text(f.read_text())
    return str(dest)
