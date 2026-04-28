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
