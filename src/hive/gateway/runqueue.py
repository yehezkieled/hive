"""The Run queue: a short, fixed list of fix buttons, each one exact, named action.

No text from a request ever reaches a command. A request names an action by its key in
``ACTIONS`` and, for two Windows actions, picks a number from a fixed list; the argument
list and the admin helper's request line are built here from those values only, and no
command goes through a shell.

**Linux** actions run as the desk's own user (no root) and only call the repo's own
units and script: ``systemctl --user`` on ``hive-gateway``, ``clip-desk``,
``hive-telegram`` and ``hive-fleet-up``, and ``scripts/fleet-up.sh --only serve``.

**Windows** actions go only through the narrow admin helper
(``deploy/windows/HiveAdminHelper.ps1``) and only use verbs from its allow-list: one
``<nonce> <verb> [<number>]`` line in its request file, then its on-demand task
(``schtasks /run``), then its audit log is read for that nonce's reply. A request the
helper never took is withdrawn, so it cannot fire later.

Every run is two lines in ``run-audit.log`` (Hive's data dir): one when it starts and
one with who, action, start, result and duration when it ends. A run the desk did not
see finish (it restarted itself, or WSL restarted under it) is settled the next time the
log is read: from the helper's audit log for a Windows run, from the desk's own start
time for a desk restart, else as unknown.

Callers must have authorised the owner and passed the step-up first; this module checks
only what to run, whether it may run now, and records what happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hive.gateway.actions import ActionError, audit
from hive.gateway.machine import DESK_UNIT, FLEET_SERVICE, redact
from hive.gateway.settings import GatewaySettings

CLIP_DESK_UNIT = "clip-desk.service"
TELEGRAM_UNIT = "hive-telegram.service"
HELPER_TASK = r"\Hive\Hive Admin Helper"
LOG_NAME = "run-audit.log"
SHOWN_RUNS = 20
COOLDOWN_S = 60.0  # the same action again only this long after it last started
WINDOW_S = 600.0  # at most WINDOW_RUNS runs started in any 10 minutes, all actions
WINDOW_RUNS = 10
POLL_S = 0.5  # how often a Windows run reads the helper's audit log
SCHTASKS_TIMEOUT_S = 15.0
GUARD_TIMEOUT_S = 5.0
MAX_OUTPUT = 64 * 1024
MAX_LOG_BYTES = 512 * 1024  # the run log is trimmed to its last KEEP_LINES lines past this
KEEP_LINES = 1000
HELPER_LOG_TAIL = 256 * 1024  # how much of the helper's audit log a settle step reads
STATE_TIMEOUT_S = 2.0

NOT_SET_UP = "The Windows admin helper is not set up yet; see the sitting steps."
SITTING = "docs/anywhere-sitting.md, step 2"

# Results, as the page names them.
OK, REFUSED, FAILED, TIMED_OUT, RUNNING, UNKNOWN = (
    "ok",
    "refused",
    "failed",
    "timed out",
    "running",
    "unknown",
)
RESULT_STATUS = {REFUSED: 409, FAILED: 502, TIMED_OUT: 504, UNKNOWN: 502}

DAYS = (1, 3, 7, 14, 21, 35)
DEFAULT_DAYS = 7

_PC_DOWN = (
    "The PC restarts in 30 seconds. Everything goes down: Windows, Tailscale, WSL, this desk, "
    "firstmate and every worker; unsaved work in them is lost. The PC comes back by itself: "
    "after the one sitting at the PC it signs in and locks, then Tailscale, WSL, the desk and "
    "firstmate start again, usually within 5 to 12 minutes. If the desk is not back after 15 "
    "minutes, someone has to go to the PC. Once you confirm, the desk cannot stop it."
)


@dataclass(frozen=True)
class Action:
    """One fix button. ``argv`` (Linux) or ``verb`` (Windows) is all it can ever run."""

    key: str
    side: str  # "linux" or "windows"
    label: str
    plain: str  # what it does, in plain words
    took: str  # how long it usually takes
    timeout_s: float  # the hard time-out: past it the desk stops the run and says so
    argv: tuple[str, ...] = ()  # Linux: "systemctl" / "fleet-up" stand for their paths
    verb: str = ""  # Windows: the admin helper verb
    choice: str = ""  # "" or "days": the one number it takes, from a fixed list
    down: str = ""  # set: an extra confirm stating what goes down and how it comes back
    reload_s: int = 0  # the page reloads itself this long after (the desk restarted)
    check_unit: str = ""  # Linux: after the command, this unit must be active

    @property
    def group(self) -> str:
        """One run at a time per group: each Linux action alone, all Windows ones together
        (the helper has a single request file)."""
        return "windows" if self.side == "windows" else self.key

    @property
    def choices(self) -> tuple[int, ...]:
        return DAYS if self.choice == "days" else ()

    @property
    def default(self) -> int | None:
        return DEFAULT_DAYS if self.choice == "days" else None

    def command(self, n: int | None = None) -> str:
        """The exact command or helper verb, as the confirm step shows it."""
        if self.side == "windows":
            arg = f" {n}" if n is not None else (f" <{self.choice}>" if self.choice else "")
            return f"admin helper verb: {self.verb}{arg}"
        shown = {"systemctl": "systemctl", "fleet-up": "scripts/fleet-up.sh"}
        return " ".join(shown.get(a, a) for a in self.argv)

    def short(self, n: int | None) -> str:
        """The chosen number in a few characters: "7 days"."""
        if n is None:
            return ""
        return f"{n} day{'s' if n != 1 else ''}"

    def when(self, n: int | None) -> str:
        if self.choice == "days" and n is not None:
            return f"for {n} day{'s' if n != 1 else ''} from now"
        return ""


ACTIONS: dict[str, Action] = {
    a.key: a
    for a in (
        Action(
            "restart-desk",
            "linux",
            "Restart the desk",
            "Restarts this desk's own service. The page goes blank for a few seconds and "
            "reloads itself. Firstmate and the workers are not touched.",
            "about 10 s",
            20.0,
            argv=("systemctl", "--user", "restart", "--no-block", DESK_UNIT),
            reload_s=8,
        ),
        Action(
            "restart-clipdesk",
            "linux",
            "Restart the clip desk",
            "Restarts the clip desk's service. Its page is gone for a few seconds.",
            "about 10 s",
            45.0,
            argv=("systemctl", "--user", "restart", CLIP_DESK_UNIT),
            check_unit=CLIP_DESK_UNIT,
        ),
        Action(
            "restart-telegram",
            "linux",
            "Restart the Telegram bot",
            "Restarts the Telegram bot's service. It stays off while the bot is not set up.",
            "about 10 s",
            45.0,
            argv=("systemctl", "--user", "restart", TELEGRAM_UNIT),
            check_unit=TELEGRAM_UNIT,
        ),
        Action(
            "fleet-up",
            "linux",
            "Re-run fleet-up",
            "Runs fleet-up now instead of waiting for its 5-minute timer. It starts whatever "
            "is down (the desk, the clip desk, the Telegram bot, broke-no-more, the Lavish "
            "bridge, the preview server, the serve maps), then firstmate if no session runs. "
            "It never starts a second firstmate.",
            "under a minute, at most 4 min",
            240.0,
            argv=("systemctl", "--user", "start", FLEET_SERVICE),
        ),
        Action(
            "serve-maps",
            "linux",
            "Repair the serve maps",
            "Sets the tailnet-only links again (tailscale serve, never funnel), from "
            "fleet-up's list of ports. Pages may drop for a second.",
            "about 10 s",
            60.0,
            argv=("fleet-up", "--only", "serve"),
        ),
        Action(
            "helper-check",
            "windows",
            "Check the admin helper",
            "Asks the Windows admin helper to answer. It changes nothing on the PC. Use it "
            "after the sitting, or after a Windows button timed out.",
            "about 5 s",
            30.0,
            verb="ping",
        ),
        Action(
            "restart-wsl",
            "windows",
            "Restart WSL",
            "Restarts WSL on the PC, so all of Linux stops and starts again.",
            "1 to 3 min until the desk is back",
            60.0,
            verb="restart-wsl",
            down=(
                "All of Linux goes down: this desk, firstmate and every worker; work in their "
                "sessions may be lost. Windows and Tailscale stay up. WSL comes back by "
                "itself: the keep-alive task starts it again and the desk returns in about 1 "
                "to 3 minutes. If it does not, someone has to open Ubuntu at the PC."
            ),
        ),
        Action(
            "restart-tailscale",
            "windows",
            "Restart Tailscale",
            "Restarts the PC's Tailscale service. The desk drops off the tailnet for a few "
            "seconds; reload the page if it stops answering.",
            "about 15 s",
            60.0,
            verb="restart-tailscale",
        ),
        Action(
            "tailscale-up",
            "windows",
            "Tailscale up",
            "Runs tailscale up --unattended on the PC to reconnect it. If Tailscale needs a "
            "new sign-in this cannot finish it; that is done at the PC.",
            "up to 60 s",
            90.0,
            verb="tailscale-up",
        ),
        Action(
            "restart-pc",
            "windows",
            "Restart the PC now",
            "Restarts the whole PC in 30 seconds.",
            "5 to 12 min until the desk is back",
            30.0,
            verb="restart-pc",
            down=_PC_DOWN,
        ),
        Action(
            "pause-updates",
            "windows",
            "Pause Windows updates",
            "Pauses Windows Update for the chosen number of days from now, so it does not "
            "restart the PC by itself. Running it again starts a new pause from now.",
            "about 5 s",
            30.0,
            verb="pause-updates",
            choice="days",
        ),
    )
}

# Shown as notes, never as buttons. Both wait on a cancel verb in the admin helper.
NOT_OFFERED = (
    (
        "Cancel a scheduled restart",
        "The admin helper has no cancel verb, so the desk cannot cancel a restart. At the "
        "PC, run shutdown /a.",
    ),
    (
        "Restart the PC at a set hour",
        "Left out on purpose: a restart set for later must be cancellable, and the admin "
        "helper has no cancel verb. It comes once the helper has one.",
    ),
)


def parse(form: dict[str, str]) -> tuple[Action, int | None]:
    """The action and number a request names, or ``ActionError``. Only exact list values."""
    action = ACTIONS.get(form.get("do", ""))
    if action is None:
        raise ActionError("That is not one of the Run page's actions.")
    raw = form.get("n", "")
    if not action.choice:
        if raw:
            raise ActionError(f"{action.label} takes no number.")
        return action, None
    allowed = {str(v): v for v in action.choices}
    if raw not in allowed:
        raise ActionError(f"Pick {action.choice} from the list.")
    return action, allowed[raw]


def request_line(nonce: str, verb: str, n: int | None) -> bytes:
    """The one line the helper reads: ``<nonce> <verb> [<number>]``, ASCII. Only a verb a
    button sends, with a number that button offers (none for a button that takes none)."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}", nonce):
        raise ValueError("bad nonce")
    offered = [a for a in ACTIONS.values() if a.verb and a.verb == verb]
    if not offered:
        raise ValueError("unknown verb")
    if not any(n in (a.choices or (None,)) for a in offered):
        raise ValueError("number not offered for that verb")
    text = f"{nonce} {verb}" + (f" {int(n)}" if n is not None else "")
    return (text + "\n").encode("ascii")


