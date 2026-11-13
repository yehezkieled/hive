"""The Machine page's readings: the chain that has to hold for the desk to be reachable.

Strictly read only. Every probe is an unelevated read with a short time-out: a Windows
fact through ``powershell.exe``, the PC's Tailscale through its own ``status`` command, a
``/proc`` or systemd read, the one expiry field of Claude Code's credentials file, and one
outbound TCP connect that sends nothing. Probes run off the request path: the page renders
``MachineMonitor.peek()``, which answers from memory and refreshes behind it. A probe that
fails, times out or returns something not understood reads ``unknown``; it never raises
and never guesses.

Nothing that could be a secret leaves this module. Probe output is parsed into typed
readings (numbers, dates, fixed words); a failure's reason is a fixed phrase, never raw
output; and every displayed string still goes through ``redact``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hive import gonedark
from hive.gateway.settings import GatewaySettings
from hive.gateway.wake import StatusCache, last_activity
from hive.gateway.watchdog import View

OK, WARN, DOWN, UNKNOWN = "ok", "warn", "down", "unknown"

PROBE_TIMEOUT_S = 2.5  # every command probe; the brief's ceiling is 3 s
NET_TIMEOUT_S = 2.0
SLOW_TTL_S = 600.0  # boot time and installed updates change rarely: re-read at most this often
STALE_S = 120.0  # readings older than this are shown as "checking", not as their old state
DAY_S = 86400.0
WARN_DAYS = 14  # an expiry inside this many days is a warning
URGENT_DAYS = 3  # and inside this many it gets the red band
FLEET_OVERDUE_S = 900.0  # the fleet-up timer fires every 5 min
MAX_CREDENTIALS_BYTES = 1 << 20
NAME_CHARS = 18  # a device name is clipped to this before it is shown

DESK_UNIT = "hive-gateway.service"
FLEET_TIMER = "hive-fleet-up.timer"
FLEET_SERVICE = "hive-fleet-up.service"
UNIT_PROPS = (
    "Id",
    "LoadState",
    "ActiveState",
    "Result",
    "MainPID",
    "ActiveEnterTimestamp",
    "LastTriggerUSec",
    "ExecMainStatus",
    "ExecMainExitTimestamp",
)

# Unelevated reads only: CIM and Get-HotFix queries, process names, two registry key tests.
PS_BOOT = "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"
_CV = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
PS_SESSION = (
    "$ErrorActionPreference='SilentlyContinue'; "
    "@{logonui=@(Get-Process -Name LogonUI | ForEach-Object {$_.SessionId}); "
    "shell=@(Get-Process -Name explorer | ForEach-Object {$_.SessionId}); "
    f"cbs=(Test-Path '{_CV}\\Component Based Servicing\\RebootPending'); "
    f"wu=(Test-Path '{_CV}\\WindowsUpdate\\Auto Update\\RebootRequired')}} "
    "| ConvertTo-Json -Compress"
)
PS_UPDATES = (
    "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1 "
    "@{n='on';e={$_.InstalledOn.ToString('yyyy-MM-dd')}} | ConvertTo-Json -Compress"
)

# Fixed reasons for an unknown reading. Raw probe output is never used as a reason.
TIMED_OUT = "the check did not answer in time"
FAILED = "the check answered with an error"
NOT_STARTED = "the check could not be started"
NOT_UNDERSTOOD = "the answer was not understood"
SWITCHED_OFF = "this check is switched off"
# What to do about a sign-in that is running out: both are done from the Sign-ins page.
RENEW_CLAUDE = "Renew it on the Sign-ins page"
EXTEND_TAILSCALE = "Extend it: see the Sign-ins page"

# ---- redaction ---------------------------------------------------------------------------

REDACTED = "[redacted]"
_REDACT_RES = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b(?:sk-|tskey-|gh[pousr]_|github_pat_|xox[abposr]-)\S+"),
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # an email address or account login
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+"),  # any link
    re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    re.compile(r"[A-Za-z0-9_\-+/=]{20,}"),  # any long unbroken run: a key, hash or token
)


def redact(text: object, limit: int = 160) -> str:
    """Display text with anything token-like, any address and any link replaced.

    Applied to every field the page shows. The page's own wording is short words and
    dates, so the last rule (a 20-character unbroken run) never touches it.
    """
    s = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    for rx in _REDACT_RES:
        s = rx.sub(REDACTED, s)
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# ---- readings ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Unknown:
    """A probe that could not answer. ``why`` is one of the fixed phrases above."""

    why: str


@dataclass(frozen=True)
class Reading[T]:
    value: T | Unknown
    at: float  # when it was read


@dataclass(frozen=True)
class Session:
    state: str  # "signed-in", "locked" or "nobody"
    restart_pending: bool | None  # None: the restart flags could not be read


@dataclass(frozen=True)
class Device:
    name: str
    key_expiry: float


@dataclass(frozen=True)
class Tailscale:
    backend: str  # Tailscale's own state word: "Running", "NeedsLogin", "Stopped", ...
    online: bool
    key_expiry: float | None  # None: this PC's key does not expire
    key_expired: bool
    others: tuple[Device, ...]  # the same account's other devices whose key has an expiry


@dataclass(frozen=True)
class Unit:
    load: str  # "loaded", "not-found", ...
    active: str  # "active", "inactive", "failed", ...
    result: str  # "success", "exit-code", ...
    main_pid: int
    since: float | None  # when it last became active
    last_trigger: float | None  # a timer's last firing
    exit_status: int | None
    exited_at: float | None


@dataclass(frozen=True)
class Units:
    desk: Unit | None
    timer: Unit | None
    service: Unit | None


@dataclass(frozen=True)
class Firstmate:
    state: str  # wake.FirstmateStatus.state: "alive", "no-beat", "down" or "unknown"
    idle_s: float | None  # seconds since the session last showed a turn
    watchdog: str  # watchdog.View.label: "on", "off", "halted", "off (config unusable)"
    watchdog_trouble: bool  # halted, or its config is unusable


@dataclass(frozen=True)
class Net:
    ok: bool
    host: str
    ms: int | None = None
    why: str = ""


@dataclass(frozen=True)
class Alarm:
    """The gone-dark alarm: configured or not, and its last tick. Never the address."""

    on: bool
    last: gonedark.Outcome | None
    interval_s: int


@dataclass(frozen=True)
class Readings:
    at: float  # when the latest round finished
    boot: Reading[float]  # Windows boot time (epoch)
    session: Reading[Session]
    updated_on: Reading[date]  # the newest installed Windows update's date
    tailscale: Reading[Tailscale]
    wsl_up_s: Reading[float]
    units: Reading[Units]
    firstmate: Reading[Firstmate]
    claude_expiry: Reading[float]  # the Claude sign-in's expiry (epoch)
    net: Reading[Net]
    gonedark: Reading[Alarm] | None = None  # None: not set up to be read here


# ---- parsers: text in, a typed reading or Unknown out; never raise -------------------------

_BOOT_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z")
_DAY_RE = re.compile(r"\d{4}-\d\d-\d\d")
_STAMP_RE = re.compile(r"@(\d{1,12})(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{0,23}")
_Y2K = 946684800.0  # epochs before this are "no date", not a date


def _json(out: str) -> object:
    try:
        return json.loads(out.lstrip("\ufeff").strip())
    except ValueError:
        return None


def _word(value: object) -> str:
    """A state word as the tool printed it, or "" when it is not a plain word."""
    return value if isinstance(value, str) and _WORD_RE.fullmatch(value) else ""


def _epoch(value: object) -> float | None:
    """An RFC 3339 time as an epoch; None for anything else, including Go's zero time."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    try:
        at = when.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return at if at > _Y2K else None


