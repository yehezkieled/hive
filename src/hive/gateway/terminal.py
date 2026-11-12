"""Terminal page: firstmate's own herdr pane, live in the browser, read-only by default.

The bridge runs no program of its own except herdr's CLI, and never a shell. It finds the
one herdr pane firstmate runs in by the rule ``scripts/fleet-up.sh`` and ``wake.py`` use
(``agent == "claude"``, with the firstmate home as both its cwd and its foreground cwd),
reads that pane's screen with ``herdr pane read``, and only while the owner holds the
keyboard types into it with ``herdr pane send-text`` and ``herdr pane send-keys``. No
request names a pane, a session, a command or a program: there is nothing to choose.

Rules this module keeps:

- Opening needs the desk's step-up. A finished step-up hands the page a pass that lets one
  live connection at a time attach, for at most ``SESSION_MAX_S``; then the page has to
  confirm again. A desk restart forgets every pass.
- Read-only unless this connection took the keyboard with a second step-up. One holder at a
  time; another tab cannot type. The keyboard goes back after ``KEYBOARD_IDLE_S`` without a
  keystroke, when the pass runs out, when the tab goes away and when the pane changes.
- What the pane shows and what is typed live in memory only while someone watches. Neither
  is ever logged, audited, written to a file or published to the event stream.
- The audit log records who, which tab (a random id), what happened and when: open, attach,
  take, release, detach, expired, refused. Only fixed words are ever written to it.
- When firstmate's pane cannot be found the page says so in plain words and offers nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import secrets
import time
import unicodedata
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hive.gateway import actions
from hive.gateway.actions import ActionError
from hive.gateway.settings import GatewaySettings

SESSION_MAX_S = 30 * 60.0  # the hard limit on one attach before the page must confirm again
KEYBOARD_IDLE_S = 10 * 60.0  # the keyboard goes back to read-only after this long untouched
TAKE_STEP_TTL_S = 60.0  # how long "Tap again to take" stays armed
ATTACH_WAIT_S = 5.0  # a new connection must present its pass within this
POLL_FAST_S = 0.25  # how often the pane is read while it changes or is typed into
POLL_SLOW_S = 1.0  # ... and once it has been still for ``SLOW_AFTER_S``
SLOW_AFTER_S = 10.0
IDENTITY_EVERY_S = 2.0  # how often the pane is looked up again
READ_FAILS_LOST = 3  # this many failed reads in a row and the pane is looked up again
HERDR_TIMEOUT_S = 5.0
OPEN_LOOK_S = 2.0  # how long opening waits on herdr before it says herdr is not answering
HISTORY_LINES = 1000  # herdr ignores a smaller --lines than the pane's height, so keep it big
HISTORY_EVERY_S = 2.0  # a page may ask for the scrollback at most this often
MAX_VIEWERS = 4  # tabs attached at once
MAX_MESSAGE = 16 * 1024  # characters in one message from a page
MAX_INPUT = 4096  # characters of typed or pasted text in one message
MAX_QUEUE = 256  # inputs waiting for herdr before more are refused
MAX_OUTBOX = 64  # control messages waiting for a slow page before it is dropped
MAX_LINES = 400  # rows in one screen
MAX_LINE = 4000  # characters in one row
MAX_HISTORY_CHARS = 1_000_000
OPEN_LIMIT = 6  # opens (finished step-ups) allowed ...
OPEN_WINDOW_S = 900.0  # ... inside this many seconds, across desk restarts
ATTACH_LIMIT = 30  # attach attempts (any tab, good pass or not) allowed ...
ATTACH_WINDOW_S = 600.0  # ... inside this many seconds
MIN_COLS = 20

# Close codes a page reads. 4401: confirm again. 4409: the pass is in use elsewhere.
CLOSE_STEP_UP, CLOSE_IN_USE, CLOSE_LIMIT, CLOSE_FULL, CLOSE_END = 4401, 4409, 4429, 4503, 4000

# Why something ended or was refused, as fixed slugs (the audit log's words) with the plain
# words a page shows. Nothing a pane printed or a page sent is ever used as a reason.
WORDS = {
    "herdr-missing": "herdr was not found on this PC, so there is no session to show.",
    "herdr-down": (
        "herdr is not answering, so firstmate's session cannot be shown. "
        "Check the Firstmate row on the Machine page."
    ),
    "no-session": (
        "Firstmate's session was not found: no herdr pane runs Claude in the firstmate home. "
        "Use Wake firstmate on the desk."
    ),
    "many-sessions": (
        "More than one herdr pane looks like firstmate's session, so the terminal will not "
        "pick one. Ask firstmate to close the extra pane."
    ),
    "session-gone": "Firstmate's session ended. The keyboard went back to read-only.",
    "pane-changed": (
        "Firstmate's session moved to another pane. The keyboard went back to read-only."
    ),
    "step-up": "Confirm it is you to open the terminal.",
    "expired": "The terminal was open for its time limit. Confirm it is you to open it again.",
    "in-use": "This confirmation is already in use in another tab. Confirm it is you again here.",
    "rate-limited": "Too many attempts to open the terminal. Wait a few minutes.",
    "full": "Too many tabs have the terminal open. Close one first.",
    "shutdown": "The desk restarted. Confirm it is you to open the terminal again.",
    "slow": "This page fell too far behind the terminal. Reload it.",
    "held": "Another tab has the keyboard. Release it there, or wait until it goes back by itself.",
    "read-only": "Read-only: take the keyboard first. Nothing was typed.",
    "not-armed": "Tap Take keyboard first, then tap again within a minute.",
    "no-pane": "Firstmate's session is not showing, so the keyboard cannot be taken.",
    "too-long": "That was too much text at once. Nothing was typed.",
    "busy": "Typing is going too fast for herdr. Some input was not typed.",
    "not-typed": "herdr did not take that input. It may not have been typed.",
    "idle": "The keyboard went back to read-only after 10 minutes without typing.",
}

# ---- what a pane prints, made safe to show: pure, never raising ----------------------------

# One terminal sequence: CSI, OSC (to BEL or ST), DCS/SOS/PM/APC (to ST), any other ESC
# sequence, or a lone ESC at the end.
_ESC_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|[P^_X][^\x1b]*(?:\x1b\\)?"
    r"|[ -/]*[0-~]|$)"
)
_PANE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}$")
_SGR_RE = re.compile(r"\x1b\[[0-9;:]{0,64}m")


def _plain(text: str, keep: str = "\n\t") -> str:
    """``text`` without control characters, except those in ``keep``."""
    return "".join(c for c in text if c in keep or unicodedata.category(c) not in ("Cc", "Cs"))


def sgr_only(text: str) -> str:
    """The pane's text with colours kept and every other terminal sequence dropped.

    Cursor movement, screen modes, titles, hyperlinks, clipboard writes and device queries
    never reach the browser's terminal: the page draws each screen itself.
    """
    out: list[str] = []
    pos = 0
    for m in _ESC_RE.finditer(text):
        out.append(_plain(text[pos : m.start()]))
        if _SGR_RE.fullmatch(m.group(0)):
            out.append(m.group(0))
        pos = m.end()
    out.append(_plain(text[pos:]))
    return "".join(out)


def width(line: str) -> int:
    """How many terminal cells ``line`` takes, colours aside."""
    cells = 0
    for c in _SGR_RE.sub("", line):
        if unicodedata.combining(c):
            continue
        cells += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return cells


def screen_lines(raw: str, limit: int) -> list[str]:
    """The rows of a pane read, made safe and bounded."""
    text = sgr_only(raw.replace("\r\n", "\n").replace("\r", ""))
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    return [ln[:MAX_LINE] for ln in lines[-limit:]]


# ---- what a page types, as herdr input: pure, never raising --------------------------------

# herdr's key names. ``enter``, ``escape``, ``ctrl+c`` and ``ctrl+u`` are the ones firstmate's
# herdr backend verified; the rest follow the same scheme (see docs/terminal.md).
_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_SS3_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE_KEYS = {
    "1": "home",
    "3": "delete",
    "4": "end",
    "5": "pageup",
    "6": "pagedown",
    "7": "home",
    "8": "end",
}
# A whole CSI sequence (parameters, intermediates, final byte), so a mouse report such as
# ``ESC [ < 0 ; 10 ; 5 M`` is dropped whole and never typed as text.
_CSI_RE = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
KEYS = frozenset(
    {
        "enter",
        "escape",
        "tab",
        "shift+tab",
        "backspace",
        *_CSI_KEYS.values(),
        *_TILDE_KEYS.values(),
        *(f"ctrl+{chr(c)}" for c in range(ord("a"), ord("z") + 1)),
    }
)


@dataclass(frozen=True)
class Op:
    """One herdr call: literal text, or named key presses."""

    text: str = ""
    keys: tuple[str, ...] = ()


def translate(data: str) -> list[Op]:
    """Terminal input, as a browser terminal sends it, as herdr text and key presses.

    Plain text stays text. Enter, Esc, Tab, Backspace, the arrows, Home, End, Page Up and
    Down, Delete and Ctrl with a letter become key presses. Anything else (mouse reports,
    focus events, modified arrows, function keys, other control bytes) is dropped.
    """
    ops: list[Op] = []
    text: list[str] = []

    def flush() -> None:
        if text:
            ops.append(Op(text="".join(text)))
            text.clear()

    def key(name: str) -> None:
        flush()
        if ops and ops[-1].keys:
            ops[-1] = Op(keys=(*ops[-1].keys, name))
        else:
            ops.append(Op(keys=(name,)))

    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == "\x1b":
            nxt = data[i + 1] if i + 1 < n else ""
            if nxt == "[":
                m = _CSI_RE.match(data, i)
                if m is None:
                    key("escape")
                    i += 1
                    continue
                params, inter, final = m.groups()
                i = m.end()
                name = None
                if not inter and final == "~":
                    name = _TILDE_KEYS.get(params)
                elif not inter and params == "" and final == "Z":
                    name = "shift+tab"
                elif not inter and params in ("", "1"):
                    name = _CSI_KEYS.get(final)
                if name:
                    key(name)
                continue
            if nxt == "O" and i + 2 < n:
                name = _SS3_KEYS.get(data[i + 2])
                if name:
                    key(name)
                i += 3
                continue
            key("escape")
            i += 1
            continue
        if c == "\r":
            key("enter")
        elif c == "\t":
            key("tab")
        elif c in ("\x7f", "\x08"):
            key("backspace")
        elif "\x01" <= c <= "\x1a":
            key("ctrl+" + chr(ord(c) + 96))
        elif unicodedata.category(c) not in ("Cc", "Cs"):
            text.append(c)
        i += 1
    flush()
    return ops


PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"


def paste_text(data: str) -> str:
    """Text from the paste box as it is typed: control characters dropped, line breaks kept.

    Text with a line break goes as a bracketed paste, as a terminal pastes it, so Claude Code
    takes the lines as one paste instead of an Enter after each.
    """
    text = _plain(data.replace("\r\n", "\n").replace("\r", "\n"))
    return f"{PASTE_START}{text}{PASTE_END}" if "\n" in text else text


# ---- herdr -----------------------------------------------------------------------------------


class HerdrError(Exception):
    """A herdr call that failed. Its message is a fixed slug: never herdr's output."""


