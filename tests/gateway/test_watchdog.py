"""The automatic recovery ladder: every rung, thresholds, cap, cooldown, switch and fail-safes.

Fakes only: a fake clock, a fake WakeService (a few tests wrap the real one around the
fakes from ``test_wake``), temp dirs for Hive's data dir and a synthetic firstmate home.
Nothing here starts or stops a session, and no test reads the real firstmate home or the
running desk.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway.actions import Tokens
from hive.gateway.app import create_app
from hive.gateway.settings import GatewaySettings
from hive.gateway.wake import MANUAL_MARK, FirstmateStatus, WakeResult, WakeService
from hive.gateway.watchdog import (
    ConfigError,
    Watchdog,
    WatchdogStore,
    parse_config,
)
from hive.telegram.bridge import WATCHDOG_CURSOR, TelegramBridge
from tests.gateway.fmcfg import write_config
from tests.gateway.test_wake import (
    FM_PID,
    Fake,
    Killer,
    _bridge,
    _message_update,
    _service,
    _settings,
)

OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
MIN = 60.0
T0 = 1_800_000_000.0
CSRF = Tokens(b"w" * 32)
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
    "accept": "application/json",
}


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeWake:
    """The slice of WakeService the watchdog uses, with a scripted firstmate."""

    def __init__(self, tmp_path: Path, clock: Clock, state: str = "no-beat") -> None:
        self.clock = clock
        self.settings = SimpleNamespace(
            fm_home=write_config(tmp_path / "fm"), data_dir=tmp_path / "d"
        )
        self.state = state  # "alive" | "no-beat" | "down" | "unknown"
        self.beat_at: float | None = clock() - 30 * MIN if state != "alive" else clock()
        self.calls: list[str] = []
        self.audits: list[tuple[str, str]] = []
        self.results: dict[str, WakeResult] = {}
        self.status = SimpleNamespace(fresh=self._fresh)

    async def _fresh(self) -> FirstmateStatus:
        pids = (FM_PID,) if self.state in ("alive", "no-beat") else ()
        age = None if self.beat_at is None else max(0.0, self.clock() - self.beat_at)
        return FirstmateStatus(self.state, pids, age, self.clock())

    def silent_for(self, seconds: float) -> None:
        self.beat_at = self.clock() - seconds

    async def wake(self, who: str, via: str) -> WakeResult:
        self.calls.append(f"wake:{who}:{via}")
        return self.results.get("wake", WakeResult("started", "started a session"))

    async def restart(self, who: str, via: str) -> WakeResult:
        self.calls.append(f"restart:{who}:{via}")
        return self.results.get("restart", WakeResult("restarted", "restarted"))

    def audit(self, action: str, who: str, via: str, outcome: str, detail: str) -> None:
        self.audits.append((action, f"{who}/{via}/{outcome}"))


class Rig:
    def __init__(self, tmp_path: Path, state: str = "no-beat", work: bool | None = True) -> None:
        self.clock = Clock()
        self.wake = FakeWake(tmp_path, self.clock, state)
        self.store = WatchdogStore(tmp_path / "d", self.clock)
        self.work = work
        self.announced: list[dict] = []
        self.dog = self.make()

    def make(self) -> Watchdog:
        async def work() -> bool | None:
            return self.work

        async def announce(event: dict) -> None:
            self.announced.append(event)

        return Watchdog(self.wake, self.store, work, announce, self.clock)  # type: ignore[arg-type]

    def on(self, **thresholds: float) -> None:
        body = {"enabled": True, **thresholds}
        self.store.dir.mkdir(parents=True, exist_ok=True)
        self.store.config_path.write_text(json.dumps(body))

    async def tick(self) -> None:
        await self.dog.tick()

    async def at(self, minutes: float) -> None:
        """Move the clock to where the watcher beat is ``minutes`` old, and tick."""
        assert self.wake.beat_at is not None
        self.clock.t = self.wake.beat_at + minutes * MIN
        await self.tick()

    def begin(self, silent_min: float = 11) -> None:
        self.wake.silent_for(silent_min * MIN)

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.store.events()]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _wd(tmp_path: Path, **kw: object) -> Rig:
    return Rig(tmp_path, **kw)  # type: ignore[arg-type]


# ---- off by default, switch, kill switch ---------------------------------------------


async def test_off_by_default_nothing_happens_however_silent(rig: Rig) -> None:
    rig.wake.silent_for(5 * 3600)
    for _ in range(3):
        await rig.tick()
        rig.clock.advance(15 * MIN)
        rig.wake.silent_for(5 * 3600)
    assert rig.wake.calls == [] and rig.store.events() == []
    view = rig.store.view()
    assert view.label == "off" and not view.enabled


def test_default_config_is_off_and_thresholds_match_the_spec(tmp_path: Path) -> None:
    cfg, err = WatchdogStore(tmp_path).load_config()
    assert err == "" and cfg.enabled is False
    assert (cfg.silent_after_s, cfg.restart_after_s) == (600, 600)
    assert cfg.give_up_after_s == 2400 and cfg.restart_cooldown_s == 900
    assert (cfg.max_restarts, cfg.window_s) == (2, 6 * 3600)


async def test_switch_on_arms_it_and_off_is_the_kill_switch(rig: Rig) -> None:
    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    await rig.tick()
    assert rig.wake.calls == []  # still off
    await rig.dog.set_enabled(True, OWNER, "desk")
    assert rig.store.view().enabled
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]
    await rig.dog.set_enabled(False, OWNER, "desk")
    rig.clock.advance(30 * MIN)
    rig.wake.silent_for(41 * MIN)
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]  # nothing more once off
    assert ("watchdog-switch", f"{OWNER}/desk/on") in rig.wake.audits
    assert ("watchdog-switch", f"{OWNER}/desk/off") in rig.wake.audits
    assert any(e["kind"] == "switch" for e in rig.store.events())


async def test_switch_keeps_thresholds_and_resets_the_ladder(rig: Rig) -> None:
    rig.on(silent_after_s=300, restart_after_s=300, give_up_after_s=1500)
    rig.wake.state = "down"
    rig.wake.silent_for(6 * MIN)
    await rig.tick()  # rung 1
    await rig.dog.set_enabled(False, OWNER, "desk")
    await rig.dog.set_enabled(True, OWNER, "desk")
    cfg, _ = rig.store.load_config()
    assert cfg.enabled and cfg.silent_after_s == 300 and cfg.give_up_after_s == 1500
    assert rig.store.view().stage == 0


# ---- the ladder ------------------------------------------------------------------------


async def test_below_t1_nothing_happens(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.wake.silent_for(9 * MIN)
    await rig.tick()
    assert rig.wake.calls == [] and rig.store.events() == []


async def test_rung_one_wakes_a_dead_firstmate_at_t1(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.begin(11)
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]
    assert rig.kinds() == ["silent", "wake"]
    assert rig.store.view().stage == 1


async def test_rung_one_only_announces_when_a_session_runs(rig: Rig) -> None:
    rig.on()
    rig.begin(11)  # no-beat: session up, watcher silent
    await rig.tick()
    assert rig.wake.calls == []
    assert rig.kinds() == ["silent"] and "not waking" in rig.store.events()[0]["text"]
    assert rig.store.view().stage == 1


async def test_rung_two_restarts_once_after_t2(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()  # rung 1
    await rig.at(19)
    assert rig.wake.calls == []  # not yet: T1 + T2 = 20 minutes
    await rig.at(21)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]
    await rig.at(25)
    await rig.at(30)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]  # one attempt only
    assert rig.store.view().stage == 2


async def test_rung_two_wakes_again_when_the_session_is_down(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    assert rig.wake.calls == ["wake:watchdog:watchdog"] * 2  # a wake, never a restart
    assert rig.store._read()[0].restarts == []


async def test_rung_two_waits_for_t2_after_the_first_rung(rig: Rig) -> None:
    """Turned on late, with a very old silence: T2 still has to pass after rung one."""
    rig.on(give_up_after_s=7200)
    rig.begin(25)  # already past T1 + T2 at the first look
    await rig.tick()
    assert rig.wake.calls == []  # session up: rung one only announces
    await rig.at(30)
    assert rig.wake.calls == []  # only 5 minutes since rung one
    await rig.at(36)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]


async def test_halts_at_t3_and_attempts_nothing_more(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    await rig.at(41)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]
    assert rig.kinds()[-1] == "halted" and rig.store.view().halted
    last = rig.announced[-1]
    assert last["level"] == "alert" and "stopped" in last["text"]
    for m in (50, 90, 200):
        await rig.at(m)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]
    assert rig.kinds().count("halted") == 1


async def test_thresholds_come_from_hive_config(rig: Rig) -> None:
    rig.on(silent_after_s=120, restart_after_s=60, give_up_after_s=600)
    rig.wake.silent_for(121)
    await rig.tick()
    assert rig.kinds() == ["silent"]
    await rig.at(3.2)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]


async def test_no_work_under_way_or_unknown_does_nothing(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.wake.silent_for(15 * MIN)
    rig.work = False
    await rig.tick()
    rig.work = None
    await rig.tick()
    assert rig.wake.calls == [] and rig.store.events() == []
    rig.work = True
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_alive_or_unknown_firstmate_is_left_alone(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "unknown"
    rig.wake.silent_for(3 * 3600)
    await rig.tick()
    rig.wake.state = "alive"
    rig.wake.beat_at = rig.clock()
    await rig.tick()
    assert rig.wake.calls == [] and rig.store.events() == []


async def test_a_missing_beat_record_ages_from_the_first_look(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.wake.beat_at = None
    await rig.tick()
    assert rig.wake.calls == []  # first look only starts the clock
    rig.clock.advance(11 * MIN)
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_recovery_resets_the_ladder(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    rig.wake.state = "alive"
    rig.wake.beat_at = rig.clock()
    await rig.tick()
    assert rig.kinds()[-1] == "recovered" and rig.store.view().stage == 0
    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    await rig.tick()  # a fresh episode starts at rung one again
    assert rig.wake.calls[-1] == "wake:watchdog:watchdog"


# ---- cap, cooldown, mid-turn refusal ---------------------------------------------------


def _seed_restarts(rig: Rig, ages_min: list[float]) -> None:
    with rig.store._locked() as (ladder, _events, _seq):
        ladder.restarts = [rig.clock() - m * MIN for m in ages_min]


async def test_two_restarts_in_six_hours_halts_instead_of_a_third(rig: Rig) -> None:
    rig.on()
    _seed_restarts(rig, [300, 100])
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    assert rig.wake.calls == []
    assert rig.kinds()[-1] == "halted" and "2 automatic restarts" in rig.store.events()[-1]["text"]


async def test_old_restarts_fall_out_of_the_window(rig: Rig) -> None:
    rig.on()
    _seed_restarts(rig, [7 * 60, 6.5 * 60])
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]


async def test_cooldown_holds_a_restart_until_it_has_passed(rig: Rig) -> None:
    rig.on(give_up_after_s=7200)
    _seed_restarts(rig, [5])  # one automatic restart 5 minutes before the first look
    rig.begin(11)
    await rig.tick()
    await rig.at(21)  # 5 + 21 minutes since... cooldown 15: already over
    assert rig.wake.calls == ["restart:watchdog:watchdog"]
    rig2 = _wd(rig.store.dir.parent / "two")
    rig2.on(give_up_after_s=7200, restart_cooldown_s=3 * 3600)
    _seed_restarts(rig2, [60])
    rig2.begin(11)
    await rig2.tick()
    await rig2.at(21)
    await rig2.at(30)
    assert rig2.wake.calls == []
    assert rig2.kinds().count("cooldown") == 1  # said once, not every tick
    assert not rig2.store.view().halted


async def test_a_refused_restart_is_given_back_and_retried(rig: Rig) -> None:
    rig.on(give_up_after_s=7200)
    rig.wake.results["restart"] = WakeResult("refused", "Firstmate was mid-turn 1m ago")
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    await rig.at(22)
    assert rig.wake.calls == ["restart:watchdog:watchdog"] * 2  # tried again, not given up
    assert rig.store._read()[0].restarts == []  # a refusal costs no restart
    assert rig.kinds().count("deferred") == 1
    rig.wake.results["restart"] = WakeResult("restarted", "ok")
    await rig.at(23)
    assert len(rig.store._read()[0].restarts) == 1


async def test_a_restart_that_ran_counts_even_when_it_failed(rig: Rig) -> None:
    rig.on()
    rig.wake.results["restart"] = WakeResult("failed", "did not start")
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    assert len(rig.store._read()[0].restarts) == 1
    assert rig.kinds()[-1] == "restart"
    assert rig.announced[-1]["level"] == "warn"


async def test_mid_turn_session_is_never_restarted_with_the_real_wake_service(
    tmp_path: Path,
) -> None:
    """The real WakeService's refusal is the guard: a recent turn stops the restart."""
    clock = Clock(time.time())
    runner, killer = Fake(), Killer()
    service = _service(tmp_path, "no-beat", runner, killer=killer, active_s=30.0)
    service.settings = _settings(tmp_path)
    service.settings.fm_home.mkdir(parents=True, exist_ok=True)
    write_config(service.settings.fm_home)
    store = WatchdogStore(service.settings.data_dir, clock)
    store.dir.mkdir(parents=True)
    store.config_path.write_text(json.dumps({"enabled": True, "give_up_after_s": 7200}))

    # the world's beat is "silent for an hour", so the real status reads no-beat
    async def work() -> bool:
        return True

    dog = Watchdog(service, store, work, None, clock)
    await dog.tick()  # rung one: session up, only announces
    clock.t += 21 * MIN
    await dog.tick()  # silent age comes from the world (4000 s) and the ladder has time passed
    clock.t += 5 * MIN
    await dog.tick()
    assert killer.sent == [] and runner.calls == 0
    assert store._read()[0].restarts == []
    assert (
        "action=restart who=watchdog via=watchdog outcome=refused" in service.audit_path.read_text()
    )