def new_nonce() -> str:
    return f"hive-{secrets.token_hex(8)}"


# ---- running commands ---------------------------------------------------------------------


@dataclass(frozen=True)
class Done:
    code: int
    output: str
    timed_out: bool = False


# Runs an argument list with a hard time-out; replaced by a fake in tests.
Exec = Callable[[Sequence[str], float], Awaitable[Done]]


async def run_argv(argv: Sequence[str], timeout_s: float) -> Done:
    """Run one argument list (never a shell) in its own process group, killed at the
    time-out. Output goes to a file, not a pipe, so a child it leaves behind cannot hold
    the wait open."""
    with tempfile.TemporaryFile() as out:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=out,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            return Done(127, f"could not start {Path(argv[0]).name}: {exc}")
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            timed_out = True
        finally:
            if proc.returncode is None:  # overran, or this task was cancelled
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 2.0)
        out.seek(0)
        text = out.read(MAX_OUTPUT).decode("utf-8", "replace")
    return Done(proc.returncode if proc.returncode is not None else -9, text, timed_out)


def _first(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return redact(line, 200)
    return ""


# ---- the run log --------------------------------------------------------------------------


@dataclass
class Run:
    id: str
    action: str
    who: str
    start: float
    n: int | None = None
    nonce: str = ""
    result: str = RUNNING
    duration_s: float | None = None
    detail: str = ""
    silent: bool = False  # Windows: the helper did not answer at all

    @property
    def label(self) -> str:
        a = ACTIONS.get(self.action)
        if a is None:
            return self.action
        return f"{a.label} · {a.short(self.n)}" if self.n is not None else a.label


class RunLog:
    """``run-audit.log``: JSON lines, mode 0600, Hive's own file. No secrets go in it: the
    owner login, an action key, a number, times, a result and redacted plain words."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line)
            if self.path.stat().st_size > MAX_LOG_BYTES:
                self._trim()
        except OSError:
            pass  # an unwritable log must not turn a run into a crash; the journal has it too

    def _trim(self) -> None:
        lines = self.path.read_text().splitlines(keepends=True)[-KEEP_LINES:]
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.writelines(lines)
        os.replace(tmp, self.path)

    def runs(self) -> list[Run]:
        """Every run in the log, oldest first; a later line for the same id updates it."""
        try:
            text = self.path.read_text()
        except OSError:
            return []
        by_id: dict[str, Run] = {}
        for line in text.splitlines():
            try:
                rec = json.loads(line)
                rid = str(rec["id"])
                if rid not in by_id:
                    by_id[rid] = Run(
                        rid, str(rec["action"]), str(rec.get("who", "")), float(rec["start"])
                    )
                run = by_id[rid]
                n = rec.get("n")
                run.n = n if isinstance(n, int) else None
                run.nonce = str(rec.get("nonce", run.nonce))
                run.result = str(rec.get("result", run.result))
                d = rec.get("duration_s")
                run.duration_s = float(d) if isinstance(d, int | float) else run.duration_s
                run.detail = str(rec.get("detail", run.detail))
                run.silent = bool(rec.get("silent", run.silent))
            except (ValueError, KeyError, TypeError):
                continue
        return sorted(by_id.values(), key=lambda r: r.start)


def _record(run: Run) -> dict:
    rec: dict[str, object] = {
        "id": run.id,
        "who": run.who,
        "action": run.action,
        "start": round(run.start, 3),
        "result": run.result,
    }
    if run.n is not None:
        rec["n"] = run.n
    if run.nonce:
        rec["nonce"] = run.nonce
    if run.duration_s is not None:
        rec["duration_s"] = round(run.duration_s, 2)
    if run.detail:
        rec["detail"] = run.detail
    if run.silent:
        rec["silent"] = True
    return rec


# ---- the Windows admin helper ---------------------------------------------------------------

MISSING, UNPROVEN, READY = "missing", "unproven", "ready"
_AUDIT_RE = re.compile(
    r"^(\S+) nonce=(\S+) verb=(\S+) arg=(\S+) result=(\S+) detail=(.*)$"
)  # deploy/windows/HiveAdminHelper.ps1, Write-HiveAdminAudit


@dataclass(frozen=True)
class Reply:
    result: str  # OK, REFUSED, FAILED, TIMED_OUT
    detail: str  # plain words, external text already redacted
    silent: bool = False  # the helper never answered


def _helper_says(result: str, detail: str) -> Reply | None:
    """A final line of the helper's audit log, in plain words; None for ``started``."""
    said = redact(detail.strip().removeprefix("rejected: "), 200)
    if result == "ok":
        return Reply(OK, f"The admin helper did it: {said or 'ok'}.")
    if result == "failed":
        return Reply(FAILED, f"The admin helper tried and failed: {said or 'no detail'}.")
    if result in ("rejected", "ignored"):
        return Reply(
            REFUSED, f"The admin helper refused the request: {said or result}. Nothing ran."
        )
    return None


class AdminHelper:
    """The desk's side of the helper's request-file protocol (deploy/windows/README.md)."""

    def __init__(self, root: Path | None, schtasks: Path | None, run: Exec = run_argv) -> None:
        self.root = root
        self.schtasks = schtasks
        self._run = run

    @property
    def request_dir(self) -> Path | None:
        return self.root / "request" if self.root else None

    @property
    def request_file(self) -> Path | None:
        return self.root / "request" / "request.txt" if self.root else None

    @property
    def audit_log(self) -> Path | None:
        return self.root / "audit.log" if self.root else None

    def state(self) -> str:
        """``missing`` (not installed, or not this host), ``unproven`` (installed but it has
        never written its log) or ``ready``. File checks only; nothing is started."""
        if self.root is None or self.schtasks is None:
            return MISSING
        try:
            if not self.request_dir.is_dir() or not self.schtasks.is_file():  # type: ignore[union-attr]
                return MISSING
            return READY if self.audit_log.is_file() else UNPROVEN  # type: ignore[union-attr]
        except OSError:
            return MISSING

    def _size(self) -> int:
        try:
            return self.audit_log.stat().st_size  # type: ignore[union-attr]
        except OSError:
            return 0

    def _lines_from(self, offset: int) -> tuple[list[str], int]:
        """New audit lines since ``offset``; a rotated (shorter) log is read from its start."""
        path = self.audit_log
        try:
            with open(path, "rb") as fh:  # type: ignore[arg-type]
                size = os.fstat(fh.fileno()).st_size
                if size < offset:
                    offset = 0
                fh.seek(offset)
                data = fh.read(HELPER_LOG_TAIL)
        except OSError:
            return [], offset
        end = data.rfind(b"\n")
        if end < 0:
            return [], offset
        text = data[: end + 1].decode("ascii", "replace")
        return text.splitlines(), offset + end + 1

    def _write_request(self, line: bytes) -> None:
        fd = os.open(
            self.request_file,  # type: ignore[arg-type]
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as fh:
            fh.write(line)

    def withdraw(self, nonce: str) -> bool:
        """Remove the request file if it still holds this nonce's line. True when removed."""
        path = self.request_file
        if path is None:
            return False
        try:
            if path.is_symlink():
                return False
            with open(path, "rb") as fh:
                head = fh.read(256)
            if head.split(b" ", 1)[0] != nonce.encode():
                return False
            path.unlink()
            return True
        except OSError:
            return False

    def find(self, nonce: str) -> Reply | None:
        """The helper's final reply for ``nonce`` in its audit log (and the rotated one)."""
        path = self.audit_log
        if path is None:
            return None
        found: Reply | None = None
        for p in (path.with_name(path.name + ".1"), path):
            try:
                with open(p, "rb") as fh:
                    fh.seek(max(0, os.fstat(fh.fileno()).st_size - HELPER_LOG_TAIL))
                    text = fh.read().decode("ascii", "replace")
            except OSError:
                continue
            for line in text.splitlines():
                m = _AUDIT_RE.match(line.strip())
                if m and m[2] == nonce:
                    found = _helper_says(m[5], m[6]) or found
        return found

    async def ask(self, verb: str, n: int | None, nonce: str, timeout_s: float) -> Reply:
        """One request: write the line, start the task, wait for this nonce's reply."""
        deadline = time.monotonic() + timeout_s
        if self.root is None or self.schtasks is None or self.request_file is None:
            return Reply(REFUSED, f"{NOT_SET_UP} ({SITTING})", silent=True)
        line = request_line(nonce, verb, n)
        offset = await asyncio.to_thread(self._size)
        try:
            await asyncio.to_thread(self._write_request, line)
        except OSError:
            return Reply(REFUSED, f"{NOT_SET_UP} The request could not be written.", silent=True)
        started = await self._run(
            [str(self.schtasks), "/run", "/tn", HELPER_TASK],
            max(1.0, min(SCHTASKS_TIMEOUT_S, deadline - time.monotonic())),
        )
        if started.code != 0 or started.timed_out:
            withdrawn = await asyncio.to_thread(self.withdraw, nonce)
            said = started.output.lower()
            if "access is denied" in said:
                why = (
                    "Windows did not let the desk start the admin helper (access denied). "
                    f"Nothing ran. The sitting checks this ({SITTING})."
                )
            elif "cannot find" in said or "does not exist" in said:
                why = f"{NOT_SET_UP} ({SITTING}) Windows has no admin helper task."
            elif started.timed_out:
                why = "Windows did not start the admin helper's task in time. Nothing ran."
            else:
                why = f"Starting the admin helper failed: {_first(started.output) or 'no detail'}."
            if not withdrawn:
                why += " The request was already taken."
            return Reply(REFUSED, why, silent=True)
        seen_start = False
        rejected: str | None = None
        while True:
            lines, offset = await asyncio.to_thread(self._lines_from, offset)
            for raw in lines:
                m = _AUDIT_RE.match(raw.strip())
                if not m:
                    continue
                if m[2] == nonce:
                    if m[5] == "started":
                        seen_start = True
                        continue
                    reply = _helper_says(m[5], m[6])
                    if reply is not None:
                        return reply
                elif m[2] == "-" and m[5] == "rejected":
                    rejected = m[6]  # a request it could not read carries no nonce
            if rejected is not None and not await asyncio.to_thread(self._still_there, nonce):
                reply = _helper_says("rejected", rejected)
                if reply is not None:
                    return reply
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(POLL_S)
        if seen_start:
            return Reply(
                TIMED_OUT,
                f"The admin helper started but did not finish within {int(timeout_s)} s. It may "
                "still be working; its result shows here once the page reads its log again.",
            )
        withdrawn = await asyncio.to_thread(self.withdraw, nonce)
        tail = (
            " The request was withdrawn, so it cannot run later."
            if withdrawn
            else " Its request was taken, so it may still run."
        )
        return Reply(
            TIMED_OUT,
            f"The Windows admin helper did not answer within {int(timeout_s)} s. {NOT_SET_UP}"
            f" ({SITTING}){tail}",
            silent=withdrawn,
        )

    def _still_there(self, nonce: str) -> bool:
        path = self.request_file
        try:
            with open(path, "rb") as fh:  # type: ignore[arg-type]
                return fh.read(256).split(b" ", 1)[0] == nonce.encode()
        except OSError:
            return False


# ---- the queue ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finished:
    result: str
    message: str
    reload_s: int = 0


@dataclass(frozen=True)
class Card:
    action: Action
    why_not: str  # "" when the button is offered; else why it is not


@dataclass(frozen=True)
class RunView:
    cards: tuple[Card, ...]
    helper: str  # MISSING, UNPROVEN or READY
    runs: tuple[Run, ...]  # newest first, at most SHOWN_RUNS
    owner: str = ""
    not_offered: tuple[tuple[str, str], ...] = field(default=NOT_OFFERED)


class RunQueue:
    """Runs the fixed actions: rate limit, one at a time per group, hard time-out, audit."""

    def __init__(
        self,
        settings: GatewaySettings,
        run: Exec = run_argv,
        helper: AdminHelper | None = None,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[], None] | None = None,
        pid: int | None = None,
    ) -> None:
        self.settings = settings
        self._run = run
        self.helper = helper or AdminHelper(settings.admin_helper_root, settings.schtasks, run=run)
        self._clock = clock
        self._on_change = on_change
        self._pid = os.getpid() if pid is None else pid
        self.log = RunLog(settings.data_dir / LOG_NAME)
        self.booted_at = clock()  # a run that started before this never finishes here
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: set[str] = set()
        self._settle = asyncio.Lock()

    # ---- what is offered --------------------------------------------------------------

    def _binary(self, name: str) -> Path | None:
        return self.settings.systemctl if name == "systemctl" else self.settings.fleet_up

    def why_not(self, action: Action, helper_state: str) -> str:
        """Why a button is not offered, in a few plain words; "" when it is."""
        if action.side == "linux":
            prog = action.argv[0]
            path = self._binary(prog)
            if path is None or not path.is_file():
                what = "systemctl" if prog == "systemctl" else "the fleet-up script"
                return f"Not on this host: {what} was not found."
            return ""
        if helper_state == MISSING:
            return "Needs the admin helper."
        if helper_state == UNPROVEN and action.verb != "ping":
            return "Offered once the admin helper has answered."
        return ""

    def _helper_state(self, runs: list[Run]) -> str:
        """The helper's files decide first; then a READY helper whose last desk run got no
        answer at all is UNPROVEN again, so only the harmless check is offered."""
        state = self.helper.state()
        if state != READY:
            return state
        for r in reversed(runs):
            a = ACTIONS.get(r.action)
            if a is not None and a.side == "windows" and r.result != RUNNING:
                return UNPROVEN if r.silent else READY
        return READY

    async def view(self) -> RunView:
        runs = await self.settled()
        try:
            state = await asyncio.wait_for(
                asyncio.to_thread(self._helper_state, runs), STATE_TIMEOUT_S
            )
        except TimeoutError:
            state = UNPROVEN
        cards = tuple(Card(a, self.why_not(a, state)) for a in ACTIONS.values())
        shown = tuple(reversed(runs[-SHOWN_RUNS:]))
        return RunView(cards, state, shown, self.settings.owner_login)

    async def refusal(self, action: Action, runs: list[Run] | None = None) -> tuple[str, int]:
        """Why ``action`` may not run now, in plain words, with its HTTP status; ("", 0)
        when it may. Checked before the confirm step is shown and again before the run."""
        if action.side == "linux":
            why = self.why_not(action, MISSING)
            return (why, 503) if why else ("", 0)
        if runs is None:
            runs = await self.settled()
        state = await asyncio.to_thread(self._helper_state, runs)
        if not self.why_not(action, state):
            return "", 0
        extra = (
            f" Tap {ACTIONS['helper-check'].label} first; the other Windows buttons are "
            "offered once it answers."
            if state == UNPROVEN
            else ""
        )
        return f"{NOT_SET_UP} ({SITTING}){extra}", 409

    # ---- settling runs the desk did not see finish -----------------------------------

    async def settled(self) -> list[Run]:
        """The log's runs, with any the desk did not see finish settled and written."""
        async with self._settle:
            runs = await asyncio.to_thread(self.log.runs)
            changed = False
            for run in runs:
                if run.result != RUNNING or run.id in self._active:
                    continue
                if await self._settle_one(run):
                    changed = True
            if changed:
                self._changed()
            return runs

    async def _settle_one(self, run: Run) -> bool:
        action = ACTIONS.get(run.action)
        now = self._clock()
        restarted = run.start < self.booted_at
        if action is None:
            run.result, run.detail = UNKNOWN, "Not an action this desk knows."
        elif action.side == "windows" and run.nonce:
            reply = await asyncio.to_thread(self.helper.find, run.nonce)
            if reply is not None:
                run.result = reply.result
                run.detail = reply.detail + (
                    " The desk restarted during the run." if restarted else ""
                )
            elif not restarted and now - run.start < action.timeout_s + 30:
                return False  # its own run may still answer
            else:
                withdrawn = await asyncio.to_thread(self.helper.withdraw, run.nonce)
                run.result = UNKNOWN
                run.detail = (
                    "The desk restarted before the admin helper answered, and its log has no "
                    "reply for this request." + (" The request was withdrawn." if withdrawn else "")
                )
        elif action.key == "restart-desk" and restarted:
            run.result = OK
            run.detail = "The desk restarted and came back."
            run.duration_s = max(0.0, self.booted_at - run.start)
        elif restarted or now - run.start > action.timeout_s + 30:
            run.result = UNKNOWN
            run.detail = "The desk restarted during the run, so its result was not seen."
        else:
            return False
        if run.duration_s is None:  # at most until the desk came back, or until now
            run.duration_s = max(0.0, (self.booted_at if restarted else now) - run.start)
        await asyncio.to_thread(self.log.append, _record(run))
        self._journal(run, settled="later")
        return True

    # ---- running ----------------------------------------------------------------------

    def _journal(self, run: Run, **extra: object) -> None:
        """The run's line in the desk's journal too: the same facts as ``run-audit.log``,
        with the owner named by role, not by login."""
        if run.n is not None:
            extra["n"] = run.n
        audit(
            "run",
            run.action,
            run.result.replace(" ", "-"),
            who="owner" if run.who == self.settings.owner_login else "-",
            start=_stamp(run.start),
            duration=f"{run.duration_s or 0.0:.1f}s",
            **extra,
        )

    def _changed(self) -> None:
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change()

    def _limit(self, action: Action, runs: list[Run], now: float) -> None:
        last = next((r for r in reversed(runs) if r.action == action.key), None)
        if last is not None and now - last.start < COOLDOWN_S:
            wait = int(COOLDOWN_S - (now - last.start)) + 1
            raise ActionError(
                f"{action.label} ran {int(now - last.start)} s ago. Try again in {wait} s.", 429
            )
        recent = sum(1 for r in runs if now - r.start < WINDOW_S)
        if recent >= WINDOW_RUNS:
            raise ActionError(
                f"{WINDOW_RUNS} runs in the last {int(WINDOW_S // 60)} minutes is the limit. "
                "Wait a few minutes.",
                429,
            )

    async def run(self, action: Action, n: int | None, run_id: str, who: str) -> Finished:
        """Run one action once. Refusals before it starts raise ``ActionError`` and are not
        runs; once started it is always recorded, whatever happens."""
        lock = self._locks.setdefault(action.group, asyncio.Lock())
        if lock.locked():
            busy = "A Windows action" if action.side == "windows" else action.label
            raise ActionError(f"{busy} is already running. Wait for it to finish.", 409)
        async with lock:
            runs = await self.settled()
            why, status = await self.refusal(action, runs)
            if why:
                raise ActionError(why, status)
            self._limit(action, runs, self._clock())
            if action.key == "restart-desk":
                await self._guard_desk()
            run = Run(run_id, action.key, who, self._clock(), n)
            if action.side == "windows":
                run.nonce = new_nonce()
            self._active.add(run_id)
            try:
                return await self._record_run(action, n, run)
            finally:
                self._active.discard(run_id)

    async def _record_run(self, action: Action, n: int | None, run: Run) -> Finished:
        started = time.monotonic()
        await asyncio.to_thread(self.log.append, _record(run))
        self._changed()
        try:
            result = await self._execute(action, n, run)
        except Exception as exc:  # recorded, never left open
            result = Finished(FAILED, f"The desk could not run it: {type(exc).__name__}.")
        run.result = result.result
        run.detail = result.message
        run.duration_s = time.monotonic() - started
        await asyncio.to_thread(self.log.append, _record(run))
        self._journal(run, rid=run.id)
        self._changed()
        return result

    async def _guard_desk(self) -> None:
        """Restarting the desk's service is only safe when this desk is that service."""
        systemctl = self.settings.systemctl
        done = await self._run(
            [str(systemctl), "--user", "show", "--property=MainPID", "--value", DESK_UNIT],
            GUARD_TIMEOUT_S,
        )
        pid = done.output.strip()
        if done.code != 0 or done.timed_out or pid != str(self._pid):
            raise ActionError(
                "This desk is not running as its service, so restarting the service would "
                "start a second desk. Nothing was restarted.",
                409,
            )

    async def _execute(self, action: Action, n: int | None, run: Run) -> Finished:
        if action.side == "windows":
            reply = await self.helper.ask(action.verb, n, run.nonce, action.timeout_s)
            run.silent = reply.silent
            return Finished(reply.result, reply.detail)
        argv = [str(self._binary(action.argv[0])), *action.argv[1:]]
        done = await self._run(argv, action.timeout_s)
        if done.timed_out:
            if action.key == "fleet-up":
                return Finished(
                    TIMED_OUT,
                    f"Fleet-up did not finish within {int(action.timeout_s // 60)} minutes. The "
                    "desk stopped waiting; fleet-up carries on by itself. Check the Machine "
                    "page in a few minutes.",
                )
            return Finished(
                TIMED_OUT, f"It did not finish within {int(action.timeout_s)} s and was stopped."
            )
        if done.code != 0:
            said = _first(done.output) or f"exit {done.code}"
            return Finished(FAILED, f"It failed: {said}")
        if action.key == "restart-desk":
            return Finished(
                OK,
                "The desk is restarting. This page reloads itself in a few seconds.",
                action.reload_s,
            )
        if action.key == "serve-maps":
            bad = re.findall(r"serve: failed for :(\d+)", done.output)
            if bad:
                return Finished(
                    FAILED,
                    f"Tailscale did not take port{'s' if len(bad) > 1 else ''} "
                    f"{', '.join(bad)}. Check the Tailscale row on the Machine page.",
                )
            return Finished(OK, "The serve maps are set again.")
        if action.key == "fleet-up":
            return Finished(OK, "Fleet-up ran to the end: anything that was down was started.")
        if action.check_unit:
            check = await self._run(
                [str(self.settings.systemctl), "--user", "is-active", action.check_unit],
                GUARD_TIMEOUT_S,
            )
            state = redact(check.output.strip(), 40) or "unknown"
            name = action.label.removeprefix("Restart the ")
            if check.code != 0 or state != "active":
                tail = (
                    " It stays off until it is set up (docs/telegram.md)."
                    if action.check_unit == TELEGRAM_UNIT
                    else " Re-run fleet-up, or look at its log at the PC."
                )
                said = f"The {name} is not running after the restart ({state})."
                return Finished(FAILED, said + tail)
            return Finished(OK, f"The {name} is running again.")
        return Finished(OK, "Done.")


def _stamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))