@dataclass(frozen=True)
class Pane:
    pane_id: str
    cols: int = 0  # the pane's size from herdr's layout, when it gave one
    rows: int = 0


@dataclass(frozen=True)
class Found:
    pane: Pane | None
    why: str = ""  # a WORDS slug when ``pane`` is None


class Herdr:
    """herdr's CLI: list panes, read one, type into one. Nothing it prints is logged, and an
    argument list (which may hold typed text) is never put into an error or a log line."""

    def __init__(self, binary: Path | None) -> None:
        self.binary = binary

    @property
    def available(self) -> bool:
        return self.binary is not None and os.access(self.binary, os.X_OK)

    async def _run(self, *args: str, timeout: float = HERDR_TIMEOUT_S) -> str:
        if not self.available:
            raise HerdrError("herdr-missing")
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.binary),
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            raise HerdrError("herdr-missing") from None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HerdrError("herdr-down") from None
        if proc.returncode != 0:
            raise HerdrError("herdr-down")
        return out.decode("utf-8", "replace")

    async def panes(self, timeout: float = HERDR_TIMEOUT_S) -> list[dict]:
        try:
            data = json.loads(await self._run("pane", "list", timeout=timeout))
            panes = data["result"]["panes"]
        except (ValueError, KeyError, TypeError):
            raise HerdrError("herdr-down") from None
        if not isinstance(panes, list):
            raise HerdrError("herdr-down")
        return [p for p in panes if isinstance(p, dict)]

    async def size(self, pane_id: str, timeout: float = HERDR_TIMEOUT_S) -> tuple[int, int]:
        """The pane's (columns, rows) from herdr's layout; 0 for what cannot be read."""
        try:
            out = await self._run("pane", "layout", "--pane", pane_id, timeout=timeout)
            data = json.loads(out)
        except (HerdrError, ValueError):
            return 0, 0
        return _rect(data, pane_id)

    async def read(self, pane_id: str, history: bool = False) -> str:
        if history:
            source = ("--source", "recent", "--lines", str(HISTORY_LINES))
        else:
            source = ("--source", "visible")
        return await self._run("pane", "read", pane_id, *source, "--format", "ansi")

    async def send(self, pane_id: str, op: Op) -> None:
        if op.text:
            # "--" so typed text that starts with "-" is never read as one of herdr's options.
            await self._run("pane", "send-text", pane_id, "--", op.text)
        elif op.keys:
            await self._run("pane", "send-keys", pane_id, *op.keys)