async def test_restart_goes_through_when_idle_with_the_real_wake_service(tmp_path: Path) -> None:
    clock = Clock(time.time())
    runner, killer = Fake(), Killer()
    service = _service(tmp_path, "no-beat", runner, killer=killer, active_s=None, cooldown=0.0)
    service.settings = _settings(tmp_path)
    write_config(service.settings.fm_home)
    store = WatchdogStore(service.settings.data_dir, clock)
    store.dir.mkdir(parents=True)
    store.config_path.write_text(json.dumps({"enabled": True, "give_up_after_s": 20000}))

    async def work() -> bool:
        return True

    dog = Watchdog(service, store, work, None, clock)
    await dog.tick()
    clock.t += 21 * MIN
    await dog.tick()
    assert [s for _p, s in killer.sent][:1] == [15]  # SIGTERM to the one pid, nothing else
    assert runner.calls == 1
    audit = service.audit_path.read_text()
    assert "action=restart who=watchdog via=watchdog outcome=restarted" in audit
    assert "action=watchdog-silent" in audit


# ---- manual actions reset the ladder -------------------------------------------------


async def test_a_manual_wake_or_restart_resets_the_ladder(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    await rig.at(41)  # halted
    assert rig.store.view().halted
    (rig.store.dir / MANUAL_MARK).write_text(str(time.time()))
    await rig.tick()
    assert not rig.store.view().halted and rig.store.view().stage == 0
    assert rig.kinds()[-1] == "reset"
    await rig.tick()
    assert rig.kinds().count("reset") == 1  # the same mark is not handled twice


async def test_wake_service_marks_manual_actions_but_not_the_watchdogs_own(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "down", Fake())
    mark = service.settings.data_dir / MANUAL_MARK
    await service.wake("watchdog", "watchdog")
    assert not mark.exists()
    await service.wake(OWNER, "desk")
    assert mark.exists() and float(mark.read_text()) > 0
    assert os.stat(mark).st_mode & 0o777 == 0o600


# ---- fail safe ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"projects": None},
        {"projects": "# registry only, no rows\n"},
        {"dispatch": "{ not json", "harness": None},
        {"dispatch": None, "harness": None},
        {"permission": "banana"},
    ],
)
async def test_unusable_firstmate_config_does_nothing_and_alerts_once(
    rig: Rig, kwargs: dict
) -> None:
    rig.on()
    write_config(rig.wake.settings.fm_home, **kwargs)
    rig.wake.state = "down"
    rig.wake.silent_for(15 * MIN)
    for _ in range(3):
        await rig.tick()
        rig.clock.advance(MIN)
    assert rig.wake.calls == []
    assert rig.kinds() == ["config"]  # one alert, not one per tick
    assert (
        rig.announced[0]["level"] == "alert"
        and "nothing was started" in rig.announced[0]["text"].lower()
    )
    write_config(rig.wake.settings.fm_home)  # fixed: it resumes
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_missing_firstmate_files_are_never_copied_or_written(rig: Rig) -> None:
    rig.on()
    home = rig.wake.settings.fm_home
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    rig.wake.state = "down"
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    after = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    assert before == after  # only read
    assert not any(
        str(home) in p.read_text() for p in rig.store.dir.glob("*.json") if "fm" in p.name
    )