def parse_boot(out: str) -> float | Unknown:
    """Windows' last boot time, from PowerShell's round-trip UTC format."""
    m = _BOOT_RE.fullmatch(out.strip())
    try:
        at = datetime.fromisoformat(m[1]).replace(tzinfo=UTC).timestamp() if m else 0.0
    except ValueError:
        at = 0.0
    return at if at > _Y2K else Unknown(NOT_UNDERSTOOD)


def _session_ids(value: object) -> set[int] | None:
    """PowerShell prints one id as a number and several as a list; None: neither."""
    items = value if isinstance(value, list) else [] if value is None else [value]
    if all(isinstance(i, int) and not isinstance(i, bool) for i in items):
        return set(items)
    return None


def parse_session(out: str) -> Session | Unknown:
    """Signed in, locked or nobody, plus whether Windows has a restart pending.

    A session with the desktop shell (``explorer``) is signed in; it is locked while the
    lock screen's process (``LogonUI``) runs in that same session. Best effort: Windows
    has no unelevated "is it locked" read.
    """
    data = _json(out)
    if not isinstance(data, dict) or "shell" not in data or "logonui" not in data:
        return Unknown(NOT_UNDERSTOOD)
    shell, lock = _session_ids(data["shell"]), _session_ids(data["logonui"])
    if shell is None or lock is None:
        return Unknown(NOT_UNDERSTOOD)
    state = "nobody" if not shell else "signed-in" if shell - lock else "locked"
    flags = [f for f in (data.get("cbs"), data.get("wu")) if isinstance(f, bool)]
    pending = True if any(flags) else False if len(flags) == 2 else None
    return Session(state, pending)