def _rect(data: object, pane_id: str) -> tuple[int, int]:
    """The ``rect`` (width, height) of the entry for ``pane_id`` anywhere in a layout answer."""

    def cells(value: object, top: int) -> int:
        return value if isinstance(value, int) and 0 < value <= top else 0

    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            rect = item.get("rect")
            if item.get("pane_id") == pane_id and isinstance(rect, dict):
                return cells(rect.get("width"), 1000), cells(rect.get("height"), MAX_LINES)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return 0, 0


def find_pane(panes: Sequence[dict], homes: set[str]) -> Found:
    """Firstmate's own pane: the one pane running Claude in the firstmate home.

    A crew opened from the home runs in its worktree, so its foreground cwd differs and it
    never counts. None found, or more than one, offers nothing.
    """
    hits = [
        p
        for p in panes
        if p.get("agent") == "claude"
        and p.get("cwd") in homes
        and (p.get("foreground_cwd") or p.get("cwd")) in homes
        and isinstance(p.get("pane_id"), str)
        and _PANE_ID_RE.fullmatch(p["pane_id"])
    ]
    if not hits:
        return Found(None, "no-session")
    if len(hits) > 1:
        return Found(None, "many-sessions")
    return Found(Pane(hits[0]["pane_id"]))


# ---- viewers -----------------------------------------------------------------------------------


