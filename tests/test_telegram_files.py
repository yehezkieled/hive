"""Tests for Sprint 17 PHOTO + DOCUMENT handlers in TelegramBridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hive.telegram.bridge import TelegramBridge


def _make_bridge(
    *,
    uploads_dir: Path,
    allowed_user_ids: list[int] | None = None,
    attachment_store: Any | None = None,
    audit_log: Any | None = None,
) -> TelegramBridge:
    """Build a minimal TelegramBridge stub for unit tests."""
    bridge = TelegramBridge.__new__(TelegramBridge)
    bridge.allowed_user_ids = allowed_user_ids or [42]
    bridge.default_maestro = "dev"
    bridge.attachment_store = attachment_store or AsyncMock(save=AsyncMock(return_value=99))
    bridge.audit_log = audit_log
    bridge._execute_command = AsyncMock(return_value="OK from dev")
    return bridge


def _make_photo_update(
    *,
    user_id: int = 42,
    file_id: str = "tg_file_id",
    file_size: int | None = 1234,
    caption: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build (update, context) mocks for an incoming Telegram photo."""
    photo = MagicMock()
    photo.file_id = file_id
    photo.file_size = file_size

    message = MagicMock()
    message.photo = [photo]
    message.document = None
    message.caption = caption
    message.reply_text = AsyncMock()

    user = MagicMock()
    user.id = user_id

    update = MagicMock()
    update.message = message
    update.effective_user = user

    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    bot.get_file = AsyncMock(return_value=file_obj)

    context = MagicMock()
    context.bot = bot
    return update, context


def _make_document_update(
    *,
    user_id: int = 42,
    file_id: str = "tg_doc_id",
    file_size: int | None = 4096,
    file_name: str | None = "report.pdf",
    mime_type: str | None = "application/pdf",
    caption: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build (update, context) mocks for an incoming Telegram document."""
    doc = MagicMock()
    doc.file_id = file_id
    doc.file_size = file_size
    doc.file_name = file_name
    doc.mime_type = mime_type

    message = MagicMock()
    message.photo = []
    message.document = doc
    message.caption = caption
    message.reply_text = AsyncMock()

    user = MagicMock()
    user.id = user_id

    update = MagicMock()
    update.message = message
    update.effective_user = user

    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    bot.get_file = AsyncMock(return_value=file_obj)

    context = MagicMock()
    context.bot = bot
    return update, context


@pytest.fixture
def uploads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect UPLOADS_DIR to a tmp dir for the test."""
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr("hive.telegram.bridge.UPLOADS_DIR", target)
    return target


async def test_photo_with_entity_caption_routes_and_persists(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_photo_update(caption="/m:dev describe this")

    await bridge._handle_attachment(update, context)

    bridge.attachment_store.save.assert_awaited_once()
    save_kwargs = bridge.attachment_store.save.await_args.kwargs
    assert save_kwargs["source"] == "telegram"
    assert save_kwargs["actor"] == "user:42"
    assert save_kwargs["forwarded_to"] == "dev"
    assert save_kwargs["mime_type"] == "image/jpeg"
    assert save_kwargs["original_name"] is None
    assert save_kwargs["size_bytes"] == 1234

    bridge._execute_command.assert_awaited_once()
    cmd_arg = bridge._execute_command.await_args.args[0]
    assert cmd_arg.name == "message"
    assert cmd_arg.target == "dev"
    assert "[Attached file:" in cmd_arg.args
    assert "describe this" in cmd_arg.args

    update.message.reply_text.assert_awaited()


async def test_photo_no_caption_stores_without_routing(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_photo_update(caption=None)

    await bridge._handle_attachment(update, context)

    bridge.attachment_store.save.assert_awaited_once()
    assert bridge.attachment_store.save.await_args.kwargs["forwarded_to"] is None
    bridge._execute_command.assert_not_awaited()

    reply = update.message.reply_text.await_args.args[0]
    assert "received" in reply.lower()
    assert "#99" in reply


async def test_document_pdf_routes_with_correct_mime(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_document_update(caption="/m:dev summarize this")

    await bridge._handle_attachment(update, context)

    save_kwargs = bridge.attachment_store.save.await_args.kwargs
    assert save_kwargs["mime_type"] == "application/pdf"
    assert save_kwargs["original_name"] == "report.pdf"
    assert save_kwargs["forwarded_to"] == "dev"

    cmd_arg = bridge._execute_command.await_args.args[0]
    assert "application/pdf" in cmd_arg.args
    assert "original: report.pdf" in cmd_arg.args


async def test_oversize_attachment_rejected(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_photo_update(file_size=21 * 1024 * 1024)

    await bridge._handle_attachment(update, context)

    bridge.attachment_store.save.assert_not_awaited()
    context.bot.get_file.assert_not_awaited()
    reply = update.message.reply_text.await_args.args[0]
    assert "too large" in reply.lower()


async def test_unauthorized_user_dropped(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir, allowed_user_ids=[42])
    update, context = _make_photo_update(user_id=999)

    await bridge._handle_attachment(update, context)

    bridge.attachment_store.save.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_plain_caption_routes_to_default_maestro(uploads_dir: Path) -> None:
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_photo_update(caption="what is in this photo?")

    await bridge._handle_attachment(update, context)

    save_kwargs = bridge.attachment_store.save.await_args.kwargs
    assert save_kwargs["forwarded_to"] == "dev"

    cmd_arg = bridge._execute_command.await_args.args[0]
    assert cmd_arg.target == "dev"
    assert "what is in this photo?" in cmd_arg.args


async def test_command_caption_does_not_route_attachment(uploads_dir: Path) -> None:
    """A non-message command like /status ignores the file but still stores it."""
    bridge = _make_bridge(uploads_dir=uploads_dir)
    update, context = _make_photo_update(caption="/status")

    await bridge._handle_attachment(update, context)

    bridge.attachment_store.save.assert_awaited_once()
    assert bridge.attachment_store.save.await_args.kwargs["forwarded_to"] is None
    bridge._execute_command.assert_not_awaited()


async def test_embedder_failure_still_persists_file(
    uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 18 regression: a Voyage outage must not break Telegram uploads."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("voyage down")

    monkeypatch.setattr("hive.telegram.bridge.embed_attachment", boom)

    store = AsyncMock(save=AsyncMock(return_value=77), update_embedding=AsyncMock())
    bridge = _make_bridge(uploads_dir=uploads_dir, attachment_store=store)
    update, context = _make_photo_update(caption=None)

    await bridge._handle_attachment(update, context)

    store.save.assert_awaited_once()
    store.update_embedding.assert_not_awaited()
    reply = update.message.reply_text.await_args.args[0]
    assert "#77" in reply
