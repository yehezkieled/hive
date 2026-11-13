"""Sign-ins: the state of each sign-in that expires, and renewing one from the desk.

The panel shows each sign-in's state and expiry date, read without touching a token: one
date field for Claude, one state word for GitHub, the key expiry for this PC's Tailscale.
The Claude card also carries a live light, whether Claude's service answers right now
(``reach.py``: one unsigned request, cached, at most once a minute).

Renew runs the vendor's own login command, from a fixed table, in a pseudo-terminal the
desk owns, and hands the link it prints to the page that started it. Nothing from a request
reaches a command line: a request names a provider from the table, and a pasted code is
written to the login command's terminal and nowhere else.

Rules this module keeps:

- One flow at a time, with a hard time-out and a cancel.
- The page that starts a flow gets a random token. The link and any one-time code are only
  ever returned to a request that carries it; any other page learns that a sign-in is
  running and may stop it, nothing more.
- The link, the one-time code and the command's output live in memory only while the flow
  runs. They are never logged, audited, written to a file or published to the event stream.
- Output that cannot be understood offers nothing: the flow is stopped and says so.

Tailscale has a row but no Renew. The desk is reached through this PC's Tailscale, and both
ways of signing it in again (``tailscale login`` switches to an empty profile,
``tailscale up --force-reauth`` may drop the connection) can cut the page that asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hmac
import json
import os
import pty
import re
import secrets
import signal
import struct
import termios
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from hive.gateway import actions, machine, reach
from hive.gateway.actions import ActionError
from hive.gateway.files import append_private, claim_slot
from hive.gateway.machine import DOWN, OK, UNKNOWN, WARN, Reading, Unknown
from hive.gateway.settings import GatewaySettings

FLOW_TIMEOUT_S = 600.0  # the hard limit on one sign-in
READ_WINDOW_S = 30.0  # how long a login command has to print a link that can be offered
KILL_GRACE_S = 2.0  # between asking the login command to stop and killing it
MAX_OUTPUT = 64 * 1024  # bytes of login output kept for reading the link, while a flow runs
TAIL_CHARS = 400  # the latest output, searched for the command's own success and failure words
MUTED_TAIL_CHARS = 64  # the same, once a code was pasted: the command may echo the code
MAX_LINK = 2048
MAX_CODE = 512
MAX_TOKEN = 128
START_LIMIT = 5  # start attempts allowed ...
START_WINDOW_S = 900.0  # ... inside this many seconds
STATE_TTL_S = 15.0  # how old the panel's readings may get before a page view re-reads them
STATE_TIMEOUT_S = 6.0  # one state read; gh checks its token over the network
FIRST_WAIT_S = 0.5  # how long the first page view waits for the first readings
RESULT_TTL_S = 900.0  # how long the last result stays on the page
PTY_COLS = 2000  # wide, so a terminal program never wraps a long link

# Phases of a flow, as a page sees them.
IDLE, STARTING, WAITING, CHECKING, STOPPING = "idle", "starting", "waiting", "checking", "stopping"
DONE_OK, DONE_FAILED, DONE_CANCELLED = "ok", "failed", "cancelled"
ACTIVE = (STARTING, WAITING, CHECKING, STOPPING)

# Why a flow ended, as fixed slugs: these are what the audit log records. The page shows the
# plain words beside each; the login command's own output is never used as a reason.
REASONS = {
    "missing": "The login command was not found on this PC, so nothing was started.",
    "not-started": "The login command could not be started.",
    "not-understood": (
        "The login command's output was not understood, so no link is offered. "
        "Sign in at the PC instead."
    ),
    "timed-out": "The sign-in was not finished in time, so it was stopped. Start it again.",
    "cancelled": "The sign-in was cancelled.",
    "refused": "The login command reported that the sign-in failed. Start it again.",
    "code-expired": "The one-time code ran out before it was used. Start it again.",
    "env-token": (
        "This PC signs in to GitHub with an access token set in its environment, "
        "so there is no saved sign-in to renew."
    ),
    "exited": "The login command stopped before the sign-in was finished.",
    "unconfirmed": "The login command finished, but the sign-in still does not read as valid.",
    "shutdown": "The desk restarted, which stopped the sign-in. Start it again.",
}

TAILSCALE_ADMIN = "https://login.tailscale.com/admin/machines"
TAILSCALE_WHY = (
    "Renew is not offered here: this page reaches you through this PC's Tailscale, and "
    "signing it in again would cut that link. In Tailscale's admin page, open this PC's "
    "menu and choose Disable key expiry."
)
TAILSCALE_NO_EXPIRY = "Nothing to renew: this PC's key does not expire."
GITHUB_ENV_WHY = (
    "Renew is not offered: this PC signs in to GitHub with an access token set in its "
    "environment, so there is no saved sign-in to renew."
)

# ---- reading a login command's output: pure, never raising ---------------------------------

_OSC8_RE = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]*)(?:\x07|\x1b\\)")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(r"\x1b[()*+][0-9A-Za-z]|\x1b[@-Z\\^_=>78]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_URL_RE = re.compile(r"https://\S+(?=\s)")
_LINK_CHARS_RE = re.compile(r"https://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_GH_CODE_RE = re.compile(r"one-time code:\s*([A-Z0-9]{4}-[A-Z0-9]{4})(?=\s)")
_GH_ENTER_RE = re.compile(r"Press Enter to open\b[^\n]*\bin your browser")
_CODE_RE = re.compile(rf"[!-~]{{1,{MAX_CODE}}}")  # visible ASCII only: no space, no control
_TOKEN_RE = re.compile(rf"[A-Za-z0-9_-]{{1,{MAX_TOKEN}}}")

CLAUDE_DOMAINS = ("claude.ai", "claude.com", "anthropic.com")
GITHUB_DOMAINS = ("github.com",)


def clean(raw: str) -> tuple[str, tuple[str, ...]]:
    """What a person would read on the terminal, plus the targets of any terminal hyperlinks.

    Colour and cursor sequences are dropped. Text after an escape sequence that has not
    finished arriving is left for the next read, so a link is never taken half-printed.
    """
    targets = tuple(t for t in _OSC8_RE.findall(raw) if t)
    text = _ESC_RE.sub("", _CSI_RE.sub("", _OSC_RE.sub("", raw)))
    cut = text.find("\x1b")
    if cut >= 0:
        text = text[:cut]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL_RE.sub("", text), targets


def safe_link(
    url: str, domains: Sequence[str], *, subdomains: bool = True, path: str = ""
) -> str | None:
    """``url`` when it is an https link on one of the vendor's own domains, else None."""
    if len(url) > MAX_LINK or not _LINK_CHARS_RE.fullmatch(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443):
        return None
    if parts.username is not None or parts.password is not None:
        return None
    on_domain = any(host == d or (subdomains and host.endswith("." + d)) for d in domains)
    return url if on_domain and path in parts.path else None


