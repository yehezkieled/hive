"""Shared test fixtures for Hive."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.router import MessageRouter
from hive.bus.store import MessageStore
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.knowledge.blueprints import BlueprintStore


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary directory for test data (logs, etc.)."""
    return tmp_path


@pytest.fixture
def personalities_dir(tmp_path: Path) -> Path:
    """Temporary directory with test personality files."""
    d = tmp_path / "personalities"
    d.mkdir()

    template = d / "maestro-dev.md"
    template.write_text(
        """# Maestro: Dev

## Identity
- **Name**: Dev
- **Role**: maestro
- **Model**: sonnet

## System Prompt
You are Dev, a software engineering maestro. You lead development teams
and coordinate technical work.

## Tools
- allowedTools: Bash Read Write Edit Grep Glob
"""
    )
    return d


# -----------------------------------------------------------------------------
# PostgreSQL fixtures — one container for the whole test session, tables
# truncated between tests to keep each test isolated without paying the
# container-startup cost per test.
# -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """Session-scoped PostgreSQL container. Yields an asyncpg-compatible DSN."""
    with PostgresContainer("pgvector/pgvector:pg16") as container:
        # testcontainers returns a SQLAlchemy-style URL (postgresql+psycopg2://...);
        # strip the driver part to get a plain DSN asyncpg accepts.
        raw = container.get_connection_url()
        dsn = raw.replace("postgresql+psycopg2://", "postgresql://")
        yield dsn


@pytest_asyncio.fixture
async def store(pg_dsn: str) -> AsyncIterator[MessageStore]:
    """Function-scoped MessageStore bound to the session PG container.

    Truncates the messages and entities tables before each test so every
    test starts with a clean slate while still reusing the warm pool.
    """
    s = MessageStore(pg_dsn)
    await s.connect()
    async with s.pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE messages RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE entities RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE token_usage RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE tasks RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE audit_log RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE vault_actions RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE blueprints RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE mode_requests RESTART IDENTITY CASCADE")
    try:
        yield s
    finally:
        await s.close()


@pytest_asyncio.fixture
async def router(store: MessageStore) -> AsyncIterator[MessageRouter]:
    """Function-scoped MessageRouter around the test store."""
    yield MessageRouter(store)


@pytest_asyncio.fixture
async def entity_store(store: MessageStore) -> AsyncIterator[EntityStore]:
    """Function-scoped EntityStore sharing the test pool."""
    yield EntityStore(store.pool)


@pytest_asyncio.fixture
async def token_store(store: MessageStore) -> AsyncIterator[TokenStore]:
    """Function-scoped TokenStore sharing the test pool."""
    yield TokenStore(store.pool)


@pytest_asyncio.fixture
async def task_store(store: MessageStore) -> AsyncIterator[TaskStore]:
    """Function-scoped TaskStore sharing the test pool."""
    yield TaskStore(store.pool)


@pytest_asyncio.fixture
async def audit_log(store: MessageStore) -> AsyncIterator[AuditLog]:
    """Function-scoped AuditLog sharing the test pool."""
    yield AuditLog(store.pool)


@pytest_asyncio.fixture
async def vault_store(store: MessageStore) -> AsyncIterator[VaultStore]:
    """Function-scoped VaultStore sharing the test pool."""
    yield VaultStore(store.pool)


@pytest_asyncio.fixture
async def blueprint_store(store: MessageStore) -> AsyncIterator[BlueprintStore]:
    """Function-scoped BlueprintStore sharing the test pool."""
    yield BlueprintStore(store.pool)


@pytest_asyncio.fixture
async def mode_request_store(store: MessageStore) -> AsyncIterator[ModeRequestStore]:
    """Function-scoped ModeRequestStore sharing the test pool."""
    yield ModeRequestStore(store.pool)