def parse_updates(out: str) -> date | Unknown:
    """The newest installed update's date from ``Get-HotFix`` (one object or a list)."""
    data = _json(out)
    days = []
    for item in data if isinstance(data, list) else [data]:
        on = item.get("on") if isinstance(item, dict) else None
        if isinstance(on, str) and _DAY_RE.fullmatch(on):
            with contextlib.suppress(ValueError):
                days.append(date.fromisoformat(on))
    return max(days) if days else Unknown(NOT_UNDERSTOOD)


def _device_name(peer: dict) -> str:
    """The name Tailscale shows for a device: its MagicDNS label, else its host name.

    Phones report the host name "localhost", so the MagicDNS label comes first. Only the
    first label is taken, never the tailnet's domain. Clipped to NAME_CHARS.
    """
    dns, host = peer.get("DNSName"), peer.get("HostName")
    name = dns.split(".", 1)[0] if isinstance(dns, str) else ""
    name = " ".join((name or (host if isinstance(host, str) else "")).split())
    return name if len(name) <= NAME_CHARS else name[: NAME_CHARS - 1] + "…"


def parse_tailscale(out: str) -> Tailscale | Unknown:
    """The PC's Tailscale state from ``tailscale status --json``.

    Takes the state word, whether this PC is online, key expiry dates and device names.
    The account logins and the sign-in link that the same output carries are never read.
    """
    data = _json(out)
    if not isinstance(data, dict):
        return Unknown(NOT_UNDERSTOOD)
    backend = _word(data.get("BackendState"))
    me = data.get("Self")
    if not backend or not isinstance(me, dict | None):
        return Unknown(NOT_UNDERSTOOD)
    me = me or {}
    owner = me.get("UserID")
    others = []
    peers = data.get("Peer")
    for peer in peers.values() if isinstance(peers, dict) else ():
        if not isinstance(peer, dict) or owner is None or peer.get("UserID") != owner:
            continue
        name, expiry = _device_name(peer), _epoch(peer.get("KeyExpiry"))
        if name and expiry is not None:
            others.append(Device(name, expiry))
    return Tailscale(
        backend,
        me.get("Online") is True,
        _epoch(me.get("KeyExpiry")),
        me.get("Expired") is True,
        tuple(sorted(others, key=lambda d: (d.key_expiry, d.name))),
    )


def parse_uptime(out: str) -> float | Unknown:
    """Seconds up, from ``/proc/uptime``."""
    try:
        up = float(out.split()[0])
    except (ValueError, IndexError):
        return Unknown(NOT_UNDERSTOOD)
    return up if 0 <= up < 1e10 else Unknown(NOT_UNDERSTOOD)


def _stamp(value: str) -> float | None:
    m = _STAMP_RE.fullmatch(value.strip())
    return float(m[1]) if m and int(m[1]) > 0 else None


def _int(value: str) -> int | None:
    return int(value) if re.fullmatch(r"\d{1,9}", value.strip()) else None


def parse_units(out: str) -> Units | Unknown:
    """``systemctl show`` blocks (``Key=Value`` lines, one block per unit)."""
    found: dict[str, Unit] = {}
    for block in re.split(r"\n\s*\n", out):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not kv.get("Id") or not _word(kv.get("LoadState")):
            continue
        found[kv["Id"].strip()] = Unit(
            load=_word(kv.get("LoadState")),
            active=_word(kv.get("ActiveState")),
            result=_word(kv.get("Result")),
            main_pid=_int(kv.get("MainPID", "")) or 0,
            since=_stamp(kv.get("ActiveEnterTimestamp", "")),
            last_trigger=_stamp(kv.get("LastTriggerUSec", "")),
            exit_status=_int(kv.get("ExecMainStatus", "")),
            exited_at=_stamp(kv.get("ExecMainExitTimestamp", "")),
        )
    if not found:
        return Unknown(NOT_UNDERSTOOD)
    return Units(found.get(DESK_UNIT), found.get(FLEET_TIMER), found.get(FLEET_SERVICE))


def parse_claude_expiry(text: str) -> float | Unknown:
    """The Claude sign-in's expiry date and nothing else from the credentials file."""
    data = _json(text)
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    value = oauth.get("refreshTokenExpiresAt") if isinstance(oauth, dict) else None
    del data, oauth  # the rest of the file is credentials: keep none of it
    if isinstance(value, bool) or not isinstance(value, int | float):
        return Unknown("no sign-in expiry date was found")
    at = value / 1000 if value > 1e11 else float(value)  # stored in milliseconds
    return at if _Y2K < at < 1e10 else Unknown("no sign-in expiry date was found")


# ---- probes: each one bounded, read only, never raising ------------------------------------