def _candidates(text: str, targets: Sequence[str]) -> list[str]:
    """Links in the order a reader meets them: the printed ones, then hyperlink targets.

    A printed link counts only once white space follows it, which is how its end is known.
    """
    return [*(m.group(0).rstrip(".,;)") for m in _URL_RE.finditer(text)), *targets]


@dataclass(frozen=True)
class Parsed:
    link: str | None = None
    code: str | None = None  # a one-time code the vendor prints, to be typed at the link
    press_enter: bool = False  # the command is waiting for Enter before it goes on


def parse_claude(text: str, targets: Sequence[str] = ()) -> Parsed:
    """The sign-in link from ``claude auth login``: an authorize page on Claude's own domains."""
    for url in _candidates(text, targets):
        link = safe_link(url, CLAUDE_DOMAINS, path="/oauth/")
        if link is not None:
            return Parsed(link=link)
    return Parsed()


def parse_github(text: str, targets: Sequence[str] = ()) -> Parsed:
    """The device page and one-time code from ``gh auth login --web``."""
    found = _GH_CODE_RE.search(text)
    link = None
    for url in _candidates(text, targets):
        link = safe_link(url, GITHUB_DOMAINS, subdomains=False, path="/login/device")
        if link is not None:
            break
    return Parsed(link, found.group(1) if found else None, bool(_GH_ENTER_RE.search(text)))


@dataclass(frozen=True)
class Marker:
    """Words a login command prints that say how it is going."""

    pattern: re.Pattern[str]
    reason: str = ""  # the reason to give if the command then ends in failure
    retry: bool = False  # the command turned a pasted code down and is waiting for another


@dataclass(frozen=True)
class Provider:
    """One sign-in the desk can renew. Everything a flow runs comes from this record."""

    key: str
    label: str
    binary: Callable[[GatewaySettings], Path | None]
    args: tuple[str, ...]  # fixed: nothing from a request is ever added
    env: tuple[tuple[str, str], ...]
    parse: Callable[[str, Sequence[str]], Parsed]
    takes_code: bool  # the vendor gives a code that is pasted back here
    shows_code: bool  # the command prints a one-time code that is typed at the vendor
    markers: tuple[Marker, ...]
    confirm: str  # what the confirm step says before anything runs

    def ready(self, link: str | None, code: str | None) -> bool:
        return link is not None and (code is not None or not self.shows_code)


