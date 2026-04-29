"""Tests for AttachmentStore — Sprint 17 file transit."""

from __future__ import annotations

from hive.bus.attachment_store import AttachmentStore


async def test_save_returns_id_and_persists(attachment_store: AttachmentStore) -> None:
    attachment_id = await attachment_store.save(
        file_path="/tmp/uploads/abc.jpg",
        original_name="cat.jpg",
        mime_type="image/jpeg",
        size_bytes=1234,
        source="telegram",
        actor="user:42",
        forwarded_to="dev",
    )

    assert attachment_id >= 1
    fetched = await attachment_store.get(attachment_id)
    assert fetched is not None
    assert fetched.file_path == "/tmp/uploads/abc.jpg"
    assert fetched.original_name == "cat.jpg"
    assert fetched.mime_type == "image/jpeg"
    assert fetched.size_bytes == 1234
    assert fetched.source == "telegram"
    assert fetched.actor == "user:42"
    assert fetched.forwarded_to == "dev"


async def test_save_with_nullable_fields(attachment_store: AttachmentStore) -> None:
    attachment_id = await attachment_store.save(
        file_path="/tmp/uploads/no-meta.bin",
        original_name=None,
        mime_type=None,
        size_bytes=None,
        source="web",
        actor=None,
    )

    fetched = await attachment_store.get(attachment_id)
    assert fetched is not None
    assert fetched.original_name is None
    assert fetched.mime_type is None
    assert fetched.size_bytes is None
    assert fetched.actor is None
    assert fetched.forwarded_to is None


async def test_get_missing_returns_none(attachment_store: AttachmentStore) -> None:
    assert await attachment_store.get(99999) is None


async def test_list_recent_orders_newest_first_and_respects_limit(
    attachment_store: AttachmentStore,
) -> None:
    ids = []
    for i in range(5):
        ids.append(
            await attachment_store.save(
                file_path=f"/tmp/uploads/{i}.bin",
                original_name=None,
                mime_type=None,
                size_bytes=i,
                source="web",
                actor=None,
            )
        )

    recent = await attachment_store.list_recent(limit=3)
    assert len(recent) == 3
    # newest first → reverse insertion order
    assert [m.id for m in recent] == [ids[4], ids[3], ids[2]]
