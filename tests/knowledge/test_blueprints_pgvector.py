"""Integration tests for the pgvector-backed BlueprintStore."""

from __future__ import annotations

import pytest


@pytest.fixture
def mock_embed(monkeypatch):
    """Patch embed_texts to return deterministic one-hot vectors per leading char.

    Same first character → same vector, so cosine distance = 0. Different
    first character → orthogonal vectors, so cosine distance = 1. This gives
    us predictable distances for both ordering and threshold tests.
    """
    calls: list[list[str]] = []

    async def fake(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        results: list[list[float]] = []
        for t in texts:
            vec = [0.0] * 1024
            if t:
                vec[ord(t[0]) % 1024] = 1.0
            results.append(vec)
        return results

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake)
    return calls


async def test_save_inserts_row_with_embedding(blueprint_store, mock_embed):
    bp_id = await blueprint_store.save("auth fix", "Fixed OAuth redirect", ["auth"])
    assert bp_id > 0
    rows = await blueprint_store.list_all()
    assert len(rows) == 1
    assert rows[0]["title"] == "auth fix"
    assert rows[0]["tags"] == ["auth"]


async def test_search_returns_semantically_ordered_results(blueprint_store, mock_embed):
    await blueprint_store.save("alpha", "a content", [])
    await blueprint_store.save("bravo", "b content", [])
    await blueprint_store.save("charlie", "c content", [])
    # Query with leading 'a' → matches alpha exactly (distance 0); others orthogonal.
    results = await blueprint_store.search("another query", limit=2)
    assert len(results) == 2
    assert results[0]["title"] == "alpha"


async def test_search_empty_store_returns_empty(blueprint_store, mock_embed):
    results = await blueprint_store.search("anything", limit=5)
    assert results == []


async def test_list_all_returns_newest_first(blueprint_store, mock_embed):
    await blueprint_store.save("first", "1", [])
    await blueprint_store.save("second", "2", [])
    rows = await blueprint_store.list_all()
    assert [r["title"] for r in rows] == ["second", "first"]


async def test_search_max_distance_filters_far_results(blueprint_store, mock_embed):
    """With max_distance set, orthogonal blueprints (distance ~1) are dropped."""
    await blueprint_store.save("alpha", "a content", [])
    await blueprint_store.save("zulu", "z content", [])

    # Without filter: both come back (alpha first because distance 0).
    no_filter = await blueprint_store.search("alpha-ish", limit=10)
    assert len(no_filter) == 2

    # With tight threshold: only alpha (distance 0). Zulu's cosine distance
    # is 1.0 (orthogonal), well above 0.5.
    filtered = await blueprint_store.search("alpha-ish", limit=10, max_distance=0.5)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "alpha"


# -----------------------------------------------------------------------------
# Sprint 26 — chunked embeddings: one row per chunk, parent surfaces by best chunk.
# -----------------------------------------------------------------------------


async def test_search_returns_chunk_text_field(blueprint_store, mock_embed):
    """Search rows expose ``chunk_text`` so auto-retrieve can render the matched
    section instead of the full body."""
    await blueprint_store.save("alpha", "a content", [])
    results = await blueprint_store.search("anything starting with a", limit=1)
    assert len(results) == 1
    assert "chunk_text" in results[0]
    assert results[0]["chunk_text"]
    # body is still present for legacy callers.
    assert "body" in results[0]


async def test_save_long_body_creates_multiple_chunks(blueprint_store, mock_embed):
    """A body above the short-body fast-path threshold fans out to N chunks."""
    section = "lorem ipsum dolor sit amet. " * 200  # ~5400 chars
    long_body = (
        f"## Section A\n\n{section}\n\n## Section B\n\n{section}\n\n## Section C\n\n{section}\n"
    )
    await blueprint_store.save("long doc", long_body, [])
    async with blueprint_store.pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM blueprint_chunks WHERE blueprint_id = "
            "(SELECT id FROM blueprints WHERE title = 'long doc')"
        )
    # Multiple ## sections → at least 3 chunks. Exact count depends on
    # paragraph packing — assert lower bound to avoid flakiness on minor
    # splitter tuning.
    assert count >= 3


async def test_search_groups_by_blueprint(blueprint_store, mock_embed):
    """Each parent blueprint surfaces at most once even if many of its chunks match."""
    # A long blueprint will produce many chunks; mock_embed gives them all
    # the same vector (same first char of each chunk → same one-hot index
    # — actually different first chars per chunk!). To force the grouping
    # case, save a short blueprint (one chunk) twice with the same leading
    # char so we know exactly how many parent rows we expect.
    section = "alpha section. " * 200
    long_body = f"## A\n\n{section}\n\n## B\n\n{section}\n\n## C\n\n{section}\n"
    await blueprint_store.save("doc1", long_body, [])
    await blueprint_store.save("doc2", "another short body", [])

    results = await blueprint_store.search("aardvark", limit=10)
    titles = [r["title"] for r in results]
    # Each blueprint must appear at most once.
    assert len(titles) == len(set(titles))