_PLAIN = (("NO_COLOR", "1"), ("TERM", "dumb"), ("COLUMNS", str(PTY_COLS)), ("LINES", "50"))
_ENDS = "It stops by itself after {limit}."

CLAUDE = Provider(
    key="claude",
    label="Claude",
    binary=lambda s: s.claude,
    args=("auth", "login"),
    env=_PLAIN,
    parse=parse_claude,
    takes_code=True,
    shows_code=False,
    markers=(
        Marker(re.compile(r"invalid code", re.I), retry=True),
        Marker(re.compile(r"login failed", re.I), reason="refused"),
    ),
    confirm=(
        "This runs Claude's own login command on the PC and shows its sign-in link on this "
        "page only. You sign in at Claude, then paste the code it gives you back here. " + _ENDS
    ),
)
GITHUB = Provider(
    key="github",
    label="GitHub",
    binary=lambda s: s.gh,
    # No --git-protocol: the PC's git settings stay as they are.
    args=("auth", "login", "--hostname", "github.com", "--web"),
    # Prompts off: gh prints the link and waits, without asking questions or opening a browser.
    env=(*_PLAIN, ("GH_PROMPT_DISABLED", "1"), ("GH_NO_UPDATE_NOTIFIER", "1")),
    parse=parse_github,
    takes_code=False,
    shows_code=True,
    markers=(
        Marker(re.compile(r"is being used for authentication"), reason="env-token"),
        Marker(re.compile(r"expired_token|code (?:has )?expired", re.I), reason="code-expired"),
        Marker(re.compile(r"access_denied|authorization (?:was )?denied", re.I), reason="refused"),
    ),
    confirm=(
        "This runs GitHub's own login command on the PC and shows its link and one-time code "
        "on this page only. You enter the code at GitHub. " + _ENDS
    ),
)
PROVIDERS: Mapping[str, Provider] = {p.key: p for p in (CLAUDE, GITHUB)}

# ---- the panel's readings: state and expiry, never a token ---------------------------------

GH_STATUS_ARGS = ("auth", "status", "--hostname", "github.com", "--active", "--json", "hosts")
GH_ENV_SOURCES = frozenset(
    {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}
)
_STATE_WORD_RE = re.compile(r"[a-z][a-z_-]{0,23}")


@dataclass(frozen=True)
class GitHub:
    state: str  # gh's own word for the saved sign-in, or "none" when there is not one
    from_env: bool  # the token comes from the environment, not from a saved sign-in


def parse_github_status(out: str) -> GitHub | Unknown:
    """The state word of the active github.com sign-in from ``gh auth status --json hosts``.

    The account name and scopes the same output carries are never read.
    """
    try:
        data = json.loads(out)
    except ValueError:
        return Unknown(machine.NOT_UNDERSTOOD)
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, dict):
        return Unknown(machine.NOT_UNDERSTOOD)
    entries = hosts.get("github.com", [])
    if not isinstance(entries, list):
        return Unknown(machine.NOT_UNDERSTOOD)
    rows = [e for e in entries if isinstance(e, dict)]
    if not rows:
        return GitHub("none", False) if not entries else Unknown(machine.NOT_UNDERSTOOD)
    entry = next((e for e in rows if e.get("active") is True), rows[0])
    state, source = entry.get("state"), entry.get("tokenSource")
    if not isinstance(state, str) or not _STATE_WORD_RE.fullmatch(state):
        return Unknown(machine.NOT_UNDERSTOOD)
    return GitHub(state, isinstance(source, str) and source in GH_ENV_SOURCES)


@dataclass(frozen=True)
class States:
    claude: Reading[float | Unknown]  # the sign-in's expiry date
    github: Reading[GitHub | Unknown]
    tailscale: Reading[machine.Tailscale | Unknown]
    at: float


@dataclass(frozen=True)
class SignIn:
    """One row of the panel."""

    key: str
    label: str
    state: str  # ok, warn, down or unknown: the Machine page's words
    headline: str
    detail: str = ""
    moment: machine.Moment | None = None
    urgent: bool = False
    checked_at: float | None = None
    renewable: bool = False  # a Renew button is offered
    why_not: str = ""  # plain words when it is not
    help_label: str = ""  # a fixed link out, and what it is for
    help_url: str = ""
    reach: Light | None = None  # Claude only: can the service answer right now


@dataclass(frozen=True)
class Light:
    """The live service light on the Claude card: its colour, a short chip word and why."""

    light: str  # ok, down or unknown (green, red, grey)
    chip: str
    words: str


