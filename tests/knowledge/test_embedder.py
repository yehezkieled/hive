"""Tests for the OpenAI embedder wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hive.knowledge.embedder import embed_texts


@pytest.fixture
def mock_openai(monkeypatch):
    """Patch the lazily-created AsyncOpenAI client with a mock."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=[0.1] * 1536),
        MagicMock(embedding=[0.2] * 1536),
    ]
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    import hive.knowledge.embedder as emb
    monkeypatch.setattr(emb, "_client", mock_client)
    return mock_client


async def test_embed_texts_returns_one_vector_per_input(mock_openai):
    vectors = await embed_texts(["hello", "world"])
    assert len(vectors) == 2
    assert all(len(v) == 1536 for v in vectors)


async def test_embed_texts_passes_model_and_input(mock_openai):
    await embed_texts(["foo"])
    mock_openai.embeddings.create.assert_awaited_once()
    kwargs = mock_openai.embeddings.create.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["input"] == ["foo"]


async def test_embed_texts_empty_list_short_circuits(mock_openai):
    vectors = await embed_texts([])
    assert vectors == []
    mock_openai.embeddings.create.assert_not_awaited()