async def run(argv: Sequence[str], timeout_s: float | None = None) -> str | Unknown:
    """One read-only command's output, or why there is none. Killed when it overruns.

    The command gets its own process group, and the whole group is killed: a helper it
    started must not outlive it holding the output pipe open.
    """
    timeout_s = PROBE_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return Unknown(NOT_STARTED)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        return Unknown(TIMED_OUT)
    finally:
        if proc.returncode is None:  # overran, or this task was cancelled
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):  # reap it and see the pipe close
                await asyncio.wait_for(proc.communicate(), 1.0)
    if proc.returncode != 0:
        return Unknown(FAILED)
    return out.decode("utf-8", "replace")


async def _ask[T](
    binary: Path | None, missing: str, args: Sequence[str], parse: Callable[[str], T | Unknown]
) -> T | Unknown:
    if binary is None:
        return Unknown(missing)
    out = await run([str(binary), *args])
    return out if isinstance(out, Unknown) else parse(out)


def _powershell[T](
    binary: Path | None, command: str, parse: Callable[[str], T | Unknown]
) -> Awaitable[T | Unknown]:
    args = ("-NoProfile", "-NonInteractive", "-Command", command)
    return _ask(binary, "PowerShell was not found", args, parse)


def _read_small(path: Path, limit: int = MAX_CREDENTIALS_BYTES) -> str | Unknown:
    try:
        if path.stat().st_size > limit:
            return Unknown(NOT_UNDERSTOOD)
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return Unknown("its record was not found")
    except OSError:
        return Unknown("its record could not be read")


def read_uptime(path: Path) -> float | Unknown:
    text = _read_small(path, 4096)
    return text if isinstance(text, Unknown) else parse_uptime(text)


def read_alarm(env_file: Path, state_dir: Path) -> Alarm | Unknown:
    """Whether the gone-dark alarm's config exists (never its contents) and its last tick."""
    try:
        on = env_file.is_file()
    except OSError:
        return Unknown(FAILED)
    return Alarm(on, gonedark.read_last(state_dir), gonedark.read_interval(state_dir))


def read_claude_expiry(path: Path | None) -> float | Unknown:
    """Only the expiry date is kept; the file's contents never leave this function."""
    if path is None:
        return Unknown(SWITCHED_OFF)
    text = _read_small(path)
    return text if isinstance(text, Unknown) else parse_claude_expiry(text)


async def probe_net(
    target: tuple[str, int] | None, timeout_s: float | None = None
) -> Net | Unknown:
    """One outbound TCP connect, closed at once. Nothing is sent."""
    if target is None:
        return Unknown(SWITCHED_OFF)
    timeout_s = NET_TIMEOUT_S if timeout_s is None else timeout_s
    host, port = target
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
    except TimeoutError:
        return Net(False, host, why="No answer in time")
    except OSError:  # no route, refused, or the name did not resolve
        return Net(False, host, why="No connection")
    ms = round((time.monotonic() - started) * 1000)
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), 1.0)
    return Net(True, host, ms)


