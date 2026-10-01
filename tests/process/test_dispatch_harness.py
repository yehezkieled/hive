"""send_to_entity surfaces which harness/mode ran, and a loud no-harness alert (ADR 0029)."""

from __future__ import annotations

import pytest

from hive.models.maestro import Maestro
from hive.process.message_dispatcher import MessageDispatcher
from hive.runtime.harness import (
    HarnessSpec,
    HarnessStatus,
    NoUsableHarnessError,
    RunMode,
)
from tests.process.test_message_dispatcher import (
    FakeTurnAdapter,
    StubManager,
    _hermetic_send_flags,
)


@pytest.fixture
def mgr() -> StubManager:
    m = StubManager()
    m._entities["dev"] = Maestro(name="dev", model="sonnet")
    return m


@pytest.fixture
def dispatcher(mgr: StubManager) -> MessageDispatcher:
    d = MessageDispatcher(mgr)
    mgr._handle_actions = d._handle_actions  # type: ignore[attr-defined]
    mgr._handle_parse_errors = d._handle_parse_errors  # type: ignore[attr-defined]
    return d


class _Turn(FakeTurnAdapter):
    def __init__(self, usages: list[dict]) -> None:
        super().__init__("ok")
        self._usages = usages

    async def send_turn(self, prompt: str):
        text, usage = await super().send_turn(prompt)
        return text, {**usage, **self._usages.pop(0)}


def _harness_notifications(mgr: StubManager) -> list[tuple]:
    return [c for c in mgr.notify_calls if c[1] == "harness_run"]


async def test_first_turn_announces_harness_and_mode_then_stays_quiet(dispatcher, mgr) -> None:
    ran = {"harness": "pi", "mode": "headless", "fell_back": []}
    mgr.adapter = _Turn([ran, ran, ran])

    with _hermetic_send_flags():
        for _ in range(3):
            await dispatcher.send_to_entity("dev", "go")

    notes = _harness_notifications(mgr)
    assert len(notes) == 1  # default calm: steady state is silent
    assert "dev is now running on pi (headless)" in notes[0][0]


async def test_fallback_is_announced_with_the_reason(dispatcher, mgr) -> None:
    mgr.adapter = _Turn(
        [
            {"harness": "claude", "mode": "headless", "fell_back": []},
            {
                "harness": "claude",
                "mode": "pty",
                "fell_back": ["claude (headless): quota — You've hit your limit"],
            },
        ]
    )
    with _hermetic_send_flags():
        await dispatcher.send_to_entity("dev", "1")
        await dispatcher.send_to_entity("dev", "2")

    notes = _harness_notifications(mgr)
    assert len(notes) == 2
    assert "claude (pty)" in notes[1][0] and "hit your limit" in notes[1][0]
    assert notes[1][2]["mode"] == "pty"


async def test_usage_without_harness_info_is_silent(dispatcher, mgr) -> None:
    with _hermetic_send_flags():
        await dispatcher.send_to_entity("dev", "go")
    assert _harness_notifications(mgr) == []


def _no_usable() -> NoUsableHarnessError:
    spec = HarnessSpec("claude", lambda: None, (RunMode.HEADLESS,), lambda m, c: None)  # type: ignore[arg-type,return-value]
    return NoUsableHarnessError(
        {"claude": HarnessStatus("claude", True, False, "Claude Code is logged out")},
        {"claude": spec},
    )


class _Failing(FakeTurnAdapter):
    async def send_turn(self, prompt: str):
        raise _no_usable()


async def test_no_usable_harness_alerts_once_and_propagates(dispatcher, mgr) -> None:
    mgr.adapter = _Failing()

    with _hermetic_send_flags():
        for _ in range(3):
            with pytest.raises(NoUsableHarnessError):
                await dispatcher.send_to_entity("dev", "go")

    alerts = [c for c in mgr.notify_calls if c[1] == "harness_unavailable"]
    assert len(alerts) == 1  # fleet-wide dedup: no Telegram spam
    assert "Claude Code is logged out" in alerts[0][0]


async def test_no_usable_alert_repeats_after_the_quiet_window(dispatcher, mgr, monkeypatch) -> None:
    mgr.adapter = _Failing()
    now = {"t": 1000.0}
    monkeypatch.setattr("hive.process.message_dispatcher.time.monotonic", lambda: now["t"])

    with _hermetic_send_flags():
        with pytest.raises(NoUsableHarnessError):
            await dispatcher.send_to_entity("dev", "go")
        now["t"] += 601
        with pytest.raises(NoUsableHarnessError):
            await dispatcher.send_to_entity("dev", "go")

    assert len([c for c in mgr.notify_calls if c[1] == "harness_unavailable"]) == 2
