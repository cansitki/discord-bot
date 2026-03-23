"""Tests for bot.database.DatabaseManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.database import DatabaseManager


class TestDatabaseConnect:
    """DatabaseManager.connect() establishes a working connection."""

    async def test_connects_to_in_memory(self, db_in_memory: DatabaseManager):
        """An in-memory connection is usable."""
        row = await db_in_memory.fetchone("SELECT 1 AS n")
        assert row is not None
        assert row["n"] == 1

    async def test_wal_mode_enabled(self, db_manager: DatabaseManager):
        """WAL journal mode is set on connect (requires file-backed DB)."""
        row = await db_manager.fetchone("PRAGMA journal_mode")
        assert row is not None
        assert row[0] == "wal"

    async def test_creates_parent_dirs(self, tmp_path: Path):
        """connect() creates parent directories for the database file."""
        deep = tmp_path / "a" / "b" / "c" / "test.db"
        db = DatabaseManager(str(deep))
        await db.connect()
        assert deep.parent.exists()
        await db.close()

    async def test_raises_when_not_connected(self):
        """Accessing conn before connect() raises RuntimeError."""
        db = DatabaseManager(":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            _ = db.conn


class TestDatabaseCRUD:
    """execute / fetchone / fetchall work for basic CRUD."""

    async def test_insert_and_fetch(self, db_in_memory: DatabaseManager):
        """Insert a row and fetch it back."""
        await db_in_memory.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"
        )
        await db_in_memory.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        row = await db_in_memory.fetchone("SELECT val FROM t WHERE id = 1")
        assert row is not None
        assert row["val"] == "hello"

    async def test_fetchall_returns_list(self, db_in_memory: DatabaseManager):
        """fetchall returns a list of rows."""
        await db_in_memory.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"
        )
        await db_in_memory.execute("INSERT INTO t (val) VALUES (?)", ("a",))
        await db_in_memory.execute("INSERT INTO t (val) VALUES (?)", ("b",))
        rows = await db_in_memory.fetchall("SELECT val FROM t ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["val"] == "a"
        assert rows[1]["val"] == "b"

    async def test_fetchone_returns_none(self, db_in_memory: DatabaseManager):
        """fetchone returns None when no rows match."""
        await db_in_memory.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"
        )
        row = await db_in_memory.fetchone("SELECT val FROM t WHERE id = 999")
        assert row is None


class TestMigrations:
    """DatabaseManager.run_migrations() applies SQL files correctly."""

    async def test_creates_tables(
        self, db_in_memory: DatabaseManager, migrations_dir: str
    ):
        """Running migrations creates _migrations, guild_config, action_log."""
        await db_in_memory.run_migrations(migrations_dir)

        tables = await db_in_memory.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in tables]
        assert "_migrations" in table_names
        assert "guild_config" in table_names
        assert "action_log" in table_names

    async def test_records_applied_migration(
        self, db_in_memory: DatabaseManager, migrations_dir: str
    ):
        """Migrations are recorded in the _migrations table."""
        await db_in_memory.run_migrations(migrations_dir)

        rows = await db_in_memory.fetchall("SELECT name FROM _migrations")
        names = [row["name"] for row in rows]
        assert "001_initial.sql" in names

    async def test_idempotent(
        self, db_in_memory: DatabaseManager, migrations_dir: str
    ):
        """Running migrations twice does not error or re-apply."""
        await db_in_memory.run_migrations(migrations_dir)
        await db_in_memory.run_migrations(migrations_dir)

        rows = await db_in_memory.fetchall("SELECT name FROM _migrations")
        # Should still have exactly the number of migration files (no duplicates)
        assert len(rows) == 3
        names = [r["name"] for r in rows]
        assert "001_initial.sql" in names
        assert "002_ai_channel.sql" in names
        assert "003_oauth_tokens.sql" in names

    async def test_missing_dir_is_noop(self, db_in_memory: DatabaseManager):
        """run_migrations with a nonexistent directory is a warning, not error."""
        await db_in_memory.run_migrations("/nonexistent/path")

    async def test_bad_sql_raises_with_filename(
        self, db_in_memory: DatabaseManager, tmp_path: Path
    ):
        """A migration with bad SQL raises RuntimeError naming the file."""
        bad_dir = tmp_path / "bad_migrations"
        bad_dir.mkdir()
        (bad_dir / "001_bad.sql").write_text("THIS IS NOT VALID SQL;")

        with pytest.raises(RuntimeError, match="001_bad.sql"):
            await db_in_memory.run_migrations(str(bad_dir))