REACH_HINT = (
    "Asks Claude's public API address one unsigned question, at most once a minute while "
    "this page is open, and reads only how it answers. It shows whether Claude's service "
    "answers this PC; it cannot prove your own account's requests will succeed."
)
_REACH_CHIP = {reach.GREEN: "Answers", reach.RED: "Not answering", reach.GREY: "Unknown"}


def reach_light(result: reach.Reach | None, enabled: bool, signin_state: str) -> Light:
    """The Claude card's service light. A red light beside a sign-in that still reads as
    valid says so: the sign-in is fine, the service is not answering."""
    if not enabled:
        return Light(reach.GREY, "Off", "Not checked: the live check is turned off")
    if result is None:
        return Light(reach.GREY, "Unknown", "Checking Claude's service…")
    words = result.words
    if result.light == reach.RED and signin_state in (OK, WARN):
        words += " — your sign-in is fine"
    return Light(result.light, _REACH_CHIP.get(result.light, "Unknown"), words)


def _sentence(why: str) -> str:
    return why[:1].upper() + why[1:]


def _from_row(
    row: machine.Row, renewable: bool, why_not: str, help_label: str = "", help_url: str = ""
) -> SignIn:
    return SignIn(
        row.key,
        row.label,
        row.state,
        row.headline,
        row.detail,
        row.moment,
        row.urgent,
        row.checked_at,
        renewable,
        why_not,
        help_label,
        help_url,
    )


_CLAUDE_DETAIL = {
    OK: "Renew it here any time before it runs out.",
    WARN: "Running out. Renew it now.",
    DOWN: "No agent can work until it is renewed.",
}
_GITHUB = {
    "success": (
        OK,
        "Signed in",
        "GitHub sign-ins have no end date. Renew only if GitHub stops working.",
    ),
    "none": (DOWN, "Not signed in", "Renew signs this PC in to GitHub."),
    "error": (DOWN, "Not working", "GitHub turned the saved sign-in down. Renew it."),
    "timeout": (UNKNOWN, "Unknown", "GitHub did not answer in time"),
}


def _offer(provider: Provider, settings: GatewaySettings) -> tuple[bool, str]:
    """Whether Renew is offered for ``provider``, and the plain words when it is not."""
    if provider.binary(settings) is None:
        return False, f"Renew is not offered: the {provider.label} login command was not found."
    return True, ""


def claude_signin(
    reading: Reading[float | Unknown],
    now: float,
    settings: GatewaySettings,
    service: Service | None = None,
) -> SignIn:
    row = machine.claude_row(reading, now)
    row = replace(row, label="Claude", detail=_CLAUDE_DETAIL.get(row.state, row.detail))
    signin = _from_row(row, *_offer(CLAUDE, settings))
    return _with_reach(signin, service)


@dataclass(frozen=True)
class Service:
    """What the panel knows of Claude's service: the last fresh result, and whether the
    check is on at all."""

    result: reach.Reach | None
    enabled: bool


def _with_reach(row: SignIn, service: Service | None) -> SignIn:
    if service is None:
        return row
    return replace(row, reach=reach_light(service.result, service.enabled, row.state))


def github_signin(reading: Reading[GitHub | Unknown], settings: GatewaySettings) -> SignIn:
    g = reading.value
    if isinstance(g, Unknown):
        state, headline, detail = UNKNOWN, "Unknown", _sentence(g.why)
    else:
        unknown = (UNKNOWN, "Unknown", "GitHub reported a state not known here")
        state, headline, detail = _GITHUB.get(g.state, unknown)
    renewable, why_not = _offer(GITHUB, settings)
    if not isinstance(g, Unknown) and g.from_env:
        renewable, why_not = False, GITHUB_ENV_WHY
    row = machine.Row("github", "GitHub", state, headline, detail, reading.at)
    return _from_row(row, renewable, why_not)


def tailscale_signin(reading: Reading[machine.Tailscale | Unknown], now: float) -> SignIn:
    row = replace(machine.tailscale_row(reading, now), label="Tailscale, this PC")
    t = reading.value
    if not isinstance(t, Unknown) and t.backend == "Running" and t.key_expiry is None:
        return _from_row(row, False, TAILSCALE_NO_EXPIRY)
    return _from_row(row, False, TAILSCALE_WHY, "Open Tailscale's admin page", TAILSCALE_ADMIN)


ROWS = (("claude", "Claude"), ("github", "GitHub"), ("tailscale", "Tailscale, this PC"))


def build_rows(
    states: States | None,
    now: float,
    settings: GatewaySettings,
    service: Service | None = None,
) -> tuple[SignIn, ...]:
    """The panel's rows. Missing or old readings show as unknown, never as fine."""
    if states is None or now - states.at > machine.STALE_S:
        at = states.at if states is not None else None
        words = ("Checking", "Reading it again now") if at else ("Unknown", "Not checked yet")
        rows = [SignIn(key, label, UNKNOWN, *words, checked_at=at) for key, label in ROWS]
        rows[0] = _with_reach(rows[0], service)
        return tuple(rows)
    return (
        claude_signin(states.claude, now, settings, service),
        github_signin(states.github, settings),
        tailscale_signin(states.tailscale, now),
    )


