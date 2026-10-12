"""Automatic firstmate recovery: a watchdog ladder over the guarded Wake and Restart actions.

Hive is the backup captain. When firstmate's watcher goes silent while work is under way,
the watchdog climbs a short ladder, one rung at a time, and then stops:

1. silent for ``silent_after_s`` (T1): **Wake** at once if no session runs. A session that
   runs with a silent watcher is never restarted on this rung: it is announced and gets
   T2 of grace first.
2. still silent ``restart_after_s`` (T2) after the first rung: **one Restart session** (or,
   if the session is down again, one more Wake). Restart's own mid-turn refusal applies
   unchanged; a held-back Restart is announced once and tried again.
3. still silent ``give_up_after_s - silent_after_s`` after the first rung (T3, with the last
   rung given T2 to work), or ``max_restarts`` automatic restarts already in ``window_s``:
   **halt**, alert the captain, and make no further attempt.

A silence episode ends when no work is under way or firstmate beats again; the next one starts
a fresh ladder, while the automatic-restart history (cap and cooldown) carries over.

T2 and T3 count from the first rung (the ladder start), never from the watcher beat's age, so
a beat that is already very old when the switch goes on or the gateway comes back still gets
the first rung (Wake, or the announcement that starts the grace) before any Restart or halt.

It starts nothing and stops nothing itself: every action is ``WakeService.wake`` or
``WakeService.restart`` (fleet-up's firstmate step), so Hive holds no start logic and no
firstmate rules. It never touches workers, merges or decides. It is **off by default**; the
same switch that turns it on is the kill switch. Firstmate's own settings are only read
(through ``fmconfig``), and when they or Hive's watchdog config are missing or unparsable it
does nothing and says so.

State survives a gateway restart: the ladder position, the automatic-restart history and the
event log live in one file, claimed under a lock and written **before** an action runs, so a
restart in the middle of a step can only skip a repeat, never double it. The same file is the
desk's and Telegram's announcement feed (the bot relays new events to the allowlist).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from hive.gateway import fmconfig
from hive.gateway.wake import HELD_BACK, MANUAL_MARK, FirstmateStatus, WakeResult, WakeService

log = logging.getLogger("hive.gateway.watchdog")

CONFIG_NAME = "watchdog.json"
STATE_NAME = "watchdog-state.json"
LOCK_NAME = "watchdog-state.lock"
MAX_EVENTS = 100
POLL_S = 30.0
WHO = "watchdog"

# (name, default, type); every threshold lives in ``watchdog.json`` beside the switch.
_FIELDS: tuple[tuple[str, float, type], ...] = (
    ("silent_after_s", 600.0, float),
    ("restart_after_s", 600.0, float),
    ("give_up_after_s", 2400.0, float),
    ("restart_cooldown_s", 900.0, float),
    ("max_restarts", 2, int),
    ("window_s", 6 * 3600.0, float),
)

ON_DETAIL = (
    "Hive will then wake or restart the firstmate session by itself when its watcher stays "
    "silent while work is under way: one Wake after 10 minutes, one Restart session after 10 "
    "more, then it stops and alerts you. At most 2 automatic restarts in 6 hours, never while "
    "firstmate is mid-turn. It touches no worker, merges nothing and decides nothing. "
    "Switching it off stops it at once."
)

# Is work under way? True/False, or None when it cannot be told (nothing is done then).
WorkProbe = Callable[[], Awaitable[bool | None]]
# Called with each new event so the desk can refresh and push it.
Announce = Callable[[dict], Awaitable[None]]


class ConfigError(Exception):
    """``watchdog.json`` exists but cannot be used."""


@dataclass(frozen=True)
class WatchdogConfig:
    enabled: bool = False
    silent_after_s: float = 600.0  # T1: the watcher beat's age that starts the ladder
    restart_after_s: float = 600.0  # T2: time after the first rung before Restart
    give_up_after_s: float = 2400.0  # T3: T1 plus the time after the first rung to stop
    restart_cooldown_s: float = 900.0  # gap between two automatic restarts
    max_restarts: int = 2  # automatic restarts allowed per ``window_s``
    window_s: float = 6 * 3600.0

    def validate(self) -> None:
        if min(self.silent_after_s, self.restart_after_s, self.restart_cooldown_s) <= 0:
            raise ConfigError("thresholds must be positive")
        if self.max_restarts < 0 or self.window_s <= 0:
            raise ConfigError("max_restarts and window_s must not be negative")
        if self.give_up_after_s <= self.silent_after_s + self.restart_after_s:
            raise ConfigError("give_up_after_s must exceed silent_after_s + restart_after_s")


@dataclass
class Ladder:
    stage: int = 0  # 0 watching, 1 first rung taken, 2 second rung taken
    halted: bool = False
    halted_reason: str = ""
    started_at: float = 0.0  # when the first rung was taken: T2 and T3 count from here
    last_action_at: float = 0.0  # when the latest rung was taken
    restarts: list[float] = field(default_factory=list)  # automatic restarts, newest last
    manual_seen: float = 0.0  # the manual-action mark already handled
    silent_since: float = 0.0  # when a missing beat record was first noticed
    once: dict[str, str] = field(default_factory=dict)  # announcement dedupe: key -> detail


@dataclass(frozen=True)
class View:
    """What the desk shows: the switch, the ladder and the latest events."""

    enabled: bool
    config_error: str
    halted: bool
    halted_reason: str
    stage: int
    events: tuple[dict, ...]

    @property
    def label(self) -> str:
        if self.config_error:
            return "off (config unusable)"
        if not self.enabled:
            return "off"
        return "halted" if self.halted else "on"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def parse_config(text: str) -> WatchdogConfig:
    """The config from JSON text; ``ConfigError`` for anything not understood."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{CONFIG_NAME} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_NAME} must hold a JSON object")
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("enabled must be true or false")
    values: dict[str, object] = {}
    for name, default, kind in _FIELDS:
        raw = data.get(name, default)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ConfigError(f"{name} must be a number")
        if kind is int and raw != int(raw):
            raise ConfigError(f"{name} must be a whole number")
        values[name] = kind(raw)
    cfg = WatchdogConfig(enabled=enabled, **values)  # type: ignore[arg-type]
    cfg.validate()
    return cfg