@pytest.mark.parametrize(
    "text",
    [
        "{nope",
        "[]",
        '{"enabled": "yes"}',
        '{"silent_after_s": "ten"}',
        '{"silent_after_s": -1}',
        '{"max_restarts": 1.5}',
        '{"silent_after_s": 600, "restart_after_s": 600, "give_up_after_s": 900}',
    ],
)
async def test_unusable_hive_config_means_off_and_one_alert(rig: Rig, text: str) -> None:
    rig.store.dir.mkdir(parents=True, exist_ok=True)
    rig.store.config_path.write_text(text)
    rig.wake.state = "down"
    rig.wake.silent_for(5 * 3600)
    await rig.tick()
    await rig.tick()
    assert rig.wake.calls == []
    assert rig.kinds() == ["config"]
    view = rig.store.view()
    assert view.label == "off (config unusable)" and view.config_error
    with pytest.raises(ConfigError):
        rig.store.set_enabled(True, OWNER, "desk")
    rig.store.set_enabled(False, OWNER, "desk")  # the kill switch still works
    assert rig.store.load_config() == (rig.store.load_config()[0], "")


def test_parse_config_accepts_a_full_file() -> None:
    cfg = parse_config(json.dumps({"enabled": True, "max_restarts": 0, "window_s": 100}))
    assert cfg.enabled and cfg.max_restarts == 0