# ---- a flow, as a page may see it ----------------------------------------------------------


@dataclass(frozen=True)
class FlowView:
    """What one page may see of the sign-in flow.

    ``link``, ``code`` and ``token`` are only ever set for the page that started the flow,
    and only while it runs.
    """

    provider: str = ""
    label: str = ""
    phase: str = IDLE
    mine: bool = False
    link: str | None = None
    code: str | None = None  # the vendor's one-time code, to type at the link
    takes_code: bool = False  # the vendor gives a code to paste back
    note: str = ""  # the latest plain-words event ("that code was not accepted")
    message: str = ""  # how it ended
    started_at: float | None = None
    ends_at: float | None = None  # the hard time-out
    ended_at: float | None = None
    expires: float | None = None  # the renewed sign-in's expiry date, once re-read
    token: str = ""  # for the starting page's own forms

    @property
    def active(self) -> bool:
        return self.phase in ACTIVE


@dataclass(frozen=True)
class Panel:
    rows: tuple[SignIn, ...]
    flow: FlowView  # the view any owner page gets: never a link or a code
    checked_at: float | None


@dataclass
class _Flow:
    provider: Provider
    token: str
    who: str
    started_at: float  # wall clock, for display
    deadline: float  # loop clock: the hard time-out
    read_deadline: float  # loop clock: a usable link must be read by then
    proc: asyncio.subprocess.Process
    fd: int  # the pseudo-terminal's desk side
    woke: asyncio.Event = field(default_factory=asyncio.Event)
    raw: bytearray = field(default_factory=bytearray)
    tail: str = ""
    link: str | None = None
    code: str | None = None
    note: str = ""
    reason: str = ""  # what the command's own words said went wrong
    code_sent: bool = False
    entered: bool = False
    stop: str = ""  # set to end it: "cancelled" or "shutdown"
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class _Result:
    provider: Provider
    token: str
    outcome: str  # DONE_OK, DONE_FAILED or DONE_CANCELLED
    message: str
    ended_at: float
    expires: float | None


