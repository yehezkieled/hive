"""HIVE_TELEGRAM_QUOTA_ALERTS toggle on the Telegram bridge.

Plan-quota alerts (the 80/90/100% band crossings plus the monitor's own
blind/recovered meta-alerts) are *ambient*, not actionable — utilisation is
always available via `/quota` and the web quota chip. So they get their own gate,
independent of the Ticket-041 actionable-alert gate: silencing the quota pings
must not silence decisions/approvals, and turning the actionable alerts down must
not silence quota.
"""

from __future__ import annotations

import pytest

from hive import config
from hive.notifications import QUOTA_KINDS, Notification
from hive.telegram.bridge import TelegramBridge


def _bridge_with_recorder() -> tuple[TelegramBridge, list[str]]:
    """A bare bridge (bypassing the heavy __init__) with a recording sink."""
    bridge = TelegramBridge.__new__(TelegramBridge)
    sent: list[str] = []

    async def _fake_send(message: str) -> None:
        sent.append(message)

    bridge._send_notification = _fake_send  # type: ignore[method-assign]
    return bridge, sent


@pytest.mark.parametrize("kind", sorted(QUOTA_KINDS))
async def test_quota_alerts_off_suppresses_every_quota_kind(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(config, "TELEGRAM_QUOTA_ALERTS", False)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="quota noise", kind=kind))
    assert sent == []


async def test_quota_alerts_on_relays_quota_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_QUOTA_ALERTS", True)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="crossed 80%", kind="quota_warn"))
    assert sent == ["crossed 80%"]


async def test_quota_off_still_relays_actionable_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: killing quota pings must not cost you approvals."""
    monkeypatch.setattr(config, "TELEGRAM_QUOTA_ALERTS", False)
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", True)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="otter needs you", kind="decision_request"))
    assert sent == ["otter needs you"]


async def test_actionable_alerts_off_still_relays_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """The converse: HIVE_TELEGRAM_ALERTS is not a quota switch."""
    monkeypatch.setattr(config, "TELEGRAM_ALERTS", False)
    monkeypatch.setattr(config, "TELEGRAM_QUOTA_ALERTS", True)
    bridge, sent = _bridge_with_recorder()
    await bridge.send(Notification(text="crossed 90%", kind="quota_urgent"))
    assert sent == ["crossed 90%"]


async def test_quota_kinds_disjoint_from_alert_kinds() -> None:
    """The two gates must never overlap, or one would mask the other."""
    from hive.notifications import ALERT_KINDS

    assert QUOTA_KINDS.isdisjoint(ALERT_KINDS)


async def test_quota_kinds_covers_every_band_the_monitor_emits() -> None:
    """Drift guard: add a band to QuotaMonitor and it must land in the gate.

    Without this, a new band (say 95%) would silently bypass
    HIVE_TELEGRAM_QUOTA_ALERTS and start pinging again.
    """
    from hive.runtime.quota_monitor import _BAND_KIND

    assert set(_BAND_KIND.values()) <= QUOTA_KINDS