class WatchdogStore:
    """Hive's own watchdog config plus the persisted ladder and event log (``data_dir``)."""

    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time) -> None:
        self.dir = data_dir
        self._clock = clock

    @property
    def config_path(self) -> Path:
        return self.dir / CONFIG_NAME

    @property
    def state_path(self) -> Path:
        return self.dir / STATE_NAME

    # ---- config ---------------------------------------------------------------

    def load_config(self) -> tuple[WatchdogConfig, str]:
        """(config, error). A missing file is the default: off. An unusable one is off too."""
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return WatchdogConfig(), ""
        except (OSError, UnicodeDecodeError) as exc:
            return WatchdogConfig(), f"{CONFIG_NAME} unreadable ({type(exc).__name__})"
        try:
            return parse_config(text), ""
        except ConfigError as exc:
            return WatchdogConfig(), str(exc)

    def set_enabled(self, on: bool, who: str, via: str) -> str:
        """Flip the switch, keeping the thresholds, and put the ladder back to watching.

        Turning on needs a usable config; turning off always works (it is the kill switch,
        and replaces an unusable file with a default one that is off). Returns the event text.
        """
        cfg, err = self.load_config()
        if err and on:
            raise ConfigError(err)
        body = {"enabled": on}
        body.update({n: getattr(cfg, n) for n, _, _ in _FIELDS})
        _atomic_write(self.config_path, json.dumps(body, indent=2) + "\n")
        text = f"Automatic recovery switched {'ON' if on else 'OFF'} by {who} ({via})."
        with self._locked() as (ladder, events, seq):
            ladder.stage, ladder.halted, ladder.halted_reason = 0, False, ""
            ladder.once.clear()
            self._add(events, seq, "info", "switch", text)
        return text

    # ---- state ----------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self):
        """Hold the state lock; yields (ladder, events, seq-box) and saves on a clean exit."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ladder, events, seq = self._read()
            box = [seq]
            yield ladder, events, box
            self._write(ladder, events, box[0])

    def _read(self) -> tuple[Ladder, list[dict], int]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            lad = raw.get("ladder", {})
            ladder = Ladder(
                stage=int(lad.get("stage", 0)),
                halted=bool(lad.get("halted", False)),
                halted_reason=str(lad.get("halted_reason", "")),
                started_at=float(lad.get("started_at", 0.0)),
                last_action_at=float(lad.get("last_action_at", 0.0)),
                restarts=[float(t) for t in lad.get("restarts", [])],
                manual_seen=float(lad.get("manual_seen", 0.0)),
                silent_since=float(lad.get("silent_since", 0.0)),
                once={str(k): str(v) for k, v in lad.get("once", {}).items()},
            )
            events = [e for e in raw.get("events", []) if isinstance(e, dict)]
            return ladder, events, int(raw.get("seq", 0))
        except FileNotFoundError:
            return Ladder(), [], 0
        except (OSError, ValueError, TypeError, AttributeError):
            # An unreadable state file must not act: halt until the owner switches it again.
            log.warning("watchdog state unreadable; halting the ladder")
            return Ladder(halted=True, halted_reason="the watchdog state file is unreadable"), [], 0

    def _write(self, ladder: Ladder, events: list[dict], seq: int) -> None:
        body = {
            "ladder": {
                "stage": ladder.stage,
                "halted": ladder.halted,
                "halted_reason": ladder.halted_reason,
                "started_at": ladder.started_at,
                "last_action_at": ladder.last_action_at,
                "restarts": ladder.restarts,
                "manual_seen": ladder.manual_seen,
                "silent_since": ladder.silent_since,
                "once": ladder.once,
            },
            "events": events[-MAX_EVENTS:],
            "seq": seq,
        }
        _atomic_write(self.state_path, json.dumps(body))

    def _add(self, events: list[dict], seq: list[int], level: str, kind: str, text: str) -> dict:
        seq[0] += 1
        event = {"id": seq[0], "at": self._clock(), "level": level, "kind": kind, "text": text}
        events.append(event)
        return event

    # ---- reads for the desk and the Telegram relay ------------------------------

    def events(self, after: int = 0) -> list[dict]:
        _, events, _ = self._read()
        return [e for e in events if int(e.get("id", 0)) > after]

    def last_event_id(self) -> int:
        return self._read()[2]

    def view(self) -> View:
        cfg, err = self.load_config()
        ladder, events, _ = self._read()
        return View(
            cfg.enabled and not err,
            err,
            ladder.halted,
            ladder.halted_reason,
            ladder.stage,
            tuple(events[-5:][::-1]),
        )


class Watchdog:
    """The loop: reads liveness, climbs the ladder, announces, never blocks a request."""

    def __init__(
        self,
        wake: WakeService,
        store: WatchdogStore,
        work: WorkProbe,
        announce: Announce | None = None,
        clock: Callable[[], float] = time.time,
        poll_s: float = POLL_S,
    ) -> None:
        self.wake = wake
        self.store = store
        self._work = work
        self._announce = announce
        self._clock = clock
        self._poll_s = poll_s
        self._task: asyncio.Task[None] | None = None

    # ---- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # the loop must outlive any one bad tick
                log.exception("watchdog tick failed")
            await asyncio.sleep(self._poll_s)

    # ---- switch (the desk's and Telegram's shared entry) ---------------------------

    async def set_enabled(self, on: bool, who: str, via: str) -> str:
        text = await asyncio.to_thread(self.store.set_enabled, on, who, via)
        self.wake.audit("watchdog-switch", who, via, "on" if on else "off", text)
        await self._publish({"kind": "switch", "level": "info", "text": text})
        return text

    # ---- one tick -----------------------------------------------------------------

    async def tick(self) -> None:
        """One look at firstmate and at most one ladder step. Safe to call at any time."""
        cfg, err = await asyncio.to_thread(self.store.load_config)
        if err:
            await self._once("config", err, "alert", "config", f"Automatic recovery is off: {err}.")
            return
        await self._clear_once("config")
        if not cfg.enabled:
            return
        now = self._clock()
        if await self._manual_reset():
            return
        gate = await asyncio.to_thread(self._fm_gate)
        if gate:
            await self._once(
                "fmconfig",
                gate,
                "alert",
                "config",
                f"Automatic recovery is paused: firstmate's settings are unusable ({gate}). "
                "Nothing was started or stopped.",
            )
            return
        await self._clear_once("fmconfig")
        status = await self.wake.status.fresh()
        if status.state == "unknown":
            return
        if status.state == "alive":
            await self._recovered(now)
            return
        work = await self._work()
        if work is not True:  # nothing under way (or unknowable): nothing to do
            await self._mutate(_end_episode if work is False else _forget_silence)
            return
        age = await self._silent_age(status, now)
        if age < cfg.silent_after_s:
            return
        await self._climb(cfg, status, age, now)

    def _fm_gate(self) -> str:
        """Why firstmate's settings cannot be relied on (empty when they can). Read-only."""
        loaded = fmconfig.load(self.wake.settings.fm_home)
        return loaded.projects.error or fmconfig.worker_gate(loaded)

    async def _manual_reset(self) -> bool:
        mark = await asyncio.to_thread(self._manual_mark)

        def step(ladder: Ladder, events: list[dict], seq: list[int]) -> dict | None:
            if mark <= ladder.manual_seen:
                return None
            ladder.manual_seen = mark
            ladder.stage, ladder.halted, ladder.halted_reason = 0, False, ""
            ladder.once.clear()
            return self.store._add(
                events, seq, "info", "reset", "A manual Wake or Restart reset the ladder."
            )

        event = await self._mutate(step)
        await self._emit(event)
        return event is not None

    def _manual_mark(self) -> float:
        try:
            return float((self.store.dir / MANUAL_MARK).read_text().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    async def _silent_age(self, status: FirstmateStatus, now: float) -> float:
        if status.beat_age_s is not None:
            return status.beat_age_s

        def step(ladder: Ladder, events: list[dict], seq: list[int]) -> float:
            if not ladder.silent_since:
                ladder.silent_since = now
            return now - ladder.silent_since

        return await self._mutate(step)

    async def _recovered(self, now: float) -> None:
        def step(ladder: Ladder, events: list[dict], seq: list[int]) -> dict | None:
            _forget_silence(ladder, events, seq)
            if not (ladder.stage or ladder.halted or ladder.once):
                return None
            was = "halted" if ladder.halted else f"on rung {ladder.stage}"
            ladder.stage, ladder.halted, ladder.halted_reason = 0, False, ""
            ladder.once.clear()
            return self.store._add(
                events, seq, "info", "recovered", f"Firstmate is beating again (ladder was {was})."
            )

        await self._emit(await self._mutate(step))

    # ---- the ladder -----------------------------------------------------------------

    async def _climb(
        self, cfg: WatchdogConfig, status: FirstmateStatus, age: float, now: float
    ) -> None:
        ladder = await self._mutate(lambda lad, ev, seq: _copy(lad))
        if ladder.halted:
            return
        if ladder.stage == 0:
            await self._first_rung(status, age, now)
            return
        since_start = now - ladder.started_at
        if ladder.stage == 1 and since_start >= cfg.restart_after_s:
            if await self._second_rung(cfg, status, now):
                return
        if (
            since_start >= cfg.give_up_after_s - cfg.silent_after_s
            and now - ladder.last_action_at >= cfg.restart_after_s
        ):
            await self._halt(f"firstmate still silent after {_mins(age)}")

    async def _first_rung(self, status: FirstmateStatus, age: float, now: float) -> None:
        """Wake if there is no session; otherwise just announce. Claimed before it acts."""
        down = status.state == "down"

        def claim(ladder: Ladder, events: list[dict], seq: list[int]) -> dict | None:
            if ladder.stage != 0:
                return None
            ladder.stage, ladder.started_at, ladder.last_action_at = 1, now, now
            what = "waking it" if down else "its session is up, so not waking it"
            return self.store._add(
                events,
                seq,
                "warn",
                "silent",
                f"Firstmate has been silent for {_mins(age)} while work is under way: {what}.",
            )

        event = await self._mutate(claim)
        if event is None:
            return  # another process already took this rung
        await self._emit(event)
        if down:
            await self._wake("Wake")

    async def _second_rung(self, cfg: WatchdogConfig, status: FirstmateStatus, now: float) -> bool:
        """Take the second rung if it can be taken now; True when it was."""
        if status.state == "down":
            if not await self._claim_second(cfg, now, restart=False):
                return False
            await self._wake("Wake (retry)")
            return True
        recent = [t for t in (await self._restarts()) if now - t < cfg.window_s]
        if len(recent) >= cfg.max_restarts:
            await self._halt(
                f"{len(recent)} automatic restarts already in the last {_mins(cfg.window_s)}"
            )
            return False
        if recent and now - recent[-1] < cfg.restart_cooldown_s:
            wait = cfg.restart_cooldown_s - (now - recent[-1])
            await self._once(
                "cooldown",
                "x",
                "info",
                "cooldown",
                f"Restart is cooling down after the last automatic one; waiting {_mins(wait)}.",
            )
            return False
        if not await self._claim_second(cfg, now, restart=True):
            return False
        result = await self.wake.restart(WHO, "watchdog")
        if result.outcome in HELD_BACK:
            await self._release_second(now)  # nothing was stopped: give the rung back
            await self._once(
                "deferred",
                "x",
                "info",
                "deferred",
                f"Restart held back ({result.outcome}): {result.message} "
                "It will be tried again until the ladder gives up.",
            )
            return False
        await self._record("restart", "Restart session", result)
        return True

    async def _claim_second(self, cfg: WatchdogConfig, now: float, restart: bool) -> bool:
        def claim(ladder: Ladder, events: list[dict], seq: list[int]) -> bool:
            if ladder.stage != 1 or ladder.halted:
                return False
            ladder.stage, ladder.last_action_at = 2, now
            if restart:
                ladder.restarts = [t for t in ladder.restarts if now - t < cfg.window_s] + [now]
            return True

        return await self._mutate(claim)

    async def _release_second(self, now: float) -> None:
        def release(ladder: Ladder, events: list[dict], seq: list[int]) -> None:
            if ladder.restarts and ladder.restarts[-1] == now:
                ladder.restarts.pop()
            if ladder.stage == 2:
                ladder.stage = 1
                ladder.last_action_at = ladder.started_at

        await self._mutate(release)

    async def _restarts(self) -> list[float]:
        return await self._mutate(lambda lad, ev, seq: list(lad.restarts))

    async def _halt(self, reason: str) -> None:
        def stop(ladder: Ladder, events: list[dict], seq: list[int]) -> dict | None:
            if ladder.halted:
                return None
            ladder.halted, ladder.halted_reason = True, reason
            return self.store._add(
                events,
                seq,
                "alert",
                "halted",
                f"Automatic recovery stopped: {reason}. No more attempts until firstmate "
                "beats again, you wake or restart it, or you switch recovery off and on.",
            )

        event = await self._mutate(stop)
        await self._emit(event)

    async def _wake(self, label: str) -> None:
        """Run the real guarded Wake (never anything else) and announce its result."""
        await self._record("wake", label, await self.wake.wake(WHO, "watchdog"))

    async def _record(self, which: str, label: str, result: WakeResult) -> None:
        level = "info" if result.ok else "warn"
        text = f"{label}: {result.outcome}. {result.message}"

        def record(ladder: Ladder, events: list[dict], seq: list[int]) -> dict:
            return self.store._add(events, seq, level, which, text)

        await self._emit(await self._mutate(record))

    # ---- helpers --------------------------------------------------------------------

    async def _mutate(self, fn: Callable[[Ladder, list[dict], list[int]], object]):
        def run():
            with self.store._locked() as (ladder, events, seq):
                return fn(ladder, events, seq)

        return await asyncio.to_thread(run)

    async def _once(self, key: str, detail: str, level: str, kind: str, text: str) -> None:
        """Announce once per distinct ``detail`` until it clears."""

        def step(ladder: Ladder, events: list[dict], seq: list[int]) -> dict | None:
            if ladder.once.get(key) == detail:
                return None
            ladder.once[key] = detail
            return self.store._add(events, seq, level, kind, text)

        await self._emit(await self._mutate(step))

    async def _clear_once(self, key: str) -> None:
        await self._mutate(lambda lad, ev, seq: lad.once.pop(key, None))

    async def _emit(self, event: dict | None) -> None:
        """Audit and announce a new event: Telegram reads the feed, the desk is told here."""
        if event is None:
            return
        self.wake.audit(f"watchdog-{event['kind']}", WHO, "watchdog", event["level"], event["text"])
        await self._publish(event)

    async def _publish(self, event: dict) -> None:
        if self._announce is not None:
            with contextlib.suppress(Exception):  # an announce failure must not stop the ladder
                await self._announce(event)


def _forget_silence(ladder: Ladder, events: list[dict], seq: list[int]) -> None:
    ladder.silent_since = 0.0


def _end_episode(ladder: Ladder, events: list[dict], seq: list[int]) -> None:
    """No work under way: the next silence starts a fresh ladder. A halt and the restart
    history (the cap and the cooldown) stay."""
    _forget_silence(ladder, events, seq)
    if not ladder.halted and ladder.stage:
        ladder.stage, ladder.started_at = 0, 0.0
        ladder.once.clear()


def _copy(ladder: Ladder) -> Ladder:
    return Ladder(
        ladder.stage,
        ladder.halted,
        ladder.halted_reason,
        ladder.started_at,
        ladder.last_action_at,
        list(ladder.restarts),
        ladder.manual_seen,
        ladder.silent_since,
        dict(ladder.once),
    )


def _mins(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}h" if seconds >= 5400 else f"{max(1, round(seconds / 60))} min"
