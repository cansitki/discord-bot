"""Async SQLite database manager with migration support."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)


class DatabaseManager:
    """Wraps an aiosqlite connection with WAL mode and a file-based migration runner.

    Usage::

        db = DatabaseManager("./data/bot.db")
        await db.connect()
        await db.run_migrations("migrations")
        # ... use db.execute / db.fetchone / db.fetchall ...
        await db.close()
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        """Return the active connection, raising if not connected."""
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    async def connect(self) -> None:
        """Open the aiosqlite connection, enable WAL mode, set row factory.

        Creates parent directories for the database file if they don't exist
        (skipped for in-memory databases).
        """
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        log.info("Database connected: %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection cleanly."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("Database connection closed")

    async def execute(
        self, sql: str, params: tuple = ()  # noqa: UP006
    ) -> aiosqlite.Cursor:
        """Run a write query, return the cursor."""
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor

    async def fetchone(
        self, sql: str, params: tuple = ()  # noqa: UP006
    ) -> aiosqlite.Row | None:
        """Run a read query, return a single row or ``None``."""
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(
        self, sql: str, params: tuple = ()  # noqa: UP006
    ) -> list[aiosqlite.Row]:
        """Run a read query, return all rows."""
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchall()

    # ── Migration runner ──────────────────────────────────────────────

    async def run_migrations(self, migrations_dir: str) -> None:
        """Apply pending SQL migrations from *migrations_dir*.

        Migrations are ``.sql`` files sorted by filename.  The ``_migrations``
        table tracks which have already been applied.  Each new migration runs
        inside a transaction.  If one fails, the exception includes the
        filename so startup halts with a clear message.
        """
        migrations_path = Path(migrations_dir)
        if not migrations_path.exists():
            log.warning("Migrations directory not found: %s", migrations_dir)
            return

        # Ensure _migrations tracking table exists
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await self.conn.commit()

        # Gather already-applied names
        cursor = await self.conn.execute("SELECT name FROM _migrations")
        applied = {row["name"] for row in await cursor.fetchall()}

        # Apply pending migrations in filename order
        sql_files = sorted(migrations_path.glob("*.sql"))
        for sql_file in sql_files:
            if sql_file.name in applied:
                log.debug("Migration already applied: %s", sql_file.name)
                continue

            sql = sql_file.read_text()
            try:
                await self.conn.executescript(sql)
                await self.conn.execute(
                    "INSERT INTO _migrations (name) VALUES (?)",
                    (sql_file.name,),
                )
                await self.conn.commit()
                print(f"Applying migration: {sql_file.name}")
                log.info("Applied migration: %s", sql_file.name)
            except Exception as exc:
                raise RuntimeError(
                    f"Migration failed: {sql_file.name}"
                ) from exc
