"""Tests for AttachmentStore — Sprint 17 file transit + Sprint 28 chunked embeddings."""

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
# Sprint 28 — chunked embedding + search
# -----------------------------------------------------------------------------


def _vec(c: str) -> list[float]:
    """One-hot 1024d vector keyed off the first character of ``c``."""
    vec = [0.0] * 1024
    if c:
        vec[ord(c[0]) % 1024] = 1.0
    return vec


@pytest.fixture
def mock_embed(monkeypatch):
    """Same first char → identical vector (cosine distance 0); different →
    orthogonal (distance 1). Only the search path embeds the query — chunk
    embeddings are written by the test directly via save_chunks.
    """

    async def fake(texts: list[str]) -> list[list[float]]:
        return [_vec(t) for t in texts]

    monkeypatch.setattr("hive.bus.attachment_store.embed_texts", fake)


async def _save_with_chunks(
    store: AttachmentStore,
    *,
    name: str,
    chunks: list[str],
) -> int:
    """Helper: insert a row then attach one chunk per body string."""
    aid = await store.save(
        file_path=f"/tmp/uploads/{name}",
        original_name=name,
        mime_type="text/plain",
        size_bytes=sum(len(c) for c in chunks),
        source="web",
        actor=None,
    )
    await store.save_chunks(aid, [(c, _vec(c)) for c in chunks])
    return aid


async def test_save_chunks_persists_and_search_finds(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    aid = await _save_with_chunks(attachment_store, name="x.txt", chunks=["hello"])
    results = await attachment_store.search("h prefix", limit=5)
    assert any(r["id"] == aid for r in results)


async def test_save_chunks_replaces_existing(attachment_store: AttachmentStore, mock_embed) -> None:
    """Re-running save_chunks for the same attachment deletes prior chunks."""
    aid = await _save_with_chunks(attachment_store, name="x.txt", chunks=["alpha"])
    await attachment_store.save_chunks(aid, [("zeta", _vec("z"))])
    # Old chunk ('a') must be gone — searching for 'a' shouldn't match anymore.
    results = await attachment_store.search("alpha", limit=5)
    assert all(r["id"] != aid or r["chunk_text"] == "zeta" for r in results)
    # New chunk should match a 'z'-prefix query.
    results = await attachment_store.search("zoo", limit=5)
    assert any(r["id"] == aid and r["chunk_text"] == "zeta" for r in results)


async def test_search_returns_chunk_text_and_chunk_index(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    aid = await attachment_store.save(
        file_path="/tmp/uploads/notes.md",
        original_name="notes.md",
        mime_type="text/markdown",
        size_bytes=20,
        source="web",
        actor=None,
    )
    await attachment_store.save_chunks(
        aid,
        [
            ("first chunk text", _vec("f")),
            ("second chunk text", _vec("s")),
        ],
    )
    results = await attachment_store.search("forecast", limit=5)
    assert results[0]["id"] == aid
    assert results[0]["chunk_text"] == "first chunk text"
    assert results[0]["chunk_index"] == 0


async def test_search_groups_by_attachment(attachment_store: AttachmentStore, mock_embed) -> None:
    """Multiple matching chunks on one attachment → still one row out."""
    aid = await attachment_store.save(
        file_path="/tmp/uploads/big.md",
        original_name="big.md",
        mime_type="text/markdown",
        size_bytes=40,
        source="web",
        actor=None,
    )
    # Two chunks both starting with 'a' — both match an 'a' query at distance 0.
    await attachment_store.save_chunks(
        aid,
        [
            ("alpha section one", _vec("a")),
            ("apple section two", _vec("a")),
        ],
    )
    results = await attachment_store.search("alpha", limit=5)
    matching = [r for r in results if r["id"] == aid]
    assert len(matching) == 1


async def test_search_orders_by_distance(attachment_store: AttachmentStore, mock_embed) -> None:
    a = await _save_with_chunks(attachment_store, name="a.txt", chunks=["alpha"])
    b = await _save_with_chunks(attachment_store, name="b.txt", chunks=["beta"])
    c = await _save_with_chunks(attachment_store, name="c.txt", chunks=["charlie"])

    results = await attachment_store.search("alpha-ish", limit=3)
    assert results[0]["id"] == a
    # b, c are orthogonal to query → distance 1.0 each, both follow
    follow_ids = {r["id"] for r in results[1:]}
    assert follow_ids == {b, c}


async def test_search_max_distance_filters_out_orthogonal(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    await _save_with_chunks(attachment_store, name="b.txt", chunks=["beta"])

    # Query starting with 'a' — orthogonal to "beta" (dist 1.0). Filter at 0.5.
    results = await attachment_store.search("alpha", limit=5, max_distance=0.5)
    assert results == []


async def test_search_excludes_attachments_with_no_chunks(
    attachment_store: AttachmentStore, mock_embed
) -> None:
    # Row with no chunks must never appear in search.
    await attachment_store.save(
        file_path="/tmp/uploads/never.bin",
        original_name="never.bin",
        mime_type="application/octet-stream",
        size_bytes=10,
        source="web",
        actor=None,
    )
    embedded = await _save_with_chunks(attachment_store, name="seen.txt", chunks=["alpha"])

    results = await attachment_store.search("alpha-like", limit=5)
    assert [r["id"] for r in results] == [embedded]


async def test_list_unembedded_returns_only_chunkless_rows(
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
    await _save_with_chunks(attachment_store, name="done.txt", chunks=["alpha"])

    pending = await attachment_store.list_unembedded()
    assert [m.id for m in pending] == [null_id]