class SignInService:
    """The panel's readings and the one sign-in flow that may run."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        timeout_s: float = FLOW_TIMEOUT_S,
        read_window_s: float = READ_WINDOW_S,
        start_limit: int = START_LIMIT,
        start_window_s: float = START_WINDOW_S,
        reach_check: reach.ReachCheck | None = None,
    ) -> None:
        self.settings = settings
        url = settings.claude_reach
        self.reach = reach_check or reach.ReachCheck(reach.http_probe(url) if url else None)
        self.providers = PROVIDERS
        self._timeout_s = timeout_s
        self._read_window_s = read_window_s
        self._start_limit = start_limit
        self._start_window_s = start_window_s
        self._lock = asyncio.Lock()
        self._flow: _Flow | None = None
        self._last: _Result | None = None
        self._spent: dict[str, float] = {}  # request ids that already started a flow
        self._states: States | None = None
        self._round: asyncio.Task[States] | None = None

    # ---- readings ---------------------------------------------------------------------

    @property
    def audit_path(self) -> Path:
        return self.settings.data_dir / "signin-audit.log"

    @property
    def _starts_path(self) -> Path:
        return self.settings.data_dir / "signin-starts"

    async def _ask[T](
        self, binary: Path | None, missing: str, args: Sequence[str], parse: Callable[[str], T]
    ) -> T | Unknown:
        if binary is None:
            return Unknown(missing)
        out = await machine.run([str(binary), *args], STATE_TIMEOUT_S)
        return out if isinstance(out, Unknown) else parse(out)

    async def _read_claude(self) -> Reading[float | Unknown]:
        path = self.settings.claude_credentials
        return Reading(await asyncio.to_thread(machine.read_claude_expiry, path), time.time())

    async def _read_github(self) -> Reading[GitHub | Unknown]:
        value = await self._ask(
            self.settings.gh, "the gh command was not found", GH_STATUS_ARGS, parse_github_status
        )
        return Reading(value, time.time())

    async def _read_tailscale(self) -> Reading[machine.Tailscale | Unknown]:
        value = await self._ask(
            self.settings.tailscale,
            "Tailscale was not found",
            ("status", "--json"),
            machine.parse_tailscale,
        )
        return Reading(value, time.time())

    async def _read_all(self) -> States:
        claude, github, tailscale = await asyncio.gather(
            self._read_claude(), self._read_github(), self._read_tailscale()
        )
        self._states = States(claude, github, tailscale, time.time())
        return self._states

    def peek(self) -> States | None:
        """The last readings; a missing or old set starts one read behind the answer."""
        last = self._states
        stale = last is None or time.time() - last.at > STATE_TTL_S
        if stale and (self._round is None or self._round.done()):
            with contextlib.suppress(RuntimeError):  # no running loop: caller is not async
                self._round = asyncio.get_running_loop().create_task(self._read_all())
        return last

    async def panel(self, wait_s: float = FIRST_WAIT_S) -> Panel:
        """The page's rows and the flow as any owner page may see it. Waits only for the
        very first readings, and then only ``wait_s``."""
        states = self.peek()

        async def first_states() -> States | None:
            if states is None and self._round is not None and wait_s > 0:
                with contextlib.suppress(TimeoutError):
                    return await asyncio.wait_for(asyncio.shield(self._round), wait_s)
            return states

        states, light = await asyncio.gather(first_states(), self.reach.light(wait_s))
        service = Service(light, self.reach.enabled)
        rows = build_rows(states, time.time(), self.settings, service)
        return Panel(rows, self.view(), states.at if states else None)

    async def _reread(self, key: str) -> SignIn | None:
        """One sign-in's row, read again now (after a flow, to report what it changed)."""
        base = self._states
        try:
            if key == "claude":
                claude = await asyncio.wait_for(self._read_claude(), STATE_TIMEOUT_S + 1)
                if base is not None:
                    self._states = replace(base, claude=claude)
                return claude_signin(claude, time.time(), self.settings)
            if key == "github":
                github = await asyncio.wait_for(self._read_github(), STATE_TIMEOUT_S + 1)
                if base is not None:
                    self._states = replace(base, github=github)
                return github_signin(github, self.settings)
        except TimeoutError:
            pass
        return None

    # ---- the audit log and the start limit ---------------------------------------------

    def _audit(self, who: str, provider: str, event: str, reason: str = "") -> None:
        """Who, which sign-in, what happened and when. Only fixed words are ever written."""
        name = "".join(c for c in who if c.isprintable() and not c.isspace())[:80] or "-"
        extra = {"reason": reason} if reason else {}
        actions.audit("signin", provider, event, who=name, **extra)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"{stamp} who={name} via=desk provider={provider} event={event}"
        line += f" reason={reason}\n" if reason else "\n"
        try:
            append_private(self.audit_path, line)
        except OSError:
            pass  # an unwritable log must not fail a sign-in; the journal line still went out

    def _claim(self, now: float) -> float:
        """Count one start attempt, across restarts. 0 when allowed, else seconds to wait."""
        return claim_slot(self._starts_path, now, self._start_limit, self._start_window_s)

    # ---- views ------------------------------------------------------------------------

    @staticmethod
    def _holds(token: str, real: str) -> bool:
        return bool(_TOKEN_RE.fullmatch(token)) and hmac.compare_digest(token, real)

    def view(self, token: str = "") -> FlowView:
        """The flow as the caller may see it: everything for the page that started it,
        only "a sign-in is running" for any other."""
        flow = self._flow
        if flow is not None:
            return self._view(flow, self._holds(token, flow.token))
        last = self._last
        if last is None or time.time() - last.ended_at > RESULT_TTL_S:
            return FlowView()
        return FlowView(
            provider=last.provider.key,
            label=last.provider.label,
            phase=last.outcome,
            mine=self._holds(token, last.token),
            message=last.message,
            ended_at=last.ended_at,
            expires=last.expires,
        )

    def _view(self, flow: _Flow, mine: bool) -> FlowView:
        p = flow.provider
        if flow.stop:
            phase = STOPPING
        elif flow.proc.returncode is not None or flow.code_sent:
            phase = CHECKING
        elif p.ready(flow.link, flow.code):
            phase = WAITING
        else:
            phase = STARTING
        shown = mine and phase == WAITING
        return FlowView(
            provider=p.key,
            label=p.label,
            phase=phase,
            mine=mine,
            link=flow.link if shown else None,
            code=flow.code if shown else None,
            takes_code=p.takes_code and shown,
            note=flow.note if mine else "",
            started_at=flow.started_at,
            ends_at=flow.started_at + self._timeout_s,
            token=flow.token if mine else "",
        )

    def _mine(self, token: str) -> _Flow:
        flow = self._flow
        if flow is None:
            raise ActionError("No sign-in is running.", 409)
        if not self._holds(token, flow.token):
            raise ActionError("This page did not start the sign-in, so it cannot act on it.", 409)
        return flow

    # ---- the flow ---------------------------------------------------------------------

    def confirm_text(self, provider: Provider) -> str:
        return provider.confirm.format(limit=machine.span(self._timeout_s))

    async def start(self, key: str, rid: str, who: str) -> FlowView:
        """Start ``key``'s login command. The returned view carries the starting page's token."""
        provider = self.providers.get(key)
        if provider is None:
            raise ActionError("Unknown sign-in.", 404)
        async with self._lock:
            now = time.time()
            self._spent = {k: v for k, v in self._spent.items() if v > now}
            if rid in self._spent:
                raise ActionError("That request was already used. Tap Renew again.", 409)
            if self._flow is not None:
                label = self._flow.provider.label
                raise ActionError(f"A {label} sign-in is already running. Cancel it first.", 409)
            binary = provider.binary(self.settings)
            if binary is None or not os.access(binary, os.X_OK):
                self._audit(who, key, "refused", "missing")
                raise ActionError(REASONS["missing"], 503)
            try:
                wait = await asyncio.to_thread(self._claim, now)
            except OSError as exc:
                raise ActionError(
                    "The desk could not record the attempt, so nothing was started.", 503
                ) from exc
            if wait > 0:
                self._audit(who, key, "refused", "rate-limited")
                raise ActionError(
                    f"Too many sign-in attempts. Try again in {machine.span(wait + 59)}.", 429
                )
            self._spent[rid] = now + actions.STEP_UP_TTL_S
            try:
                flow = await self._spawn(provider, binary, who)
            except (OSError, ValueError) as exc:
                self._audit(who, key, "failed", "not-started")
                raise ActionError(REASONS["not-started"], 502) from exc
            self._flow, self._last = flow, None
            self._audit(who, key, "started")
            flow.task = asyncio.get_running_loop().create_task(self._run(flow))
            return self._view(flow, True)

    async def _spawn(self, provider: Provider, binary: Path, who: str) -> _Flow:
        loop = asyncio.get_running_loop()
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, PTY_COLS, 0, 0))
            _echo_off(slave)
            os.set_blocking(master, False)
            proc = await asyncio.create_subprocess_exec(
                str(binary),
                *provider.args,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,  # its own process group, so all of it can be stopped
                cwd=str(Path.home()),
                env={**os.environ, **dict(provider.env)},
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        now = loop.time()
        flow = _Flow(
            provider=provider,
            token=secrets.token_urlsafe(32),
            who=who,
            started_at=time.time(),
            deadline=now + self._timeout_s,
            read_deadline=now + self._read_window_s,
            proc=proc,
            fd=master,
        )
        loop.add_reader(master, self._readable, flow)
        return flow

    def _readable(self, flow: _Flow) -> None:
        try:
            data = os.read(flow.fd, 65536)
        except BlockingIOError:
            return
        except OSError:  # the command closed its side of the terminal
            data = b""
        if data:
            self._take(flow, data)
        else:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(flow.fd)
        flow.woke.set()

    def _take(self, flow: _Flow, data: bytes) -> None:
        """Keep output only as long as it is needed: the start of it until the link is read,
        and a short tail for the command's own success and failure words."""
        limit = MUTED_TAIL_CHARS if flow.code_sent else TAIL_CHARS
        flow.tail = (flow.tail + clean(data.decode("utf-8", "replace"))[0])[-limit:]
        if flow.code_sent or flow.provider.ready(flow.link, flow.code):
            return
        room = MAX_OUTPUT - len(flow.raw)
        if room > 0:
            flow.raw += data[:room]

    def _digest(self, flow: _Flow) -> None:
        p = flow.provider
        if flow.raw and not p.ready(flow.link, flow.code):
            text, targets = clean(flow.raw.decode("utf-8", "replace"))
            parsed = p.parse(text, targets)
            if parsed.press_enter and not flow.entered:
                flow.entered = True
                with contextlib.suppress(OSError):
                    os.write(flow.fd, b"\r")
            flow.link = flow.link or parsed.link
            flow.code = flow.code or parsed.code
            if p.ready(flow.link, flow.code):
                flow.raw.clear()  # the link is read: none of the output is kept
        for marker in p.markers:
            if not marker.pattern.search(flow.tail):
                continue
            flow.tail = ""
            if marker.retry:
                flow.code_sent = False
                flow.note = "That code was not accepted. Paste the whole code again."
            else:
                flow.reason = marker.reason

    async def _run(self, flow: _Flow) -> None:
        loop = asyncio.get_running_loop()
        exited = asyncio.ensure_future(flow.proc.wait())
        exited.add_done_callback(lambda _f: flow.woke.set())
        reason = ""
        try:
            while True:
                flow.woke.clear()
                self._digest(flow)
                if flow.stop:
                    reason = flow.stop
                    break
                if exited.done():
                    break
                ready = flow.provider.ready(flow.link, flow.code)
                limit = flow.deadline if ready else min(flow.deadline, flow.read_deadline)
                now = loop.time()
                if now >= limit:
                    reason = "timed-out" if now >= flow.deadline else "not-understood"
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(flow.woke.wait(), limit - now)
        except asyncio.CancelledError:
            reason = "shutdown"
        finally:
            await self._finish(flow, exited, reason)

    async def _finish(self, flow: _Flow, exited: asyncio.Future[int], reason: str) -> None:
        loop = asyncio.get_running_loop()
        with contextlib.suppress(Exception):
            loop.remove_reader(flow.fd)
        if flow.proc.returncode is None:
            _signal_group(flow.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(exited), KILL_GRACE_S)
            except (TimeoutError, asyncio.CancelledError):
                _signal_group(flow.proc.pid, signal.SIGKILL)
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(exited), KILL_GRACE_S)
        else:  # it ended by itself: read its last words
            for _ in range(64):
                try:
                    data = os.read(flow.fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self._take(flow, data)
            self._digest(flow)
        # Forget the link, the one-time code and everything the command printed.
        flow.link = flow.code = None
        flow.raw.clear()
        flow.tail = flow.note = ""
        with contextlib.suppress(OSError):
            os.close(flow.fd)
        key, expires = flow.provider.key, None
        if reason == "cancelled":
            outcome = DONE_CANCELLED
        elif reason or flow.proc.returncode != 0:
            outcome, reason = DONE_FAILED, reason or flow.reason or "exited"
        else:
            outcome = DONE_OK
            row = await self._reread(key)
            if row is not None and row.state == DOWN:
                outcome, reason = DONE_FAILED, "unconfirmed"
            elif row is not None and row.state in (OK, WARN) and row.moment is not None:
                expires = row.moment.at
        if outcome == DONE_OK:
            message = f"{flow.provider.label} is signed in."
        elif reason == "timed-out":
            limit = machine.span(self._timeout_s)
            message = f"The sign-in was not finished in {limit}, so it was stopped. Start it again."
        else:
            message = REASONS.get(reason, REASONS["exited"])
        self._last = _Result(flow.provider, flow.token, outcome, message, time.time(), expires)
        if self._flow is flow:
            self._flow = None
        self._audit(flow.who, key, outcome, "" if outcome == DONE_OK else reason)

    async def send_code(self, token: str, code: str) -> FlowView:
        """Hand a pasted code to the login command's terminal. It is kept nowhere else."""
        flow = self._mine(token)
        if not flow.provider.takes_code:
            raise ActionError("This sign-in does not take a code.", 409)
        if flow.stop or flow.proc.returncode is not None or flow.link is None:
            raise ActionError("The sign-in is not waiting for a code.", 409)
        if flow.code_sent:
            raise ActionError("A code is already being checked.", 409)
        code = code.strip()
        if not _CODE_RE.fullmatch(code):
            raise ActionError(
                "That does not look like a sign-in code. Paste it exactly as shown, "
                "with no spaces.",
                400,
            )
        # From here on nothing the command prints is kept: it may print the code back.
        flow.code_sent, flow.note, flow.tail = True, "", ""
        flow.raw.clear()
        try:
            _echo_off(flow.fd)
            _write_all(flow.fd, code.encode("ascii") + b"\r")
        except OSError as exc:
            flow.code_sent = False
            raise ActionError("The login command is no longer listening.", 409) from exc
        return self._view(flow, True)

    async def cancel(self, token: str, who: str) -> FlowView:
        """Stop the running flow. Any owner page may: a lost page must not block the next
        sign-in until the time-out. Its link and code are forgotten at once."""
        flow = self._flow
        if flow is not None and not flow.stop:
            flow.stop = "cancelled"
            flow.who = who
            flow.link = flow.code = None
            flow.raw.clear()
            flow.woke.set()
        if flow is not None and flow.task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(flow.task), actions.ACT_WAIT_S)
        return self.view(token)

    async def stop(self) -> None:
        """Shutdown: end any running flow and any state read."""
        flow = self._flow
        if flow is not None and flow.task is not None:
            flow.stop = flow.stop or "shutdown"
            flow.woke.set()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(flow.task, 2 * KILL_GRACE_S + 1)
        if self._round is not None and not self._round.done():
            self._round.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._round
        await self.reach.stop()


def _echo_off(fd: int) -> None:
    """Stop the terminal repeating what is typed into it, so a pasted code is not echoed."""
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ECHONL)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)