@dataclass
class Pass:
    """What a finished step-up hands the page: lets one live connection attach until ``ends``."""

    token: str
    who: str
    ends: float  # wall clock
    conn: str = ""  # the live connection using it, if any


class Viewer:
    """One attached tab. Its page reads messages from ``next()``; control messages keep their
    order, and of screens only the latest is ever waiting."""

    def __init__(self, conn: str, who: str, grant: Pass) -> None:
        self.conn = conn
        self.who = who
        self.grant = grant
        self.attached = time.time()
        self.take_step: tuple[str, float] | None = None
        self.last_history = 0.0
        self.notified: dict[str, float] = {}
        self._out: deque[dict] = deque()
        self._screen: dict | None = None
        self._woke = asyncio.Event()
        self.ended: tuple[int, str] | None = None  # (close code, reason) once it must close

    def push(self, msg: dict) -> None:
        if self.ended is not None:
            return
        if len(self._out) >= MAX_OUTBOX:
            self.end(CLOSE_END, "slow")
            return
        self._out.append(msg)
        self._woke.set()

    def screen(self, msg: dict) -> None:
        if self.ended is None:
            self._screen = msg
            self._woke.set()

    def end(self, code: int, reason: str) -> None:
        if self.ended is not None:
            return
        self._out.clear()
        self._screen = None
        self._out.append({"t": "end", "reason": reason, "text": WORDS.get(reason, "")})
        self.ended = (code, reason)
        self._woke.set()

    async def next(self) -> dict | None:
        """The next message for the page; None once the last one (``end``) was taken."""
        while True:
            if self._out:
                return self._out.popleft()
            if self._screen is not None and self.ended is None:
                msg, self._screen = self._screen, None
                return msg
            if self.ended is not None:
                return None
            self._woke.clear()
            await self._woke.wait()


