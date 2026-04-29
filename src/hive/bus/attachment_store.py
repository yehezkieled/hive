"""Persistent metadata for uploaded files.

Sprint 17 ships file transit only — Telegram and web upload paths drop
files onto the VPS filesystem under ``UPLOADS_DIR`` and record one row
per file here. ``forwarded_to`` records which entity (if any) the
caption routed the file to; NULL means the file was stored without an
agent reading it. Sprint 18 will add embedding / blueprint integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass
class AttachmentMeta:
    """One row from the attachments table."""

    id: int
    file_path: str
    original_name: str | None
    mime_type: str | None
    size_bytes: int | None
    source: str
    actor: str | None
    forwarded_to: str | None
    created_at: datetime


class AttachmentStore:
    """asyncpg-backed store for upload metadata."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def save(
        self,
        *,
        file_path: str,
        original_name: str | None,
        mime_type: str | None,
        size_bytes: int | None,
        source: str,
        actor: str | None,
        forwarded_to: str | None = None,
    ) -> int:
        """Insert one attachment row and return its id."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO attachments
                (file_path, original_name, mime_type, size_bytes, source, actor, forwarded_to)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            file_path,
            original_name,
            mime_type,
            size_bytes,
            source,
            actor,
            forwarded_to,
        )
        return row["id"]

    async def get(self, attachment_id: int) -> AttachmentMeta | None:
        """Fetch a single attachment by id, or None if missing."""
        row = await self.pool.fetchrow(
            "SELECT * FROM attachments WHERE id = $1",
            attachment_id,
        )
        return _row_to_meta(row) if row else None

    async def list_recent(self, limit: int = 20) -> list[AttachmentMeta]:
        """Return recent attachments newest first."""
        rows = await self.pool.fetch(
            "SELECT * FROM attachments ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [_row_to_meta(row) for row in rows]


def _row_to_meta(row: asyncpg.Record) -> AttachmentMeta:
    """Convert a DB row into an AttachmentMeta dataclass."""
    return AttachmentMeta(
        id=row["id"],
        file_path=row["file_path"],
        original_name=row["original_name"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        source=row["source"],
        actor=row["actor"],
        forwarded_to=row["forwarded_to"],
        created_at=row["created_at"],
    )