class MachineMonitor:
    """Stale-while-revalidate over one round of probes.

    ``peek`` answers from memory at once; when the readings are older than the TTL it
    starts a round behind the answer. Nothing runs while nobody looks. ``on_change`` is
    called after each round so an open page can pick the new readings up.
    """

    def __init__(
        self,
        settings: GatewaySettings,
        status: StatusCache,
        watchdog_view: Callable[[], View],
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        proc_uptime: Path = Path("/proc/uptime"),
    ) -> None:
        self._settings = settings
        self._status = status
        self._watchdog_view = watchdog_view
        self._on_change = on_change
        self._clock = clock
        self._proc_uptime = proc_uptime
        self._last: Readings | None = None
        self._task: asyncio.Task[Readings] | None = None
        self.started_at = clock()  # the desk process's own start, near enough

    def peek(self) -> Readings | None:
        """The last readings (None before the first round lands); never waits."""
        last = self._last
        stale = last is None or self._clock() - last.at > self._settings.machine_ttl_s
        if stale and (self._task is None or self._task.done()):
            with contextlib.suppress(RuntimeError):  # no running loop: caller is not async
                self._task = asyncio.get_running_loop().create_task(self._round())
        return last

    async def fresh(self) -> Readings:
        """The readings of a round that finishes after this call (joins one in flight)."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._round())
        return await asyncio.shield(self._task)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _keep(self, old: Reading | None) -> bool:
        """A slow fact (boot time, installed updates) that is still fresh enough to reuse."""
        return (
            old is not None
            and not isinstance(old.value, Unknown)
            and self._clock() - old.at < SLOW_TTL_S
        )

    async def _firstmate(self) -> Firstmate | Unknown:
        """Firstmate's liveness (the header chip's own read), last turn and watchdog switch."""

        def more(pid: int) -> tuple[float | None, View]:
            return last_activity(self._settings.fm_home, pid), self._watchdog_view()

        try:
            status = await asyncio.wait_for(self._status.fresh(), PROBE_TIMEOUT_S)
            if status.state == "unknown":
                return Unknown("its records could not be read")
            pid = status.pids[0] if status.pids else 0
            idle, view = await asyncio.wait_for(asyncio.to_thread(more, pid), PROBE_TIMEOUT_S)
        except TimeoutError:
            return Unknown(TIMED_OUT)
        except Exception:  # an unreadable record is unknown, never an error page
            return Unknown("its records could not be read")
        return Firstmate(status.state, idle, view.label, bool(view.config_error or view.halted))

    async def _round(self) -> Readings:
        s, last = self._settings, self._last
        probes: dict[str, Awaitable[object]] = {
            "session": _powershell(s.powershell, PS_SESSION, parse_session),
            "tailscale": _ask(
                s.tailscale,
                "Tailscale's command was not found",
                ("status", "--json"),
                parse_tailscale,
            ),
            "wsl_up_s": asyncio.to_thread(read_uptime, self._proc_uptime),
            "units": _ask(
                s.systemctl,
                "the systemctl command was not found",
                (
                    "--user",
                    "show",
                    DESK_UNIT,
                    FLEET_TIMER,
                    FLEET_SERVICE,
                    "--timestamp=unix",
                    *(f"--property={p}" for p in UNIT_PROPS),
                ),
                parse_units,
            ),
            "firstmate": self._firstmate(),
            "claude_expiry": asyncio.to_thread(read_claude_expiry, s.claude_credentials),
            "net": probe_net(s.net_check),
        }
        if s.gonedark_env is not None and s.gonedark_state is not None:
            probes["gonedark"] = asyncio.to_thread(read_alarm, s.gonedark_env, s.gonedark_state)
        kept: dict[str, Reading] = {}
        for name, command, parse in (
            ("boot", PS_BOOT, parse_boot),
            ("updated_on", PS_UPDATES, parse_updates),
        ):
            old = getattr(last, name) if last is not None else None
            if self._keep(old):
                kept[name] = old
            else:
                probes[name] = _powershell(s.powershell, command, parse)
        results = await asyncio.gather(*probes.values(), return_exceptions=True)
        now = self._clock()
        read = {
            name: Reading(Unknown(FAILED) if isinstance(value, BaseException) else value, now)
            for name, value in zip(probes, results, strict=True)
        }
        self._last = Readings(at=now, **kept, **read)
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change()
        return self._last


# ---- the view: readings to rows and risks --------------------------------------------------


@dataclass(frozen=True)
class Moment:
    """A point in time shown beside a row: "since", "expires", "last run"."""

    label: str
    at: float
    exact: bool = False  # True: shown as a full date; False: as "5 min ago" when recent

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", redact(self.label))


class _Redacted:
    """A frozen view row: every text field is redacted before it can be shown."""

    def __post_init__(self) -> None:
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if isinstance(value, str):
                object.__setattr__(self, f.name, redact(value))


@dataclass(frozen=True)
class Row(_Redacted):
    key: str
    label: str
    state: str  # ok, warn, down or unknown
    headline: str
    detail: str = ""
    checked_at: float | None = None
    moment: Moment | None = None
    urgent: bool = False  # the red band: an expiry inside URGENT_DAYS


@dataclass(frozen=True)
class Risk(_Redacted):
    label: str
    state: str
    due: str  # "now", "in 10 days", "no date"
    detail: str = ""
    moment: Moment | None = None
    urgent: bool = False
    order: float = float("inf")  # nearest first; undated last


@dataclass(frozen=True)
class MachineView:
    rows: tuple[Row, ...]
    risks: tuple[Risk, ...]
    checked_at: float | None  # the latest round; None: none yet
    note: str = ""  # why there are no live readings ("Not checked yet", "Checking")
    alarm: Row | None = None  # the gone-dark alarm's one line; None: not shown


CHAIN = (
    ("power", "PC power"),
    ("session", "Windows session"),
    ("tailscale", "Tailscale"),
    ("wsl", "WSL"),
    ("desk", "Desk"),
    ("fleet", "Fleet-up timer"),
    ("firstmate", "Firstmate"),
    ("claude", "Claude sign-in"),
    ("internet", "Internet"),
)
LABELS = dict(CHAIN)


def span(seconds: float) -> str:
    """A duration in two units at most: "20 d 21 h", "3 h 12 min", "12 min"."""
    s = max(0, int(seconds))
    if s >= DAY_S:
        return f"{s // 86400} d {s % 86400 // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 60} min" if s >= 60 else "under a minute"


def left(seconds: float) -> str:
    """Time to go, rounded down: "10 days", "1 day", "5 h", "under an hour"."""
    s = max(0, int(seconds))
    if s >= 2 * DAY_S:
        return f"{s // 86400} days"
    if s >= DAY_S:
        return "1 day"
    return f"{s // 3600} h" if s >= 3600 else "under an hour"


def _sentence(why: str) -> str:
    return why[:1].upper() + why[1:]


def _unknown(key: str, reading: Reading) -> Row:
    why = reading.value.why if isinstance(reading.value, Unknown) else NOT_UNDERSTOOD
    return Row(key, LABELS[key], UNKNOWN, "Unknown", _sentence(why), reading.at)


def _expiry_state(expiry: float, now: float) -> tuple[str, bool]:
    """ok, warning inside WARN_DAYS (urgent inside URGENT_DAYS), down once past."""
    to_go = expiry - now
    if to_go <= 0:
        return DOWN, True
    if to_go <= WARN_DAYS * DAY_S:
        return WARN, to_go <= URGENT_DAYS * DAY_S
    return OK, False


def _power_row(r: Readings, now: float) -> Row:
    boot = r.boot.value
    if isinstance(boot, Unknown):
        return _unknown("power", r.boot)
    return Row(
        "power",
        LABELS["power"],
        OK,
        f"On for {span(now - boot)}",
        "Windows uptime",
        r.boot.at,
        Moment("started", boot),
    )


_SESSION = {
    "signed-in": (OK, "Signed in", "Desktop is unlocked"),
    "locked": (OK, "Locked", "Signed in, screen locked"),
    "nobody": (WARN, "Nobody signed in", "Sign in at the PC: its sign-in programs are not running"),
}


def _session_row(r: Readings) -> Row:
    s = r.session.value
    if isinstance(s, Unknown) or s.state not in _SESSION:
        return _unknown("session", r.session)
    state, headline, detail = _SESSION[s.state]
    return Row("session", LABELS["session"], state, headline, detail, r.session.at)


_BACKEND = {
    "NeedsLogin": (DOWN, "Logged out", "Log in to Tailscale at the PC"),
    "Stopped": (DOWN, "Turned off", "Turn Tailscale on at the PC"),
    "NeedsMachineAuth": (WARN, "Waiting for approval", "Approve this PC in Tailscale's admin page"),
    "Starting": (WARN, "Starting", "Tailscale is still coming up"),
    "NoState": (WARN, "Starting", "Tailscale is still coming up"),
}


def tailscale_row(reading: Reading[Tailscale | Unknown], now: float) -> Row:
    """This PC's Tailscale link and key, as the Machine and Sign-ins pages both show it."""
    t = reading.value
    if isinstance(t, Unknown):
        return _unknown("tailscale", reading)
    key, label, at = "tailscale", LABELS["tailscale"], reading.at
    expires = Moment("key expires", t.key_expiry, exact=True) if t.key_expiry else None
    if t.key_expired:
        return Row(key, label, DOWN, "Key expired", "Log in to Tailscale at the PC", at, expires)
    if t.backend in _BACKEND:
        state, headline, detail = _BACKEND[t.backend]
        return Row(key, label, state, headline, detail, at)
    if t.backend != "Running":
        return Row(key, label, UNKNOWN, "Unknown", "Tailscale reported a state not known here", at)
    if not t.online:
        detail = "No link to Tailscale's servers"
        return Row(key, label, WARN, "Running, offline", detail, at, expires)
    if t.key_expiry is None:
        return Row(key, label, OK, "Connected", "Key does not expire", at)
    state, urgent = _expiry_state(t.key_expiry, now)
    if state == DOWN:
        return Row(key, label, DOWN, "Key expired", "Log in to Tailscale at the PC", at, expires)
    detail = f"Key good for {left(t.key_expiry - now)}"
    return Row(key, label, state, "Connected", detail, at, expires, urgent)