async def test_unreadable_state_halts_rather_than_acts(rig: Rig) -> None:
    rig.on()
    rig.store.state_path.write_text("{corrupt")
    rig.wake.state = "down"
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    assert rig.wake.calls == []


# ---- persistence and no double action --------------------------------------------------


async def test_state_survives_a_gateway_restart_without_repeating_a_step(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()  # rung one
    rig.dog = rig.make()  # a new process: same files, nothing in memory
    await rig.tick()
    assert rig.kinds() == ["silent"]
    await rig.at(21)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]
    rig.dog = rig.make()
    await rig.at(22)
    await rig.at(23)
    assert rig.wake.calls == ["restart:watchdog:watchdog"]  # not twice across restarts


async def test_a_crash_in_the_middle_of_a_restart_does_not_repeat_it(rig: Rig) -> None:
    """The step is claimed in the state file before the action runs."""
    rig.on()
    rig.begin(11)
    await rig.tick()

    async def boom(who: str, via: str) -> WakeResult:
        raise RuntimeError("gateway died mid-restart")

    rig.wake.restart = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await rig.at(21)
    rig.dog = rig.make()
    rig.wake.restart = FakeWake.restart.__get__(rig.wake)  # type: ignore[method-assign]
    await rig.at(22)
    await rig.at(23)
    assert rig.wake.calls == []
    assert rig.store.view().stage == 2


