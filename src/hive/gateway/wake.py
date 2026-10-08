"""Wake firstmate: a read-only liveness view and two guarded actions.

Status is read from firstmate's own records and never written: a ``claude`` process
whose working directory is the firstmate home (the same test ``scripts/fleet-up.sh``
uses) and the mtime of the watcher's ``state/.last-watcher-beat``. The desk reads it
from a cache that refreshes in the background, so a page never waits on it.

Both actions start firstmate through ``scripts/fleet-up.sh --only firstmate`` and
nothing else: that step is idempotent and never starts a second firstmate, so this
module holds no start logic. **Wake** is for a dead session: it only runs that step.
**Restart** is for a session that runs while its watcher is silent (connected but
wedged): it stops that one ``claude`` process, by pid, and then runs the same step. It
refuses, stopping nothing, when the session's Claude Code record or transcript shows a
turn in the last few minutes. Both share a cross-process rate limit (the desk and the
Telegram bot share one state file) and an audit line in a Hive-owned log. Callers must
have authorised the owner first; this module does not know who is asking, only what to
record.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from hive.gateway.settings import GatewaySettings

BEAT_FRESH_S = 300.0  # the watcher touches its beat every poll; older means it stopped
STATUS_TTL_S = 15.0
STATUS_TIMEOUT_S = 3.0
WAKE_COOLDOWN_S = 60.0
WAKE_TIMEOUT_S = 90.0
ACTIVE_RECENT_S = 300.0  # a turn this recent means the session may be working, not wedged
STOP_WAIT_S = 10.0
_MAX_OUTPUT = 64 * 1024
CLAUDE_HOME = Path.home() / ".claude"

# (exit code, output) of the fleet-up firstmate step; replaced by a fake in tests.
Runner = Callable[[], Awaitable[tuple[int, str]]]
# os.kill's signature; replaced by a fake in tests so no real process is signalled.
Killer = Callable[[int, int], None]
# seconds since the session with this pid last showed a turn; None: no sign of one.
Activity = Callable[[int], float | None]


@dataclass(frozen=True)
class FirstmateStatus:
    state: str  # "alive", "no-beat" (session up, watcher silent), "down" or "unknown"
    pids: tuple[int, ...]  # claude processes running in the firstmate home
    beat_age_s: float | None  # seconds since the last watcher beat; None: no beat record
    checked_at: float

    @property
    def session(self) -> bool:
        return bool(self.pids)

    @property
    def label(self) -> str:
        return {
            "alive": "alive",
            "no-beat": "session up, watcher silent",
            "down": "down",
        }.get(self.state, "unknown")

    @property
    def can_wake(self) -> bool:
        return self.state == "down"

    @property
    def can_restart(self) -> bool:
        return self.state == "no-beat"

    def describe(self) -> str:
        beat = (
            "no watcher beat record"
            if self.beat_age_s is None
            else f"last watcher beat {_ago(self.beat_age_s)} ago"
        )
        session = "session running" if self.session else "no session process"
        return f"{self.label}: {session}, {beat}"


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _session_pids(fm_home: Path, proc_root: Path) -> tuple[int, ...]:
    """Processes named ``claude`` whose working directory is ``fm_home``."""
    want = fm_home.resolve()
    pids = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() != "claude":
                continue
            if (entry / "cwd").resolve() == want:
                pids.append(int(entry.name))
        except OSError:
            continue
    return tuple(sorted(pids))


def read_status(
    fm_home: Path, now: float | None = None, proc_root: Path = Path("/proc")
) -> FirstmateStatus:
    """Read-only look at firstmate's records. Blocking but bounded: one /proc scan, one stat."""
    now = time.time() if now is None else now
    pids = _session_pids(fm_home, proc_root)
    beat_age: float | None
    try:
        beat_age = max(0.0, now - (fm_home / "state" / ".last-watcher-beat").stat().st_mtime)
    except OSError:
        beat_age = None
    if not pids:
        state = "down"
    elif beat_age is not None and beat_age <= BEAT_FRESH_S:
        state = "alive"
    else:
        state = "no-beat"
    return FirstmateStatus(state, pids, beat_age, now)