def _wsl_row(r: Readings) -> Row:
    up = r.wsl_up_s.value
    if isinstance(up, Unknown):
        return _unknown("wsl", r.wsl_up_s)
    at, since = r.wsl_up_s.at, Moment("started", r.wsl_up_s.at - up)
    return Row("wsl", LABELS["wsl"], OK, f"Up {span(up)}", "Linux side of the PC", at, since)


def _desk_row(r: Readings, now: float, started_at: float, pid: int) -> Row:
    """The desk is up by definition (it rendered this). The open question is its service."""
    key, label, at = "desk", LABELS["desk"], r.units.at
    headline = f"Up {span(now - started_at)}"
    units = r.units.value
    if isinstance(units, Unknown):
        return Row(key, label, OK, headline, f"Service state unknown: {units.why}", at)
    unit = units.desk
    if unit is None or unit.load != "loaded":
        detail = "Not installed as a service: it will not start by itself"
        return Row(key, label, WARN, headline, detail, at)
    if unit.active != "active":
        detail = f"Started by hand; its service is {unit.active or 'in an unknown state'}"
        return Row(key, label, WARN, headline, detail, at)
    if unit.main_pid != pid:
        return Row(key, label, OK, headline, "Started by hand; its service runs separately", at)
    return Row(key, label, OK, headline, "Running as its service", at)


