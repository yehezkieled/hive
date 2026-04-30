"""Tests for AttachmentStore — Sprint 17 file transit + Sprint 18 embeddings."""

from __future__ import annotations

import pytest

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


# -----------------------------------------------------------------------------
# Sprint 18 — embedding + search
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_embed(monkeypatch):
    """One-hot 1024d vectors keyed off the first character of each text.

    Same first char → identical vector (cosine distance 0). Different first
    char → orthogonal (cosine distance 1).
    """

    async def fake(texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for t in texts:
            vec = [0.0] * 1024
            if t:
                vec[ord(t[0]) % 1024] = 1.0
            results.append(vec)
        return results

    monkeypatch.setattr("hive.bus.attachment_store.embed_texts", fake)


async def _save_with_embedding(store: AttachmentStore, *, name: str, body: str) -> int:
    """Helper: insert a row then attach a deterministic embedding via update."""
    aid = await store.save(
        file_path=f"/tmp/uploads/{name}",
        original_name=name,
        mime_type="text/plain",
        size_bytes=len(body),
        source="web",
        actor=None,
    )
    vec = [0.0] * 1024
    vec[ord(body[0]) % 1024] = 1.0
    await store.update_embedding(aid, vec, body)
    return aid


async def test_update_embedding_persists_columns(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    aid = await attachment_store.save(
        file_path="/tmp/uploads/x.txt",
        original_name="x.txt",
        mime_type="text/plain",
        size_bytes=5,
        source="web",
        actor=None,
    )
    vec = [0.0] * 1024
    vec[1] = 1.0
    await attachment_store.update_embedding(aid, vec, "hello")

    # Confirm via search that the row is now retrievable.
    results = await attachment_store.search("h prefix", limit=5)
    assert any(r["id"] == aid for r in results)


async def test_search_orders_by_distance(attachment_store: AttachmentStore, mock_embed) -> None:
    a = await _save_with_embedding(attachment_store, name="a.txt", body="alpha")
    b = await _save_with_embedding(attachment_store, name="b.txt", body="beta")
    c = await _save_with_embedding(attachment_store, name="c.txt", body="charlie")

    results = await attachment_store.search("alpha-ish", limit=3)
    assert results[0]["id"] == a
    # b, c are orthogonal to query → distance 1.0 each, both follow
    follow_ids = {r["id"] for r in results[1:]}
    assert follow_ids == {b, c}


async def test_search_max_distance_filters_out_orthogonal(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    await _save_with_embedding(attachment_store, name="b.txt", body="beta")

    # Query starting with 'a' — orthogonal to "beta" (dist 1.0). Filter at 0.5.
    results = await attachment_store.search("alpha", limit=5, max_distance=0.5)
    assert results == []


async def test_search_excludes_null_embedding_rows(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    # Row with no embedding must never appear in search.
    await attachment_store.save(
        file_path="/tmp/uploads/never.bin",
        original_name="never.bin",
        mime_type="application/octet-stream",
        size_bytes=10,
        source="web",
        actor=None,
    )
    embedded = await _save_with_embedding(attachment_store, name="seen.txt", body="alpha")

    results = await attachment_store.search("alpha-like", limit=5)
    assert [r["id"] for r in results] == [embedded]


async def test_list_unembedded_returns_only_null_rows(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    null_id = await attachment_store.save(
        file_path="/tmp/uploads/raw.bin",
        original_name="raw.bin",
        mime_type=None,
        size_bytes=1,
        source="web",
        actor=None,
    )
    await _save_with_embedding(attachment_store, name="done.txt", body="alpha")

    pending = await attachment_store.list_unembedded()
    assert [m.id for m in pending] == [null_id]