async def test_two_watchdogs_on_one_state_do_not_double_act(rig: Rig) -> None:
    rig.on()
    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    other = rig.make()
    await asyncio.gather(rig.dog.tick(), other.tick())
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_the_loop_survives_a_failing_tick_and_stops_cleanly(rig: Rig) -> None:
    rig.on()
    boom = {"n": 0}

    async def work() -> bool:
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("snapshot exploded")
        return True

    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    dog = Watchdog(rig.wake, rig.store, work, None, rig.clock, poll_s=0.01)  # type: ignore[arg-type]
    dog.start()
    for _ in range(100):
        if rig.wake.calls:
            break
        await asyncio.sleep(0.02)
    await dog.stop()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_the_loop_never_blocks_the_event_loop(rig: Rig) -> None:
    rig.on()

    async def work() -> bool:
        await asyncio.sleep(0.3)
        return True

    dog = Watchdog(rig.wake, rig.store, work, None, rig.clock, poll_s=0.01)  # type: ignore[arg-type]
    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    dog.start()
    ticks = 0
    start = time.monotonic()
    while time.monotonic() - start < 0.2:
        await asyncio.sleep(0.01)
        ticks += 1
    await dog.stop()
    assert ticks > 10  # the loop kept turning while the watchdog waited


# ---- announcements and audit -------------------------------------------------------------