class AttachError(Exception):
    """An attach refused: the close code a page reads, and the reason slug."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class _Keyboard:
    conn: str
    who: str
    since: float  # wall clock
    last: float  # wall clock of the last keystroke (or the take)


@dataclass(frozen=True)
class _Screen:
    lines: tuple[str, ...]
    cols: int


@dataclass(frozen=True)
class _Queued:
    conn: str
    pane_id: str
    op: Op


def overlap(history: Sequence[str], screen: Sequence[str]) -> int:
    """How many of the screen's top rows are the last rows of ``history``.

    herdr's recent output ends with what is on the screen, so the scrollback a page shows
    is the history without them: no row twice, and none lost.
    """
    plain_h = [_SGR_RE.sub("", ln).rstrip() for ln in history[-len(screen) :]] if screen else []
    plain_s = [_SGR_RE.sub("", ln).rstrip() for ln in screen]
    for k in range(min(len(plain_h), len(plain_s)), 0, -1):
        if plain_h[-k:] == plain_s[:k]:
            return k
    return 0


def _grid(msg: dict) -> dict:
    return {"cols": msg["cols"], "rows": msg["rows"]}


class TerminalService:
    """The passes, the attached tabs, the one keyboard holder and the pane bridge."""

    def __init__(
        self,
        settings: GatewaySettings,
        herdr: Herdr | None = None,
        *,
        session_max_s: float = SESSION_MAX_S,
        keyboard_idle_s: float = KEYBOARD_IDLE_S,
        open_limit: int = OPEN_LIMIT,
        attach_limit: int = ATTACH_LIMIT,
        poll_fast_s: float = POLL_FAST_S,
        poll_slow_s: float = POLL_SLOW_S,
    ) -> None:
        self.settings = settings
        self.herdr = herdr or Herdr(settings.herdr)
        self.session_max_s = session_max_s
        self.keyboard_idle_s = keyboard_idle_s
        self._open_limit = open_limit
        self._attach_limit = attach_limit
        self._poll_fast_s = poll_fast_s
        self._poll_slow_s = poll_slow_s
        self._passes: dict[str, Pass] = {}
        self._spent: dict[str, float] = {}  # step-up tokens already used to open, until expiry
        self._viewers: dict[str, Viewer] = {}
        self._keyboard: _Keyboard | None = None
        self._found: Found | None = None  # None: not looked up since someone started watching
        self._screen: _Screen | None = None
        self._cols = 0  # the widest the pane has been seen; a page's grid never shrinks
        self._rows = 0  # ... and the tallest
        self._read_fails = 0
        self._changed = 0.0
        self._attaches: deque[float] = deque()
        self._queue: asyncio.Queue[_Queued] | None = None
        self._pump: asyncio.Task[None] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._kick = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def pane(self) -> Pane | None:
        return self._found.pane if self._found is not None else None

    # ---- the audit log and the open limit ---------------------------------------------------

    @property
    def audit_path(self) -> Path:
        return self.settings.data_dir / "terminal-audit.log"

    @property
    def _opens_path(self) -> Path:
        return self.settings.data_dir / "terminal-opens"

    def _audit(self, who: str, tab: str, event: str, reason: str = "", **extra: object) -> None:
        """Who, which tab, what happened and when. Only fixed words and numbers are written:
        never a keystroke, never anything the pane printed."""
        name = "".join(c for c in who if c.isprintable() and not c.isspace())[:80] or "-"
        fields: dict[str, object] = {"tab": tab or "-"}
        if reason:
            fields["reason"] = reason
        fields.update(extra)
        actions.audit(
            "terminal", tab or "-", event, **{k: v for k, v in fields.items() if k != "tab"}
        )
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"{stamp} who={name} via=desk event={event} " + " ".join(
            f"{k}={v}" for k, v in fields.items()
        )
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass  # an unwritable log must not break the page; the journal line still went out

    def _claim_open(self, now: float) -> float:
        """Count one open, across restarts. 0 when allowed, else seconds to wait."""
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._opens_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                seen = [float(t) for t in json.loads(fh.read() or "[]")]
            except (ValueError, TypeError):
                seen = []
            recent = sorted(t for t in seen if now - OPEN_WINDOW_S < t <= now)
            if len(recent) >= self._open_limit:
                return max(1.0, recent[0] + OPEN_WINDOW_S - now)
            recent.append(now)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(recent))
            return 0.0

    # ---- finding the pane ---------------------------------------------------------------------

    def _homes(self) -> set[str]:
        home = self.settings.fm_home
        homes = {str(home)}
        with contextlib.suppress(OSError):
            homes.add(str(home.resolve()))
        return homes

    async def look(self, timeout: float = HERDR_TIMEOUT_S) -> Found:
        """Look firstmate's pane up now. The pane's width is read once per pane."""
        try:
            found = find_pane(await self.herdr.panes(timeout), self._homes())
        except HerdrError as exc:
            return Found(None, str(exc))
        if found.pane is None:
            return found
        known = self.pane
        if known is not None and known.pane_id == found.pane.pane_id:
            return Found(known)
        cols, rows = await self.herdr.size(found.pane.pane_id, timeout)
        return Found(Pane(found.pane.pane_id, cols, rows))

    def _set_found(self, found: Found) -> None:
        old = self._found
        self._found = found
        before = old.pane.pane_id if old is not None and old.pane is not None else None
        after = found.pane.pane_id if found.pane is not None else None
        if before != after:
            self._screen = None
            self._cols = self._rows = 0
            self._read_fails = 0
            if before is not None and self._keyboard is not None:
                self._release("pane-changed" if after is not None else "session-gone")
                return  # releasing told every page already
        if old is None or before != after or (after is None and old.why != found.why):
            self._broadcast_state()

    # ---- opening and attaching ----------------------------------------------------------------

    async def open(self, who: str) -> Pass:
        """A finished step-up: check the session is there and the limit allows it, then hand
        out a pass. Raises ``ActionError`` with plain words otherwise; nothing is started."""
        found = await self.look(OPEN_LOOK_S)
        if found.pane is None:
            self._audit(who, "", "refused", found.why)
            raise ActionError(WORDS[found.why], 503 if found.why.startswith("herdr") else 409)
        try:
            wait = await asyncio.to_thread(self._claim_open, time.time())
        except OSError as exc:
            raise ActionError(
                "The desk could not record the attempt, so the terminal was not opened.", 503
            ) from exc
        if wait > 0:
            self._audit(who, "", "refused", "rate-limited")
            minutes = max(1, int((wait + 59) // 60))
            raise ActionError(
                f"Too many attempts to open the terminal. Try again in {minutes} min.", 429
            )
        now = time.time()
        self._passes = {k: p for k, p in self._passes.items() if p.ends > now}
        grant = Pass(secrets.token_urlsafe(32), who, now + self.session_max_s)
        self._passes[grant.token] = grant
        self._audit(who, "", "open", ends=int(grant.ends))
        return grant

    def spend_step(self, step: str) -> bool:
        """Use a step-up token once: a confirmed open cannot be replayed for a second pass."""
        now = time.time()
        self._spent = {k: t for k, t in self._spent.items() if t > now}
        if step in self._spent:
            return False
        self._spent[step] = now + actions.STEP_UP_TTL_S + 1
        return True

    def _attach_allowed(self, now: float) -> bool:
        seen = self._attaches
        while seen and seen[0] <= now - ATTACH_WINDOW_S:
            seen.popleft()
        if len(seen) >= self._attach_limit:
            return False
        seen.append(now)
        return True

    async def attach(self, token: object, who: str) -> Viewer:
        """Bind a live connection to its pass. Raises ``AttachError`` (close code, reason)."""
        async with self._lock:
            now = time.time()
            if not self._attach_allowed(now):
                self._audit(who, "", "refused", "rate-limited")
                raise AttachError(CLOSE_LIMIT, "rate-limited")
            grant = self._passes.get(token) if isinstance(token, str) and len(token) < 200 else None
            if grant is None or grant.who != who:
                self._audit(who, "", "refused", "step-up")
                raise AttachError(CLOSE_STEP_UP, "step-up")
            if grant.ends <= now:
                self._audit(who, "", "refused", "expired")
                raise AttachError(CLOSE_STEP_UP, "expired")
            if grant.conn and grant.conn in self._viewers:
                self._audit(who, "", "refused", "in-use")
                raise AttachError(CLOSE_IN_USE, "in-use")
            if len(self._viewers) >= MAX_VIEWERS:
                self._audit(who, "", "refused", "full")
                raise AttachError(CLOSE_FULL, "full")
            viewer = Viewer(secrets.token_hex(4), who, grant)
            grant.conn = viewer.conn
            self._viewers[viewer.conn] = viewer
            self._audit(who, viewer.conn, "attach", ends=int(grant.ends))
            self._start()
            self._broadcast_state()
            if self._screen is not None:
                viewer.screen(self._screen_msg(self._screen))
            self._kick.set()
            return viewer

    async def detach(self, viewer: Viewer, reason: str = "closed") -> None:
        if self._viewers.pop(viewer.conn, None) is None:
            return
        if self._keyboard is not None and self._keyboard.conn == viewer.conn:
            self._release("detach")
        held = int(time.time() - viewer.attached)
        self._audit(viewer.who, viewer.conn, "detach", reason, attached_s=held)
        viewer.end(CLOSE_END, reason)
        if self._viewers:
            self._broadcast_state()
        else:
            await self._idle()

    async def _idle(self) -> None:
        """Nobody watches: stop reading, forget the screen and anything still to be typed."""
        pump, writer = self._pump, self._writer
        self._pump = self._writer = None
        self._queue = None
        self._found = None
        self._screen = None
        self._cols = self._rows = 0
        current = asyncio.current_task()
        for task in (pump, writer):
            if task is not None and task is not current and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def _start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._queue is None:
            self._queue = asyncio.Queue(MAX_QUEUE)
        if self._writer is None or self._writer.done():
            self._writer = loop.create_task(self._run_writer(self._queue))
        if self._pump is None or self._pump.done():
            self._pump = loop.create_task(self._run_pump())

    async def stop(self) -> None:
        """Shutdown: end every tab, release the keyboard, forget every pass."""
        for viewer in list(self._viewers.values()):
            viewer.end(1001, "shutdown")
            await self.detach(viewer, "shutdown")
        self._passes.clear()
        await self._idle()

    # ---- the keyboard --------------------------------------------------------------------------

    def _release(self, reason: str) -> None:
        kb = self._keyboard
        if kb is None:
            return
        self._keyboard = None
        held = int(time.time() - kb.since)
        self._audit(kb.who, kb.conn, "release", reason, held_s=held)
        viewer = self._viewers.get(kb.conn)
        if viewer is not None and reason in WORDS:
            viewer.push({"t": "notice", "text": WORDS[reason]})
        self._broadcast_state()

    def _take(self, viewer: Viewer, step: object) -> None:
        now = time.time()
        if self.pane is None:
            viewer.take_step = None
            viewer.push({"t": "notice", "text": WORDS["no-pane"]})
            return
        kb = self._keyboard
        if kb is not None and kb.conn != viewer.conn:
            viewer.take_step = None
            self._audit(viewer.who, viewer.conn, "refused", "held")
            viewer.push({"t": "notice", "text": WORDS["held"]})
            return
        if kb is not None:  # this tab has it already
            viewer.push(self.state_for(viewer))
            return
        if not isinstance(step, str) or not step:
            token = secrets.token_urlsafe(16)
            viewer.take_step = (token, now + TAKE_STEP_TTL_S)
            viewer.push(
                {
                    "t": "confirm",
                    "step": token,
                    "label": "Tap again to take",
                    "detail": (
                        "Typing goes straight into firstmate's own session, as if you were "
                        "at the PC. Esc or Ctrl+C can stop what it is doing."
                    ),
                }
            )
            return
        armed, viewer.take_step = viewer.take_step, None
        if armed is None or armed[1] < now or not secrets.compare_digest(step, armed[0]):
            self._audit(viewer.who, viewer.conn, "refused", "not-armed")
            viewer.push({"t": "notice", "text": WORDS["not-armed"]})
            viewer.push(self.state_for(viewer))
            return
        self._keyboard = _Keyboard(viewer.conn, viewer.who, now, now)
        self._audit(viewer.who, viewer.conn, "take")
        self._broadcast_state()

    def _holds(self, viewer: Viewer) -> bool:
        kb = self._keyboard
        return kb is not None and kb.conn == viewer.conn and self.pane is not None

    def _notify(self, viewer: Viewer, reason: str, every_s: float = 3.0) -> None:
        """A notice in plain words, at most once per ``every_s`` for the same reason."""
        now = time.monotonic()
        if now - viewer.notified.get(reason, -every_s) >= every_s:
            viewer.notified[reason] = now
            viewer.push({"t": "notice", "text": WORDS[reason]})

    def _type(self, viewer: Viewer, ops: list[Op]) -> None:
        """Queue input from the keyboard holder. Anyone else's is dropped, untyped."""
        if not ops:
            return
        kb, pane, queue = self._keyboard, self.pane, self._queue
        if kb is None or kb.conn != viewer.conn or pane is None or queue is None:
            self._notify(viewer, "read-only")
            return
        kb.last = time.time()
        for op in ops:
            try:
                queue.put_nowait(_Queued(viewer.conn, pane.pane_id, op))
            except asyncio.QueueFull:
                self._notify(viewer, "busy")
                return
        self._kick.set()

    # ---- messages from a page --------------------------------------------------------------------

    async def handle(self, viewer: Viewer, raw: str) -> None:
        """One message from an attached page. Unknown or malformed messages are ignored."""
        if viewer.conn not in self._viewers or viewer.ended is not None or len(raw) > MAX_MESSAGE:
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind, data = msg.get("t"), msg.get("d")
        if kind in ("in", "paste") and isinstance(data, str):
            if len(data) > MAX_INPUT:
                self._notify(viewer, "too-long")
                return
            if kind == "in":
                ops = translate(data)
            else:
                text = paste_text(data)
                ops = [Op(text=text)] if text else []
                if msg.get("enter") is True:
                    ops.append(Op(keys=("enter",)))
            self._type(viewer, ops)
        elif kind == "take":
            self._take(viewer, msg.get("step"))
        elif kind == "release":
            if self._keyboard is not None and self._keyboard.conn == viewer.conn:
                self._release("released")
        elif kind == "history":
            await self._history(viewer)

    async def _history(self, viewer: Viewer) -> None:
        """The scrollback, and the screen read right after it, for one page to rebuild with."""
        now = time.monotonic()
        pane = self.pane
        if pane is None or now - viewer.last_history < HISTORY_EVERY_S:
            return
        viewer.last_history = now
        try:
            raw = await self.herdr.read(pane.pane_id, history=True)
            raw_screen = await self.herdr.read(pane.pane_id)
        except HerdrError:
            return
        if self.pane is None or self.pane.pane_id != pane.pane_id:
            return
        history = screen_lines(raw[-MAX_HISTORY_CHARS:], HISTORY_LINES)
        screen = self._take_screen(raw_screen)
        cut = overlap(history, screen.lines)
        history = history[: len(history) - cut]
        self._cols = max([self._cols, *(width(ln) for ln in history)])
        msg = self._screen_msg(screen)
        viewer.push({"t": "history", "lines": history, "screen": msg["lines"], **_grid(msg)})

    # ---- what each page is told ---------------------------------------------------------------

    def _screen_msg(self, screen: _Screen) -> dict:
        """A screen for the page. Its grid is the widest and tallest the pane has been, and
        never smaller than herdr's layout says: the page never resizes the pane."""
        pane = self.pane or Pane("")
        return {
            "t": "screen",
            "lines": list(screen.lines),
            "cols": max(MIN_COLS, self._cols, pane.cols),
            "rows": max(1, self._rows, pane.rows),
        }

    def _take_screen(self, raw: str) -> _Screen:
        lines = tuple(screen_lines(raw, MAX_LINES))
        cols = max([0, *(width(ln) for ln in lines)])
        self._cols = max(self._cols, cols)
        self._rows = max(self._rows, len(lines))
        return _Screen(lines, cols)

    def state_for(self, viewer: Viewer) -> dict:
        kb = self._keyboard
        holder = "none" if kb is None else "you" if kb.conn == viewer.conn else "other"
        found = self._found
        session = "looking" if found is None else "ok" if found.pane is not None else "missing"
        state: dict[str, object] = {
            "t": "state",
            "session": session,
            "keyboard": holder,
            "viewers": len(self._viewers),
            "who": viewer.who,
            "tab": viewer.conn,
            "ends": int(viewer.grant.ends),
            "idle_s": int(self.keyboard_idle_s),
        }
        if found is not None and found.pane is None:
            state["why"] = WORDS.get(found.why, WORDS["no-session"])
        if kb is not None:
            state["since"] = int(kb.since)
            if holder == "you":
                state["idle_ends"] = int(kb.last + self.keyboard_idle_s)
        return state

    def _broadcast_state(self) -> None:
        for viewer in self._viewers.values():
            viewer.push(self.state_for(viewer))

    # ---- the pump: read the pane, expire passes and the keyboard -----------------------------

    def _expire(self) -> None:
        now = time.time()
        kb = self._keyboard
        if kb is not None and now - kb.last >= self.keyboard_idle_s:
            self._release("idle")
        for viewer in list(self._viewers.values()):
            if viewer.grant.ends <= now and viewer.ended is None:
                self._audit(viewer.who, viewer.conn, "expired")
                if self._keyboard is not None and self._keyboard.conn == viewer.conn:
                    self._release("expired")
                viewer.end(CLOSE_STEP_UP, "expired")

    async def _read_screen(self) -> None:
        pane = self.pane
        if pane is None:
            return
        try:
            raw = await self.herdr.read(pane.pane_id)
        except HerdrError:
            self._read_fails += 1
            if self._read_fails >= READ_FAILS_LOST:
                self._read_fails = 0
                self._set_found(await self.look())
            return
        self._read_fails = 0
        if self.pane is None or self.pane.pane_id != pane.pane_id:
            return  # the pane changed while it was read
        screen = self._take_screen(raw)
        if self._screen is not None and self._screen.lines == screen.lines:
            return
        self._screen = screen
        self._changed = time.monotonic()
        msg = self._screen_msg(screen)
        for viewer in self._viewers.values():
            viewer.screen(msg)

    async def _run_pump(self) -> None:
        loop = asyncio.get_running_loop()
        looked_at = -IDENTITY_EVERY_S
        self._changed = time.monotonic()
        while self._viewers:
            self._expire()
            if loop.time() - looked_at >= IDENTITY_EVERY_S:
                found = await self.look()
                if not self._viewers:
                    break
                self._set_found(found)
                looked_at = loop.time()
            await self._read_screen()
            kb = self._keyboard
            busy = time.monotonic() - self._changed < SLOW_AFTER_S or (
                kb is not None and time.time() - kb.last < SLOW_AFTER_S
            )
            self._kick.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._kick.wait(), self._poll_fast_s if busy else self._poll_slow_s
                )

    async def _run_writer(self, queue: asyncio.Queue[_Queued]) -> None:
        """Type queued input, in order. Each item is checked again just before it is typed: if
        the keyboard went back or the pane changed meanwhile, it is dropped, untyped."""
        while True:
            batch = [await queue.get()]
            # Text typed in quick succession by the same tab goes as one herdr call.
            while not queue.empty() and batch[-1].op.text:
                nxt = queue.get_nowait()
                last = batch[-1]
                if (
                    nxt.op.text
                    and (nxt.conn, nxt.pane_id) == (last.conn, last.pane_id)
                    and len(last.op.text) + len(nxt.op.text) <= MAX_INPUT
                ):
                    batch[-1] = _Queued(
                        last.conn, last.pane_id, Op(text=last.op.text + nxt.op.text)
                    )
                else:
                    batch.append(nxt)
                    break
            for item in batch:
                kb, pane = self._keyboard, self.pane
                if (
                    kb is None
                    or kb.conn != item.conn
                    or pane is None
                    or pane.pane_id != item.pane_id
                ):
                    continue
                try:
                    await self.herdr.send(item.pane_id, item.op)
                except HerdrError:
                    viewer = self._viewers.get(item.conn)
                    if viewer is not None:
                        self._notify(viewer, "not-typed")
            self._kick.set()
