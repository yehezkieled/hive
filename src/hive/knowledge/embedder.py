"""Voyage embedding wrapper for Hive's semantic blueprint store.

Uses ``voyage-multimodal-3`` by default (1024-dim, joint text+image space).
Call sites pass text, receive vectors. The same model handles future image
inputs via ``embed_multimodal``.

Voyage's multimodal endpoint takes a list of "documents," each a list of
segments (strings or PIL images). For pure text we wrap each string as a
one-segment document; ``embed_multimodal`` exposes the raw shape for
mixed text+image use later.
"""

from __future__ import annotations

from typing import Any

import voyageai

from hive.config import EMBEDDING_MODEL, VOYAGE_API_KEY

_client: voyageai.AsyncClient | None = None


def _get_client() -> voyageai.AsyncClient:
    """Lazily create one async Voyage client per process."""
    global _client
    if _client is None:
        _client = voyageai.AsyncClient(api_key=VOYAGE_API_KEY)
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts. Returns one vector per input, in order.

    Empty input returns an empty list without calling the API.
    """
    if not texts:
        return []
    response = await _get_client().multimodal_embed(
        inputs=[[t] for t in texts],
        model=EMBEDDING_MODEL,
    )
    return response.embeddings


async def embed_multimodal(inputs: list[list[Any]]) -> list[list[float]]:
    """Embed mixed text+image documents.

    Each input is a list of segments (strings or PIL.Image.Image). One
    vector is returned per document. Stub for future image-aware features —
    bridge/web don't currently route images here.
    """
    if not inputs:
        return []
    response = await _get_client().multimodal_embed(
        inputs=inputs,
        model=EMBEDDING_MODEL,
    )
    return response.embeddings
