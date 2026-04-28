"""Tests for the Voyage embedder wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hive.knowledge.embedder import embed_multimodal, embed_texts


@pytest.fixture
def mock_voyage(monkeypatch):
    """Patch the lazily-created Voyage AsyncClient with a mock."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.embeddings = [[0.1] * 1024, [0.2] * 1024]
    mock_client.multimodal_embed = AsyncMock(return_value=mock_response)

    import hive.knowledge.embedder as emb

    monkeypatch.setattr(emb, "_client", mock_client)
    return mock_client


async def test_embed_texts_returns_one_vector_per_input(mock_voyage):
    vectors = await embed_texts(["hello", "world"])
    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)


async def test_embed_texts_wraps_each_text_as_single_segment_doc(mock_voyage):
    await embed_texts(["foo", "bar"])
    mock_voyage.multimodal_embed.assert_awaited_once()
    kwargs = mock_voyage.multimodal_embed.call_args.kwargs
    assert kwargs["model"] == "voyage-multimodal-3"
    # Voyage's multimodal endpoint takes a list of documents, each a list of
    # segments. Pure text is wrapped as a one-segment list per input.
    assert kwargs["inputs"] == [["foo"], ["bar"]]


async def test_embed_texts_empty_list_short_circuits(mock_voyage):
    vectors = await embed_texts([])
    assert vectors == []
    mock_voyage.multimodal_embed.assert_not_awaited()


async def test_embed_multimodal_passes_inputs_through(mock_voyage):
    docs = [["text segment", "more text"], ["other doc"]]
    vectors = await embed_multimodal(docs)
    assert len(vectors) == 2
    mock_voyage.multimodal_embed.assert_awaited_once()
    kwargs = mock_voyage.multimodal_embed.call_args.kwargs
    assert kwargs["inputs"] == docs
    assert kwargs["model"] == "voyage-multimodal-3"


async def test_embed_multimodal_empty_short_circuits(mock_voyage):
    vectors = await embed_multimodal([])
    assert vectors == []
    mock_voyage.multimodal_embed.assert_not_awaited()