def _fleet_row(r: Readings, now: float) -> Row:
    units = r.units.value
    if isinstance(units, Unknown):
        return _unknown("fleet", r.units)
    key, label, at = "fleet", LABELS["fleet"], r.units.at
    timer, service = units.timer, units.service
    if timer is None or timer.load != "loaded":
        detail = "The fleet is not brought back after a restart"
        return Row(key, label, DOWN, "Not installed", detail, at)
    if timer.active != "active":
        detail = f"Timer is {timer.active or 'in an unknown state'}"
        return Row(key, label, DOWN, "Off", detail, at)
    ran = timer.last_trigger or (service.exited_at if service else None)
    last = Moment("last run", ran) if ran else None
    if service is not None and (service.result not in ("", "success") or service.exit_status):
        return Row(key, label, WARN, "Last run failed", "See its log on the PC", at, last)
    if ran is None:
        return Row(key, label, WARN, "No run yet", "Waiting for its first run", at)
    if now - ran > FLEET_OVERDUE_S:
        return Row(key, label, WARN, "Overdue", "It should run every 5 min", at, last)
    return Row(key, label, OK, "On", "Last run succeeded", at, last)


_FIRSTMATE = {
    "alive": (OK, "Alive"),
    "no-beat": (WARN, "Watcher silent"),
    "down": (DOWN, "Down"),
}


def _firstmate_row(r: Readings) -> Row:
    fm = r.firstmate.value
    if isinstance(fm, Unknown) or fm.state not in _FIRSTMATE:
        return _unknown("firstmate", r.firstmate)
    state, headline = _FIRSTMATE[fm.state]
    if state == OK and fm.watchdog_trouble:
        state = WARN
    at = r.firstmate.at
    active = Moment("last activity", at - fm.idle_s) if fm.idle_s is not None else None
    detail = f"Watchdog {fm.watchdog}"
    return Row("firstmate", LABELS["firstmate"], state, headline, detail, at, active)


def claude_row(reading: Reading[float | Unknown], now: float) -> Row:
    """The Claude sign-in and its expiry date, as the Machine and Sign-ins pages both show it."""
    expiry = reading.value
    if isinstance(expiry, Unknown):
        return _unknown("claude", reading)
    key, label, at = "claude", LABELS["claude"], reading.at
    state, urgent = _expiry_state(expiry, now)
    if state == DOWN:
        when = Moment("expired", expiry, exact=True)
        return Row(key, label, DOWN, "Expired", RENEW_CLAUDE, at, when, True)
    when = Moment("expires", expiry, exact=True)
    headline = f"Valid, {left(expiry - now)} left"
    if state == WARN:
        return Row(key, label, WARN, headline, RENEW_CLAUDE, at, when, urgent)
    return Row(key, label, OK, headline, "Sign-in is current", at, when)


def _internet_row(r: Readings) -> Row:
    net = r.net.value
    if isinstance(net, Unknown):
        return _unknown("internet", r.net)
    key, label, at = "internet", LABELS["internet"], r.net.at
    if net.ok:
        return Row(key, label, OK, "Reachable", f"Reached {net.host} in {net.ms} ms", at)
    return Row(key, label, DOWN, "Not reachable", f"{net.why} from {net.host}", at)


ALARM_LABEL = "Gone-dark alarm"
ALARM_SLACK_S = 60.0  # a tick may land this late before the sender counts as not running
_ALARM_RESULT = {
    gonedark.SENT: (OK, "On", "Last ping sent"),
    gonedark.SKIPPED: (WARN, "Ping skipped", ""),
    gonedark.FAILED: (WARN, "Last ping failed", ""),
}


def alarm_row(reading: Reading[Alarm | Unknown] | None, now: float) -> Row | None:
    """On or off, and the last ping's time and result. Nothing else about the alarm."""
    if reading is None:
        return None
    a, key, at = reading.value, "alarm", reading.at
    if isinstance(a, Unknown):
        return Row(key, ALARM_LABEL, UNKNOWN, "Unknown", _sentence(a.why), at)
    if not a.on:
        detail = "Nothing tells your phone when the PC goes dark: see docs/gone-dark.md"
        return Row(key, ALARM_LABEL, WARN, "Off", detail, at)
    if a.last is None:
        return Row(key, ALARM_LABEL, WARN, "On, no ping yet", "The sender has not run yet", at)
    last = Moment("last ping", a.last.at)
    if now - a.last.at > 2 * a.interval_s + ALARM_SLACK_S:
        detail = "No ping tried lately: the sender is not running"
        return Row(key, ALARM_LABEL, WARN, "On, not running", detail, at, last)
    state, headline, detail = _ALARM_RESULT[a.last.result]
    return Row(key, ALARM_LABEL, state, headline, detail or _sentence(a.last.reason), at, last)


