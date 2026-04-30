"""Embed uploaded files for semantic search.

Routes by mime type:

* ``image/*`` → load with PIL, thumbnail to 1024×1024 (Voyage caps around
  16MP / 10MB), pass to ``embed_multimodal``.
* ``application/pdf`` → extract text with pypdf, truncate, ``embed_texts``.
* ``text/*`` → read bytes, decode utf-8 with replacement fallback,
  truncate, ``embed_texts``.
* anything else → return ``None``; the upload row keeps NULL embedding.

Failures (encrypted PDFs, broken images, empty extraction, Voyage errors)
return ``None`` rather than raising so the upload path can persist the
file regardless. The backfill script picks up NULL rows on a later run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from hive.config import ATTACHMENT_EMBED_MAX_CHARS
from hive.knowledge.embedder import embed_multimodal, embed_texts

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = (1024, 1024)


async def embed_attachment(
    file_path: str | Path,
    mime_type: str | None,
) -> tuple[list[float], str] | None:
    """Embed a stored upload. Return (vector, embed_text) or None.

    ``embed_text`` is what was sent to the embedder — the literal text for
    text/PDF inputs, or the original filename for images (so the snippet
    in auto-retrieve has something to show).
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


async def _embed_image(path: Path) -> tuple[list[float], str] | None:
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
    return vectors[0], path.name


async def _embed_pdf(path: Path) -> tuple[list[float], str] | None:
    """Extract text from a PDF and embed it."""
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


async def _embed_text_file(path: Path) -> tuple[list[float], str] | None:
    """Read a text file and embed it. Falls back to replace on bad bytes."""
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


async def _embed_text_payload(text: str) -> tuple[list[float], str] | None:
    """Truncate then embed."""
    truncated = text[:ATTACHMENT_EMBED_MAX_CHARS]
    vectors = await embed_texts([truncated])
    if not vectors:
        return None
    return vectors[0], truncated
