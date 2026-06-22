"""Tests for the Ticket 038 web decision endpoints.

- ``POST /api/decision/{entity}/reply`` — answer a maestro's 029 decision from
  the web. Thin wrapper over the command-dispatcher message path
  (clear→send→route lives in ``_send_to_entity``; not re-implemented here).
- ``GET /api/decisions/pending`` — scan entities ``awaiting_decision`` so a
  fresh load re-shows outstanding decisions (SSE is best-effort).

Covers: auth gate, dispatch wiring, empty reply, unknown entity, and the
pending scan. Mirrors ``test_web_mode_request_endpoints.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.commands.dispatch import CommandResult
from hive.models.maestro import Maestro
from hive.telegram.commands import Command
from hive.web.app import create_app

_TOKEN = "secret"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _pm(entities=None) -> MagicMock:
    pm = MagicMock()
    pm.entities = {e.name: e for e in (entities or [])}
    return pm


def _dispatcher(result: CommandResult) -> MagicMock:
    cd = MagicMock()
    cd.dispatch_command = AsyncMock(return_value=result)
    return cd


class TestReplyAuthGate:
    def test_missing_header_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        client = TestClient(
            create_app(
                process_manager=_pm(), command_dispatcher=_dispatcher(CommandResult(text="x"))
            )
        )
        resp = client.post("/api/decision/otter/reply", json={"reply": "yes"})
        assert resp.status_code == 401

    def test_wrong_token_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        client = TestClient(
            create_app(
                process_manager=_pm(), command_dispatcher=_dispatcher(CommandResult(text="x"))
            )
        )
        resp = client.post(
            "/api/decision/otter/reply",
            json={"reply": "yes"},
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status_code == 401


class TestReplyHappyPath:
    def test_dispatches_message_and_returns_response(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        pm = _pm([Maestro(name="otter", model="opus")])
        cd = _dispatcher(CommandResult(text="otter: reusing auth", routed=True, entity="otter"))
        client = TestClient(create_app(process_manager=pm, command_dispatcher=cd))

        resp = client.post("/api/decision/otter/reply", json={"reply": "reuse auth"}, headers=_AUTH)

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "entity": "otter", "text": "otter: reusing auth"}
        cd.dispatch_command.assert_awaited_once_with(
            Command(name="message", target="otter", args="reuse auth"), actor="web:user"
        )


class TestReplyValidation:
    def test_empty_reply_is_bad_request(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        pm = _pm([Maestro(name="otter", model="opus")])
        cd = _dispatcher(CommandResult(text="x"))
        client = TestClient(create_app(process_manager=pm, command_dispatcher=cd))

        resp = client.post("/api/decision/otter/reply", json={"reply": "   "}, headers=_AUTH)

        assert resp.status_code == 400
        cd.dispatch_command.assert_not_awaited()

    def test_unknown_entity_is_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        pm = _pm([])  # no entities
        cd = _dispatcher(CommandResult(text="x"))
        client = TestClient(create_app(process_manager=pm, command_dispatcher=cd))

        resp = client.post("/api/decision/ghost/reply", json={"reply": "hi"}, headers=_AUTH)

        assert resp.status_code == 404
        cd.dispatch_command.assert_not_awaited()


class TestPending:
    def test_unauthorized_without_token(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        client = TestClient(create_app(process_manager=_pm()))
        assert client.get("/api/decisions/pending").status_code == 401

    def test_lists_awaiting_entities_with_question(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        waiting = Maestro(name="otter", model="opus")
        waiting.awaiting_decision = True
        waiting.last_decision_question = "reuse auth table or new sessions table?"
        calm = Maestro(name="dev", model="sonnet")
        client = TestClient(create_app(process_manager=_pm([waiting, calm])))

        resp = client.get("/api/decisions/pending", headers=_AUTH)

        assert resp.status_code == 200
        assert resp.json() == {
            "decisions": [
                {"entity": "otter", "question": "reuse auth table or new sessions table?"}
            ]
        }

    def test_empty_when_none_awaiting(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", _TOKEN)
        client = TestClient(create_app(process_manager=_pm([Maestro(name="dev", model="sonnet")])))
        resp = client.get("/api/decisions/pending", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"decisions": []}