def last_activity(
    fm_home: Path, pid: int, now: float | None = None, claude_home: Path = CLAUDE_HOME
) -> float | None:
    """Seconds since the session last showed a turn, from Claude Code's own records.

    Two read-only signs: the session record ``sessions/<pid>.json`` saying ``busy``
    (aged by its ``statusUpdatedAt``), and the newest transcript in the project
    directory Claude Code keeps for ``fm_home``. None when neither shows anything.
    """
    now = time.time() if now is None else now
    ages = []
    try:
        record = json.loads((claude_home / "sessions" / f"{pid}.json").read_text())
        if record.get("status") == "busy":
            ages.append(now - float(record["statusUpdatedAt"]) / 1000)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    project = claude_home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(fm_home.resolve()))
    try:
        ages.extend(now - t.stat().st_mtime for t in project.glob("*.jsonl"))
    except OSError:
        pass
    return max(0.0, min(ages)) if ages else None


class StatusCache:
    """Stale-while-revalidate: ``peek`` answers from memory at once and refreshes behind it."""

    def __init__(
        self,
        settings: GatewaySettings,
        reader: Callable[[Path], FirstmateStatus] = read_status,
        ttl_s: float = STATUS_TTL_S,
        timeout_s: float = STATUS_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._reader = reader
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._value: FirstmateStatus | None = None
        self._task: asyncio.Task[None] | None = None

    def peek(self) -> FirstmateStatus | None:
        """The last known status (None before the first read lands); never waits."""
        stale = self._value is None or time.time() - self._value.checked_at > self._ttl_s
        if stale and (self._task is None or self._task.done()):
            with contextlib.suppress(RuntimeError):  # no running loop: caller is not async
                self._task = asyncio.get_running_loop().create_task(self._refresh())
        return self._value

    async def fresh(self) -> FirstmateStatus:
        """A new read now, bounded by the timeout; ``unknown`` when it fails or times out."""
        return await self._refresh()

    async def _refresh(self) -> FirstmateStatus:
        try:
            value = await asyncio.wait_for(
                asyncio.to_thread(self._reader, self._settings.fm_home), self._timeout_s
            )
        except Exception:  # a failed read is unknown, never the last value; never raises
            value = FirstmateStatus("unknown", (), None, time.time())
        self._value = value
        return value


@dataclass(frozen=True)
class WakeResult:
    outcome: str  # "noop", "started", "restarted", "refused", "failed", "rate-limited"
    message: str  # or "unavailable"

    @property
    def ok(self) -> bool:
        return self.outcome in ("noop", "started", "restarted")


def _clean(text: str, limit: int = 400) -> str:
    text = "".join(c if c.isprintable() or c in "\n\t" else " " for c in text).strip()
    return " | ".join(ln.strip() for ln in text.splitlines() if ln.strip())[-limit:]


def default_runner(script: Path) -> Runner:
    async def run() -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            "--only",
            "firstmate",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), WAKE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "fleet-up timed out"
        return proc.returncode or 0, out[:_MAX_OUTPUT].decode("utf-8", "replace")

    return run


