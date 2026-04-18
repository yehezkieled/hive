"""OpenAI embedding wrapper for Hive's semantic blueprint store.

Uses `text-embedding-3-small` by default (1536-dim). Call sites pass text,
receive vectors. Batching is done by the caller — OpenAI accepts up to
2,048 inputs per call and ~8K tokens per input.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from hive.config import EMBEDDING_MODEL, OPENAI_API_KEY

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazily create one async OpenAI client per process."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts. Returns one vector per input, in order.

    An empty input returns an empty list without calling the API.
    """
    if not texts:
        return []
    response = await _get_client().embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]