def patch_tuesday(year: int, month: int) -> datetime:
    """Microsoft's monthly update release: the second Tuesday, about 18:00 UTC."""
    first = date(year, month, 1)
    return datetime(year, month, 8 + (1 - first.weekday()) % 7, 18, tzinfo=UTC)


def patch_tuesdays(now: float) -> tuple[datetime, datetime]:
    """The latest monthly release at or before ``now``, and the next one after it."""
    today = datetime.fromtimestamp(now, UTC)
    this = patch_tuesday(today.year, today.month)
    before = today.replace(day=1) - timedelta(days=1)
    after = today.replace(day=28) + timedelta(days=7)
    if this <= today:
        return this, patch_tuesday(after.year, after.month)
    return patch_tuesday(before.year, before.month), this


def _restart_risk(r: Readings, now: float) -> Risk:
    label = "Windows restart"
    released, upcoming = patch_tuesdays(now)
    session, updated_on = r.session.value, r.updated_on.value
    pending = None if isinstance(session, Unknown) else session.restart_pending
    if pending:
        detail = "Restart pending: Windows restarts by itself outside active hours"
        return Risk(label, WARN, "now", detail, order=now)
    if not isinstance(updated_on, Unknown) and updated_on < released.date():
        detail = f"{released:%B} updates are out but not installed: expect a restart within days"
        return Risk(label, WARN, "within days", detail, order=now)
    nxt = Moment("next monthly updates about", upcoming.timestamp(), exact=True)
    due = f"in {left(upcoming.timestamp() - now)}"
    if pending is None:
        detail = "Could not read whether a restart is pending"
        return Risk(label, UNKNOWN, due, detail, nxt, order=nxt.at)
    return Risk(label, OK, due, "None pending", nxt, order=nxt.at)


def _expiry_risk(label: str, expiry: float, now: float, fix: str) -> Risk:
    state, urgent = _expiry_state(expiry, now)
    if state == DOWN:
        return Risk(label, DOWN, "expired", fix, Moment("expired", expiry, True), True, expiry)
    due = f"in {left(expiry - now)}"
    detail = fix if state == WARN else ""
    return Risk(label, state, due, detail, Moment("expires", expiry, True), urgent, expiry)


def _risks(r: Readings, now: float) -> tuple[Risk, ...]:
    risks = [_restart_risk(r, now)]
    ts = r.tailscale.value
    pc_key = "Tailscale key, this PC"
    if isinstance(ts, Unknown):
        risks.append(Risk(pc_key, UNKNOWN, "no date", _sentence(ts.why)))
    elif ts.key_expiry is None:
        risks.append(Risk(pc_key, OK, "no date", "This key does not expire"))
    else:
        risks.append(_expiry_risk(pc_key, ts.key_expiry, now, EXTEND_TAILSCALE))
    claude = r.claude_expiry.value
    if isinstance(claude, Unknown):
        risks.append(Risk("Claude sign-in", UNKNOWN, "no date", _sentence(claude.why)))
    else:
        risks.append(_expiry_risk("Claude sign-in", claude, now, RENEW_CLAUDE))
    for device in () if isinstance(ts, Unknown) else ts.others:
        if device.key_expiry > now:  # an already expired device is not an upcoming risk
            fix = "Log in to Tailscale on that device"
            label = f"Tailscale key, {device.name}"
            risks.append(_expiry_risk(label, device.key_expiry, now, fix))
    return tuple(sorted(risks, key=lambda k: k.order))  # stable: undated keep their order


def build_view(
    readings: Readings | None, now: float, started_at: float, pid: int | None = None
) -> MachineView:
    """Rows and risks for the page. Missing or old readings show as unknown, never as ok."""
    if readings is None or now - readings.at > STALE_S:
        at = readings.at if readings is not None else None
        words = ("Checking", "Reading it again now") if at else ("Unknown", "Not checked yet")
        rows = tuple(Row(key, label, UNKNOWN, *words, at) for key, label in CHAIN)
        return MachineView(rows, (), at, "Checking again" if at else "Not checked yet")
    r = readings
    rows = (
        _power_row(r, now),
        _session_row(r),
        tailscale_row(r.tailscale, now),
        _wsl_row(r),
        _desk_row(r, now, started_at, os.getpid() if pid is None else pid),
        _fleet_row(r, now),
        _firstmate_row(r),
        claude_row(r.claude_expiry, now),
        _internet_row(r),
    )
    return MachineView(rows, _risks(r, now), r.at, alarm=alarm_row(r.gonedark, now))
