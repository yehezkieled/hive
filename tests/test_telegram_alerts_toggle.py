"""Ticket 041 — HIVE_TELEGRAM_ALERTS toggle on the Telegram bridge.

When alerts are turned down, the bridge drops the actionable alert-kinds (they
go to Web Push instead) but keeps relaying everything else as a debug/log
surface. Default (on) relays everything, unchanged from before 041.
"""

from __future__ import annotations

import pytest

from hive import config
from hive.notifications import Notification
from hive.telegram.bridge import TelegramBridge


def _bridge_with_recorder() -> tuple[TelegramBridge, list[str]]:
    """A bare bridge (bypassing the heavy __init__) with a recording sink.

    ``send`` only touches ``config`` and ``self._send_notification``, so a
    __new__'d instance with a fake sink exercises the toggle without spinning up
    a real Bot API application.
    """
    bridge = TelegramBridge.__new__(TelegramBridge)
    sent: list[str] = []

    async def _fake_send(message: str) -> None:
        sent.append(message)

    bridge._send_notification = _fake_send  # type: ignore[method-assign]
    return bridge, sent


async def test_alerts_off_suppresses_actionable_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", False)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="otter needs you", kind="decision_request"))
    assert sent == []


async def test_alerts_off_still_relays_non_actionable_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", False)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="just an update", kind="entity_message"))
    assert sent == ["just an update"]


@pytest.mark.parametrize(
    "kind",
    [
        "decision_request",
        "mode_request",
        "vault_action_pending",
        "workflow_completed",
        "workflow_failed",
    ],
)
async def test_alerts_off_suppresses_every_actionable_kind(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", False)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="x", kind=kind))
    assert sent == []


async def test_alerts_on_relays_actionable_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", True)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="otter needs you", kind="decision_request"))
    assert sent == ["otter needs you"]
