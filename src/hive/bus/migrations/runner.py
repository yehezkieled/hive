"""Simple versioned SQL migration runner.

Applies NNN_*.sql files in order, tracked in a schema_migrations table.
Idempotent: safe to call on every startup.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Apply any *.sql migrations that haven't been applied yet.

    Migration files must be named ``NNN_name.sql`` where NNN is the version.
    Each migration runs inside a transaction and is recorded in
    ``schema_migrations(version)`` on success.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        applied = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }

        sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not sql_files:
            logger.debug("No migration files found in %s", MIGRATIONS_DIR)
            return

        for sql_file in sql_files:
            version = _parse_version(sql_file.name)
            if version is None:
                logger.warning("Skipping unversioned migration file: %s", sql_file.name)
                continue

            if version in applied:
                continue

            logger.info("Running migration %s", sql_file.name)
            sql = sql_file.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, filename) VALUES ($1, $2)",
                    version,
                    sql_file.name,
                )


def _parse_version(filename: str) -> int | None:
    """Extract the leading NNN version from a migration filename."""
    prefix = filename.split("_", 1)[0]
    try:
        return int(prefix)
    except ValueError:
        return None
