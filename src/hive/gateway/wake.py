"""Wake firstmate: a read-only liveness view and one guarded action.

Status is read from firstmate's own records and never written: a ``claude`` process
whose working directory is the firstmate home (the same test ``scripts/fleet-up.sh``
uses) and the mtime of the watcher's ``state/.last-watcher-beat``. The desk reads it
from a cache that refreshes in the background, so a page never waits on it.

The action reuses ``scripts/fleet-up.sh --only firstmate`` and nothing else: that step
is idempotent and never starts a second firstmate, so this module holds no start logic.
It adds only what a remote control needs: an alive check before the script is run, a
cross-process rate limit (the desk and the Telegram bot share one state file), and an
audit line in a Hive-owned log. Callers must have authorised the owner before calling
``WakeService.wake``; this module does not know who is asking, only what to record.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
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
_MAX_OUTPUT = 64 * 1024

# (exit code, output) of the fleet-up firstmate step; replaced by a fake in tests.
Runner = Callable[[], Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class FirstmateStatus:
    state: str  # "alive", "no-beat" (session up, watcher silent), "down" or "unknown"
    session: bool  # a claude process runs in the firstmate home
    beat_age_s: float | None  # seconds since the last watcher beat; None: no beat record
    checked_at: float

    @property
    def label(self) -> str:
        return {
            "alive": "alive",
            "no-beat": "session up, watcher silent",
            "down": "down",
        }.get(self.state, "unknown")

    @property
    def needs_wake(self) -> bool:
        return self.state != "alive"

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


def _session_running(fm_home: Path, proc_root: Path) -> bool:
    """A process named ``claude`` has ``fm_home`` as its working directory."""
    try:
        want = fm_home.resolve()
        entries = list(proc_root.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() != "claude":
                continue
            if (entry / "cwd").resolve() == want:
                return True
        except OSError:
            continue
    return False


def read_status(
    fm_home: Path, now: float | None = None, proc_root: Path = Path("/proc")
) -> FirstmateStatus:
    """Read-only look at firstmate's records. Blocking but bounded: one /proc scan, one stat."""
    now = time.time() if now is None else now
    session = _session_running(fm_home, proc_root)
    beat_age: float | None
    try:
        beat_age = max(0.0, now - (fm_home / "state" / ".last-watcher-beat").stat().st_mtime)
    except OSError:
        beat_age = None
    if not session:
        state = "down"
    elif beat_age is not None and beat_age <= BEAT_FRESH_S:
        state = "alive"
    else:
        state = "no-beat"
    return FirstmateStatus(state, session, beat_age, now)


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
        """A new read now, bounded by the timeout; ``unknown`` when it does not finish."""
        await self._refresh()
        return self._value or FirstmateStatus("unknown", False, None, time.time())

    async def _refresh(self) -> None:
        try:
            self._value = await asyncio.wait_for(
                asyncio.to_thread(self._reader, self._settings.fm_home), self._timeout_s
            )
        except Exception:  # a failed read keeps the last value; never raises into a page
            if self._value is None:
                self._value = FirstmateStatus("unknown", False, None, time.time())


@dataclass(frozen=True)
class WakeResult:
    outcome: str  # "noop", "started", "failed", "rate-limited" or "unavailable"
    message: str

    @property
    def ok(self) -> bool:
        return self.outcome in ("noop", "started")


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
    """The guarded wake action, shared by the desk and the Telegram bot."""

    def __init__(
        self,
        settings: GatewaySettings,
        runner: Runner | None = None,
        status: StatusCache | None = None,
        cooldown_s: float = WAKE_COOLDOWN_S,
    ) -> None:
        self.settings = settings
        self.status = status or StatusCache(settings)
        self._runner = runner
        self._cooldown_s = cooldown_s
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

    def _audit(self, who: str, via: str, outcome: str, detail: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = (
            f"{stamp} who={_clean(who, 80) or '-'} via={via} outcome={outcome} {_clean(detail)}\n"
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
        """Run the fleet-up firstmate step once, unless firstmate is already alive."""
        async with self._lock:
            result = await self._wake(who, via)
        self._audit(who, via, result.outcome, result.message)
        return result

    async def _wake(self, who: str, via: str) -> WakeResult:
        runner = self._runner
        if runner is None:
            script = self._script()
            if script is None:
                return WakeResult("unavailable", "fleet-up script not found")
            runner = default_runner(script)
        status = await self.status.fresh()
        if status.state == "alive":
            return WakeResult("noop", "Firstmate is already alive; nothing to do.")
        try:
            wait = await asyncio.to_thread(self._claim, time.time())
        except OSError:
            return WakeResult("unavailable", "could not record the wake cooldown")
        if wait > 0:
            return WakeResult("rate-limited", f"Woken a moment ago; try again in {int(wait) + 1}s.")
        try:
            code, out = await runner()
        except OSError as exc:
            return WakeResult("failed", f"could not run fleet-up: {exc.strerror or exc}")
        detail = _clean(out) or "no output"
        if code != 0:
            return WakeResult("failed", f"fleet-up exited {code}: {detail}")
        await self.status.fresh()  # so the next page shows the result, not the old state
        # fleet-up exits 0 both when it starts a session and when it finds one already running.
        return WakeResult("started" if "started in pane" in out else "noop", detail)