async def test_every_step_is_announced_and_audited(rig: Rig) -> None:
    rig.on()
    rig.begin(11)
    await rig.tick()
    await rig.at(21)
    await rig.at(41)
    kinds = [e["kind"] for e in rig.announced]
    assert kinds == ["silent", "restart", "halted"]
    audited = [a for a, _ in rig.wake.audits]
    assert audited == ["watchdog-silent", "watchdog-restart", "watchdog-halted"]
    assert all(who.startswith("watchdog/watchdog") for _, who in rig.wake.audits)


async def test_an_announce_failure_does_not_stop_the_ladder(rig: Rig) -> None:
    rig.on()

    async def bad(event: dict) -> None:
        raise OSError("push service down")

    rig.dog._announce = bad  # type: ignore[assignment]
    rig.wake.state = "down"
    rig.wake.silent_for(11 * MIN)
    await rig.tick()
    assert rig.wake.calls == ["wake:watchdog:watchdog"]


async def test_the_event_log_is_capped(rig: Rig) -> None:
    for i in range(150):
        with rig.store._locked() as (_l, events, seq):
            rig.store._add(events, seq, "info", "x", str(i))
    events = rig.store.events()
    assert len(events) == 100 and events[-1]["text"] == "149"
    assert rig.store.last_event_id() == 150


# ---- the desk -----------------------------------------------------------------------------


def _client(tmp_path: Path, fake: FakeWake | None = None) -> tuple[TestClient, WatchdogStore]:
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=write_config(tmp_path / "fm"),
        snapshot_ttl_s=0,
        data_dir=tmp_path / "data",
        live=False,
        fleet_up=None,
    )
    (tmp_path / "fm" / "bin").mkdir(parents=True, exist_ok=True)
    wake = WakeService(settings, runner=Fake())
    app = create_app(settings, tokens=CSRF, wake=wake)
    client = TestClient(app, client=("127.0.0.1", 5000), follow_redirects=False)
    return client, WatchdogStore(settings.data_dir)


