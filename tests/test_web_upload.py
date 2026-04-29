"""Tests for ``POST /api/upload`` (Sprint 17 web file transit).

Mirrors the Telegram-side coverage in ``test_telegram_files.py`` for the
web surface: routing captions, no-caption stores, oversize rejection,
auth gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.commands.dispatch import CommandResult
from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


def _dispatcher_returning(text: str = "ok") -> MagicMock:
    d = MagicMock()
    d.dispatch = AsyncMock(return_value=CommandResult(text=text))
    d.dispatch_command = AsyncMock(return_value=CommandResult(text=text))
    return d


def _attachment_store(next_id: int = 99) -> MagicMock:
    s = MagicMock()
    s.save = AsyncMock(return_value=next_id)
    return s


@pytest.fixture
def uploads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr("hive.web.app.UPLOADS_DIR", target)
    return target


def test_upload_with_routing_text_returns_response(
    uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    dispatcher = _dispatcher_returning("dev says: looks good")
    store = _attachment_store(next_id=42)
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=dispatcher,
            attachment_store=store,
        )
    )
    resp = client.post(
        "/api/upload",
        files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"text": "/m:dev summarize"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 42
    assert body["forwarded_to"] == "dev"
    assert body["response"] == "dev says: looks good"

    save_kwargs = store.save.await_args.kwargs
    assert save_kwargs["source"] == "web"
    assert save_kwargs["actor"] == "web:user"
    assert save_kwargs["forwarded_to"] == "dev"
    assert save_kwargs["original_name"] == "report.pdf"
    assert save_kwargs["mime_type"] == "application/pdf"
    assert save_kwargs["size_bytes"] == len(b"%PDF-1.4 fake")

    enriched_cmd = dispatcher.dispatch_command.await_args.args[0]
    assert enriched_cmd.name == "message"
    assert enriched_cmd.target == "dev"
    assert "[Attached file:" in enriched_cmd.args
    assert "summarize" in enriched_cmd.args
    assert "application/pdf" in enriched_cmd.args


def test_upload_no_text_stores_only_no_dispatch(
    uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    dispatcher = _dispatcher_returning("should not be called")
    store = _attachment_store(next_id=7)
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=dispatcher,
            attachment_store=store,
        )
    )
    resp = client.post(
        "/api/upload",
        files={"file": ("note.txt", b"hi", "text/plain")},
        data={"text": ""},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"id": 7, "forwarded_to": None}
    dispatcher.dispatch_command.assert_not_awaited()
    assert store.save.await_args.kwargs["forwarded_to"] is None


def test_upload_command_caption_does_not_route(
    uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-routable caption like /status stores the file but skips dispatch."""
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    dispatcher = _dispatcher_returning("should not route")
    store = _attachment_store(next_id=8)
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=dispatcher,
            attachment_store=store,
        )
    )
    resp = client.post(
        "/api/upload",
        files={"file": ("note.txt", b"hi", "text/plain")},
        data={"text": "/status"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["forwarded_to"] is None
    dispatcher.dispatch_command.assert_not_awaited()


def test_upload_oversize_returns_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cumulative bytes > UPLOAD_MAX_BYTES → 413 with the partial file removed."""
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    target_dir = tmp_path / "uploads"
    target_dir.mkdir()
    monkeypatch.setattr("hive.web.app.UPLOADS_DIR", target_dir)
    monkeypatch.setattr("hive.web.app.UPLOAD_MAX_BYTES", 100)

    store = _attachment_store()
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=_dispatcher_returning(),
            attachment_store=store,
        )
    )
    payload = b"x" * 1024
    resp = client.post(
        "/api/upload",
        files={"file": ("big.bin", payload, "application/octet-stream")},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 413
    store.save.assert_not_awaited()
    assert list(target_dir.iterdir()) == []


def test_upload_requires_token(uploads_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=_dispatcher_returning(),
            attachment_store=_attachment_store(),
        )
    )
    resp = client.post(
        "/api/upload",
        files={"file": ("note.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 401


def test_upload_without_attachment_store_returns_503(
    uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    client = TestClient(
        create_app(
            process_manager=_bare_pm(),
            command_dispatcher=_dispatcher_returning(),
        )
    )
    resp = client.post(
        "/api/upload",
        files={"file": ("note.txt", b"hi", "text/plain")},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 503
