"""Integration tests for the pgvector-backed BlueprintStore."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hive.knowledge.blueprints import BlueprintStore


@pytest.fixture
def mock_embed(monkeypatch):
    """Patch embed_texts to return deterministic vectors."""
    calls: list[list[str]] = []

    async def fake(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        # Distinct vectors per input so similarity ordering is meaningful.
        return [[float(ord(t[0]) % 10)] + [0.0] * 1535 for t in texts]

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
    # Query with "a..." → should match alpha first (same leading char -> same vector).
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