def _act(client: TestClient, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/", **fields}
    return client.post("/act/watchdog", content=urlencode(data), headers=GOOD)


def test_the_desk_switch_turns_on_only_after_a_confirm_step(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        first = _act(client, to="on")
        body = first.json()
        assert body["confirm"] is True and body["extra"]["to"] == "on" and body["detail"]
        assert not store.view().enabled  # the first tap changed nothing
        forged = _act(client, to="on", rid="web-0123456789abcdef", step="forged")
        assert forged.json().get("confirm") is True and not store.view().enabled
        done = _act(client, to="on", rid=body["rid"], step=body["step"])
        assert done.status_code == 200 and done.json()["ok"] is True
        assert store.view().enabled
        again = _act(client, to="on", rid=body["rid"], step=body["step"])  # a replay is no new act
        assert again.json()["ok"] is True


def test_the_desk_switch_turns_off_at_once(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        store.set_enabled(True, OWNER, "test")
        res = _act(client, to="off")
        assert res.status_code == 200 and res.json()["ok"] is True
        assert not store.view().enabled
        assert "switched OFF" in res.json()["message"]
        audit = (tmp_path / "data" / "wake-audit.log").read_text()
        assert "action=watchdog-switch" in audit and f"who={OWNER}" in audit


def test_the_desk_switch_is_owner_only_and_csrf_checked(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        stranger = {**GOOD, "tailscale-user-login": "intruder@example.test"}
        data = urlencode({"csrf": CSRF.csrf(), "to": "off", "next": "/"})
        assert client.post("/act/watchdog", content=data, headers=stranger).status_code == 403
        noncsrf = urlencode({"csrf": "bad", "to": "off", "next": "/"})
        assert client.post("/act/watchdog", content=noncsrf, headers=GOOD).status_code == 403
        assert _act(client, to="maybe").status_code == 400
        assert not store.view().enabled


def test_the_desk_refuses_to_turn_on_with_an_unusable_config(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("{nope")
        body = _act(client, to="on").json()
        res = _act(client, to="on", rid=body["rid"], step=body["step"])
        assert res.status_code == 409 and "Not turned on" in res.json()["message"]
        assert _act(client, to="off").status_code == 200  # the kill switch still works


def test_the_desk_page_shows_state_events_and_the_matching_button(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    page_headers = {k: v for k, v in GOOD.items() if k not in ("accept", "content-type", "origin")}
    with client:
        page = client.get("/", headers=page_headers).text
        assert "Automatic recovery: off" in page and "name=to value=on" in page
        store.set_enabled(True, OWNER, "test")
        with store._locked() as (ladder, events, seq):
            store._add(events, seq, "alert", "halted", "Automatic recovery stopped: <b>x</b>")
            ladder.halted, ladder.halted_reason = True, "firstmate still silent after 40 min"
        page = client.get("/", headers=page_headers).text
        assert "Automatic recovery: halted" in page and "name=to value=off" in page
        assert "firstmate still silent after 40 min" in page
        assert "Automatic recovery stopped: &lt;b&gt;x&lt;/b&gt;" in page  # escaped


# ---- Telegram ------------------------------------------------------------------------------


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _tg(tmp_path: Path, allowed: list[int]) -> TelegramBridge:
    bridge = _bridge(tmp_path, "down", Fake(), allowed)
    bridge.watchdog_store = WatchdogStore(bridge.wake.settings.data_dir)
    bridge._app = SimpleNamespace(bot=_Bot())  # type: ignore[assignment]
    return bridge


async def test_telegram_toggle_for_the_allowlisted_chat(tmp_path: Path) -> None:
    bridge = _tg(tmp_path, [42])
    for text, enabled in (("/watchdog on", True), ("/watchdog off", False)):
        update = _message_update(42, text)
        await bridge._handle_message(update, None)  # type: ignore[arg-type]
        assert bridge.watchdog_store.view().enabled is enabled
        assert "switched" in update.message.replies[0][0]
    update = _message_update(42, "/watchdog status")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    assert update.message.replies[0][0] == "Automatic recovery: off"
    update = _message_update(42, "/watchdog sideways")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    assert "Usage" in update.message.replies[0][0]
    audit = bridge.wake.audit_path.read_text()
    assert audit.count("action=watchdog-switch") == 2 and "via=telegram" in audit


@pytest.mark.parametrize("allowed", [[], [7]])
async def test_telegram_toggle_is_not_for_strangers_or_an_empty_allowlist(
    tmp_path: Path, allowed: list[int]
) -> None:
    bridge = _tg(tmp_path, allowed)
    update = _message_update(42, "/watchdog on")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    assert update.message.replies == [] and not bridge.watchdog_store.view().enabled


async def test_telegram_relays_each_event_once_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hive.config.SUMMARY_CHAT_ID", "")
    bridge = _tg(tmp_path, [42, 43])
    store = bridge.watchdog_store
    with store._locked() as (_l, events, seq):
        store._add(events, seq, "info", "old", "history before the bot started")
    await bridge._relay_watchdog_once()  # first run: baseline only, no replay
    assert bridge._app.bot.sent == []  # type: ignore[union-attr]
    with store._locked() as (_l, events, seq):
        store._add(events, seq, "alert", "halted", "Automatic recovery stopped: test")
    await bridge._relay_watchdog_once()
    await bridge._relay_watchdog_once()
    sent = bridge._app.bot.sent  # type: ignore[union-attr]
    assert sorted(c for c, _ in sent) == [42, 43]
    assert all("Automatic recovery stopped: test" in t for _, t in sent)
    bridge._app.bot.sent.clear()  # type: ignore[union-attr]
    await bridge._relay_watchdog_once()  # "restarted" bot: the cursor file remembers
    assert bridge._app.bot.sent == []  # type: ignore[union-attr]
    assert (bridge.wake.settings.data_dir / WATCHDOG_CURSOR).read_text().strip() == "2"
