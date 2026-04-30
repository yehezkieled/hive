"""Tests for the attachment embedder — Sprint 18."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from pypdf.errors import PdfReadError

from hive.knowledge import attachment_embedder
from hive.knowledge.attachment_embedder import embed_attachment

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def fake_text_embed(monkeypatch):
    """Patch embed_texts to return a deterministic 1024d vector per call."""
    calls: list[list[str]] = []

    async def fake(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr("hive.knowledge.attachment_embedder.embed_texts", fake)
    return calls


@pytest.fixture
def fake_multimodal_embed(monkeypatch):
    """Patch embed_multimodal; record the inputs so we can assert on segments."""
    calls: list[list[list]] = []

    async def fake(inputs):
        calls.append(inputs)
        return [[0.2] * 1024 for _ in inputs]

    monkeypatch.setattr("hive.knowledge.attachment_embedder.embed_multimodal", fake)
    return calls


async def test_missing_file_returns_none(tmp_path: Path) -> None:
    result = await embed_attachment(tmp_path / "nope.png", "image/png")
    assert result is None


async def test_unsupported_mime_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "x.zip"
    f.write_bytes(b"PK\x03\x04")
    result = await embed_attachment(f, "application/zip")
    assert result is None


async def test_text_file_embeds(tmp_path: Path, fake_text_embed) -> None:
    f = tmp_path / "doc.md"
    f.write_text("# hello world\n\nthis is some text")
    result = await embed_attachment(f, "text/markdown")
    assert result is not None
    vector, embed_text = result
    assert len(vector) == 1024
    assert "hello world" in embed_text
    assert fake_text_embed == [[embed_text]]


async def test_text_file_truncates_to_max_chars(
    tmp_path: Path, fake_text_embed, monkeypatch
) -> None:
    monkeypatch.setattr("hive.knowledge.attachment_embedder.ATTACHMENT_EMBED_MAX_CHARS", 50)
    f = tmp_path / "big.txt"
    f.write_text("a" * 200)
    result = await embed_attachment(f, "text/plain")
    assert result is not None
    _, embed_text = result
    assert len(embed_text) == 50


async def test_text_file_with_non_utf8_falls_back(tmp_path: Path, fake_text_embed) -> None:
    f = tmp_path / "latin.csv"
    # 0xff is invalid utf-8; the fallback decoder replaces with U+FFFD.
    f.write_bytes(b"col1,col2\nvalue,\xff\xff")
    result = await embed_attachment(f, "text/csv")
    assert result is not None
    _, embed_text = result
    assert "col1,col2" in embed_text


async def test_empty_text_returns_none(tmp_path: Path, fake_text_embed) -> None:
    f = tmp_path / "blank.txt"
    f.write_text("   \n  \n")
    result = await embed_attachment(f, "text/plain")
    assert result is None


async def test_image_embeds_via_multimodal(fake_multimodal_embed) -> None:
    result = await embed_attachment(FIXTURES / "sample.png", "image/png")
    assert result is not None
    vector, embed_text = result
    assert len(vector) == 1024
    assert embed_text == "sample.png"
    # one document with one segment (the image)
    assert len(fake_multimodal_embed) == 1
    assert len(fake_multimodal_embed[0]) == 1
    assert len(fake_multimodal_embed[0][0]) == 1
    assert isinstance(fake_multimodal_embed[0][0][0], Image.Image)


async def test_image_oversized_thumbnails_to_max(tmp_path: Path, fake_multimodal_embed) -> None:
    big = Image.new("RGB", (4000, 3000), "blue")
    big_path = tmp_path / "big.jpg"
    big.save(big_path)

    result = await embed_attachment(big_path, "image/jpeg")
    assert result is not None
    sent_image = fake_multimodal_embed[0][0][0]
    assert max(sent_image.size) <= 1024


async def test_corrupt_image_returns_none(tmp_path: Path, fake_multimodal_embed) -> None:
    f = tmp_path / "broken.png"
    f.write_bytes(b"not a real image")
    result = await embed_attachment(f, "image/png")
    assert result is None
    assert fake_multimodal_embed == []


async def test_pdf_embeds_extracted_text(tmp_path: Path, fake_text_embed, monkeypatch) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n%fake\n")  # body unused; PdfReader is mocked

    fake_reader = MagicMock()
    fake_reader.is_encrypted = False
    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Sprint 18 file embedding"
    fake_reader.pages = [fake_page]
    monkeypatch.setattr(
        attachment_embedder,
        "PdfReader",
        lambda path: fake_reader,
    )

    result = await embed_attachment(f, "application/pdf")
    assert result is not None
    _, embed_text = result
    assert "Sprint 18" in embed_text


async def test_pdf_encrypted_returns_none(tmp_path: Path, fake_text_embed, monkeypatch) -> None:
    f = tmp_path / "locked.pdf"
    f.write_bytes(b"%PDF-1.4\n")

    fake_reader = MagicMock()
    fake_reader.is_encrypted = True
    monkeypatch.setattr(
        attachment_embedder,
        "PdfReader",
        lambda path: fake_reader,
    )

    result = await embed_attachment(f, "application/pdf")
    assert result is None
    assert fake_text_embed == []


async def test_pdf_empty_extraction_returns_none(
    tmp_path: Path, fake_text_embed, monkeypatch
) -> None:
    """Image-only PDFs return empty text from extract_text — must not embed."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4\n")

    fake_reader = MagicMock()
    fake_reader.is_encrypted = False
    fake_page = MagicMock()
    fake_page.extract_text.return_value = ""
    fake_reader.pages = [fake_page, fake_page]
    monkeypatch.setattr(
        attachment_embedder,
        "PdfReader",
        lambda path: fake_reader,
    )

    result = await embed_attachment(f, "application/pdf")
    assert result is None


async def test_pdf_read_error_returns_none(tmp_path: Path, fake_text_embed, monkeypatch) -> None:
    f = tmp_path / "broken.pdf"
    f.write_bytes(b"not a pdf at all")

    def boom(path):
        raise PdfReadError("malformed")

    monkeypatch.setattr(attachment_embedder, "PdfReader", boom)

    result = await embed_attachment(f, "application/pdf")
    assert result is None


async def test_voyage_failure_propagates_as_none(tmp_path: Path, monkeypatch) -> None:
    """Embedder failures must not raise — the upload row stays valid."""

    async def boom(texts):
        raise RuntimeError("voyage down")

    monkeypatch.setattr("hive.knowledge.attachment_embedder.embed_texts", boom)

    f = tmp_path / "doc.txt"
    f.write_text("hello")
    result = await embed_attachment(f, "text/plain")
    assert result is None