class WakeService:
    """The guarded wake and restart actions, shared by the desk and the Telegram bot."""

    def __init__(
        self,
        settings: GatewaySettings,
        runner: Runner | None = None,
        status: StatusCache | None = None,
        cooldown_s: float = WAKE_COOLDOWN_S,
        killer: Killer = os.kill,
        activity: Activity | None = None,
    ) -> None:
        self.settings = settings
        self.status = status or StatusCache(settings)
        self._runner = runner
        self._cooldown_s = cooldown_s
        self._kill = killer
        self._activity = activity or (lambda pid: last_activity(settings.fm_home, pid))
        self._lock = asyncio.Lock()

    @property
    def audit_path(self) -> Path:
        return self.settings.data_dir / "wake-audit.log"

    @property
    def _state_path(self) -> Path:
        return self.settings.data_dir / "wake-state"

    @property
    def available(self) -> bool:
        return self._runner is not None or self._script() is not None

    def _script(self) -> Path | None:
        path = self.settings.fleet_up
        return path if path is not None and path.is_file() else None

    def _audit(self, action: str, who: str, via: str, outcome: str, detail: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = (
            f"{stamp} action={action} who={_clean(who, 80) or '-'} via={via} "
            f"outcome={outcome} {_clean(detail)}\n"
        )
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line)
        except OSError:
            pass  # an unwritable log must not turn a wake into a failure

    def _claim(self, now: float) -> float:
        """Take the cooldown slot across processes. Returns 0 when taken, else seconds to wait."""
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._state_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                last = float(fh.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = last + self._cooldown_s - now
            if wait > 0:
                return wait
            fh.seek(0)
            fh.truncate()
            fh.write(str(now))
            return 0.0

    async def wake(self, who: str, via: str) -> WakeResult:
        """Run the fleet-up firstmate step once, unless a firstmate session already runs."""
        return await self._guarded("wake", who, via, self._wake)

    async def restart(self, who: str, via: str) -> WakeResult:
        """Stop the one running firstmate session whose watcher is silent, then start one."""
        return await self._guarded("restart", who, via, self._restart)

    async def _guarded(
        self,
        action: str,
        who: str,
        via: str,
        step: Callable[[Runner], Awaitable[WakeResult]],
    ) -> WakeResult:
        async with self._lock:
            runner = self._runner
            if runner is None:
                script = self._script()
                runner = default_runner(script) if script is not None else None
            if runner is None:
                result = WakeResult("unavailable", "fleet-up script not found")
            else:
                result = await step(runner)
        self._audit(action, who, via, result.outcome, result.message)
        return result

    async def _cooldown(self) -> WakeResult | None:
        """Take the shared slot; a result only when the action must stop here."""
        try:
            wait = await asyncio.to_thread(self._claim, time.time())
        except OSError:
            return WakeResult("unavailable", "could not record the wake cooldown")
        if wait > 0:
            return WakeResult("rate-limited", f"Woken a moment ago; try again in {int(wait) + 1}s.")
        return None

    async def _start(self, runner: Runner) -> tuple[int, str] | WakeResult:
        try:
            code, out = await runner()
        except OSError as exc:
            return WakeResult("failed", f"could not run fleet-up: {exc.strerror or exc}")
        if code != 0:
            return WakeResult("failed", f"fleet-up exited {code}: {_clean(out) or 'no output'}")
        await self.status.fresh()  # so the next page shows the result, not the old state
        return code, out

    async def _wake(self, runner: Runner) -> WakeResult:
        status = await self.status.fresh()
        if status.session:
            return WakeResult(
                "noop", f"Firstmate's session is already running ({status.label}); nothing to do."
            )
        blocked = await self._cooldown()
        if blocked is not None:
            return blocked
        ran = await self._start(runner)
        if isinstance(ran, WakeResult):
            return ran
        _, out = ran
        # fleet-up exits 0 both when it starts a session and when it finds one already running.
        return WakeResult(
            "started" if "started in pane" in out else "noop", _clean(out) or "no output"
        )

    async def _restart(self, runner: Runner) -> WakeResult:
        status = await self.status.fresh()
        if not status.can_restart:
            return WakeResult(
                "refused",
                f"Restart is only for a running session whose watcher is silent; "
                f"firstmate is {status.label}. Nothing was stopped.",
            )
        if len(status.pids) != 1:
            return WakeResult(
                "refused",
                f"Found {len(status.pids)} claude processes in the firstmate home; "
                "not guessing which to stop. Nothing was stopped.",
            )
        (pid,) = status.pids
        age = await asyncio.to_thread(self._activity, pid)
        if age is not None and age < ACTIVE_RECENT_S:
            return WakeResult(
                "refused",
                f"Firstmate was mid-turn {_ago(age)} ago, so it may be working. "
                "Nothing was stopped; try again later.",
            )
        blocked = await self._cooldown()
        if blocked is not None:
            return blocked
        if not await asyncio.to_thread(self._stop, pid):
            return WakeResult("failed", f"firstmate session (pid {pid}) did not stop")
        ran = await self._start(runner)
        if isinstance(ran, WakeResult):
            return WakeResult(ran.outcome, f"Stopped pid {pid}, then {ran.message}")
        _, out = ran
        detail = _clean(out) or "no output"
        if "started in pane" not in out:
            return WakeResult("failed", f"Stopped pid {pid}, but no new session started: {detail}")
        return WakeResult("restarted", f"Stopped pid {pid}; {detail}")

    def _stop(self, pid: int) -> bool:
        """SIGTERM the one pid, then SIGKILL if it outlives the wait. True once it is gone."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                self._kill(pid, sig)
                deadline = time.monotonic() + STOP_WAIT_S
                while time.monotonic() < deadline:
                    self._kill(pid, 0)
                    time.sleep(0.2)
            except ProcessLookupError:
                return True
            except OSError:
                return False
        return False
