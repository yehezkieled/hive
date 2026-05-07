"""Embed uploaded files for semantic search.

Routes by mime type:

* ``image/*`` → load with PIL, thumbnail to 1024×1024 (Voyage caps around
  16MP / 10MB), pass to ``embed_multimodal``. Returns a single chunk
  ``(filename, image_vector)`` so the schema stays uniform with text
  attachments — the chunk text is the filename so retrieval still has a
  human-readable snippet to render.
* ``application/pdf`` → extract text with pypdf, run through the
  blueprint chunker, batch-embed every chunk in one Voyage call.
* ``text/*`` → read bytes, decode utf-8 with replacement fallback,
  chunk + batch-embed.
* anything else → return ``None``; the upload row keeps no chunks.

Sprint 28: replaced single-vector return with a list of
``(chunk_text, vector)`` tuples so long PDFs/text uploads aren't
truncated at 8000 chars and retrieval ranks against the matching
section instead of one whole-document vector.

Failures (encrypted PDFs, broken images, empty extraction, Voyage errors)
return ``None`` rather than raising so the upload path can persist the
file regardless. The backfill script picks up chunkless rows on a later
run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from hive.config import (
    ATTACHMENT_CHUNK_OVERLAP_TOKENS,
    ATTACHMENT_CHUNK_TOKENS,
    ATTACHMENT_EMBED_MAX_CHARS,
)
from hive.knowledge.chunking import split_blueprint
from hive.knowledge.embedder import embed_multimodal, embed_texts

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = (1024, 1024)


async def embed_attachment(
    file_path: str | Path,
    mime_type: str | None,
) -> list[tuple[str, list[float]]] | None:
    """Embed a stored upload. Return a list of ``(chunk_text, vector)`` or None.

    Images return a single tuple ``(filename, vector)`` — the chunk text
    is the filename so the auto-retrieve snippet still has something to
    show. Text/PDF inputs fan out to N chunks of ~``ATTACHMENT_CHUNK_TOKENS``
    each. Empty/encrypted/broken inputs return ``None``.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("attachment_embed: file missing: %s", path)
        return None

    mime = (mime_type or "").lower()

    try:
        if mime.startswith("image/"):
            return await _embed_image(path)
        if mime == "application/pdf":
            return await _embed_pdf(path)
        if mime.startswith("text/"):
            return await _embed_text_file(path)
    except Exception:
        logger.exception("attachment_embed: failed for %s (%s)", path, mime)
        return None

    logger.debug("attachment_embed: skip non-embeddable mime %r for %s", mime, path)
    return None


async def _embed_image(path: Path) -> list[tuple[str, list[float]]] | None:
    """Embed an image file. Voyage gets a thumbnailed PIL Image."""
    try:
        with Image.open(path) as img:
            img.load()
            image = img.copy()
    except (UnidentifiedImageError, OSError):
        logger.exception("attachment_embed: cannot open image %s", path)
        return None

    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    image.thumbnail(_THUMBNAIL_SIZE)

    vectors = await embed_multimodal([[image]])
    if not vectors:
        return None
    return [(path.name, vectors[0])]


async def _embed_pdf(path: Path) -> list[tuple[str, list[float]]] | None:
    """Extract text from a PDF and embed it as chunks."""
    try:
        reader = PdfReader(str(path))
    except (PdfReadError, OSError):
        logger.exception("attachment_embed: cannot read pdf %s", path)
        return None
    if reader.is_encrypted:
        logger.info("attachment_embed: encrypted pdf, skip: %s", path)
        return None

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            logger.exception("attachment_embed: page extract failed in %s", path)

    text = "\n".join(parts).strip()
    if not text:
        logger.info("attachment_embed: empty pdf extraction (image-only?), skip: %s", path)
        return None
    return await _embed_text_payload(text)


async def _embed_text_file(path: Path) -> list[tuple[str, list[float]]] | None:
    """Read a text file and embed it as chunks."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    text = text.strip()
    if not text:
        logger.info("attachment_embed: empty text file, skip: %s", path)
        return None
    return await _embed_text_payload(text)


async def _embed_text_payload(text: str) -> list[tuple[str, list[float]]] | None:
    """Chunk + batch-embed. ``ATTACHMENT_EMBED_MAX_CHARS`` is a soft cap on
    total characters fed to the chunker so a monstrous PDF doesn't OOM
    the splitter — chunks themselves are sized by ``ATTACHMENT_CHUNK_TOKENS``.
    """
    truncated = text[:ATTACHMENT_EMBED_MAX_CHARS]
    chunks = split_blueprint(
        truncated,
        target_tokens=ATTACHMENT_CHUNK_TOKENS,
        overlap_tokens=ATTACHMENT_CHUNK_OVERLAP_TOKENS,
    )
    if not chunks:
        return None
    vectors = await embed_texts(chunks)
    if not vectors or len(vectors) != len(chunks):
        return None
    return list(zip(chunks, vectors))
