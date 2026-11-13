# ruff: noqa: E501  (inline HTML)
"""What every desk page is built from: the per-render context (``Ctx``), the top bar with
its firstmate and quota chips, the page frame (``_page``) and the small shared pieces
(state chips, tabs, the read-only banner). Names with a leading underscore are private to
the ``pages`` package, not to this module."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html import escape as esc
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hive.gateway import fmconfig
from hive.gateway.pages.script import SCRIPT
from hive.gateway.pages.style import CSS
from hive.gateway.quota import STALE_AFTER_S, Quota
from hive.gateway.settings import DEFAULT_BOARD_URL, DEFAULT_TZ
from hive.gateway.snapshot import Snapshot
from hive.gateway.wake import FirstmateStatus
from hive.gateway.watchdog import View

BOARD_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):4387/(session/[A-Za-z0-9_-]+)")


def _safe_url(url: str | None) -> str | None:
    if url and urlsplit(url).scheme in ("http", "https"):
        return url
    return None


class Ctx:
    """Per-render write context: CSRF token, whether writes are allowed, display settings."""

    def __init__(
        self,
        csrf: str,
        writable: bool = True,
        nxt: str = "/",
        board_url: str = DEFAULT_BOARD_URL,
        tz: str = DEFAULT_TZ,
        quota: Quota | None = None,
        descriptions: dict[str, str] | None = None,
        repos: dict[str, str] | None = None,
        can_close_reviews: bool = False,
        project_notes: dict[str, str] | None = None,
        firstmate: FirstmateStatus | None = None,
        wake_enabled: bool = False,
        watchdog: View | None = None,
        postures: fmconfig.Projects | None = None,
        terminal: bool = False,
    ) -> None:
        self.terminal = terminal  # the Terminal page is on: the firstmate chip links to it
        self.postures = postures  # firstmate's project registry; None: not read
        self.can_close_reviews = can_close_reviews  # lavish-axi is available to end a session
        self.csrf = csrf
        self.writable = writable
        self.nxt = nxt
        self.board_url = board_url.rstrip("/")
        self.tz = tz
        self.quota = quota  # None: quota-axi did not answer
        self.descriptions = descriptions or {}  # "project/item" -> cached agent description
        self.project_notes = project_notes or {}  # registry name -> one-line project description
        self.repos = repos or {}  # registry name -> GitHub repo name (when it differs)
        self.firstmate = firstmate  # cached liveness; None: not read yet
        self.watchdog = watchdog  # automatic recovery: switch, ladder, latest events
        self.wake_enabled = wake_enabled  # the wake form is shown (the action may still refuse)

    def show(self, name: str) -> str:
        """The project's display name: its GitHub repo name, else the registry name."""
        return self.repos.get(name) or name

    def name_html(self, name: str, breaks: bool = True) -> str:
        """Display name, plus the local registry name as a small secondary label if it differs."""
        shown = self.show(name)
        aka = (
            f"<small class=aka title='local clone name'>{esc(name)}</small>"
            if shown != name
            else ""
        )
        # a long repo name wraps only between its words, never mid-word; a one-line row
        # (breaks=False) takes no break points, which would wrap it even under nowrap
        text = re.sub(r"([_-])", r"\1<wbr>", esc(shown)) if breaks else esc(shown)
        return text + aka

    def form(
        self, action: str, inner: str, cls: str = "", always: bool = False, **hidden: str
    ) -> str:
        if not self.writable and not always:
            return ""
        fields = {"csrf": self.csrf, "next": self.nxt, **hidden}
        hid = "".join(
            f'<input type=hidden name={esc(k)} value="{esc(v)}">' for k, v in fields.items()
        )
        klass = f" class='{esc(cls)}'" if cls else ""
        return f"<form method=post action='/act/{esc(action)}'{klass}>{hid}{inner}</form>"

    def link(self, url: str | None) -> str | None:
        """An http(s) URL, with loopback Lavish board links pointed at the tailnet origin."""
        safe = _safe_url(url)
        return BOARD_RE.sub(lambda m: f"{self.board_url}/{m[1]}", safe) if safe else None

    def plain(self, value: str) -> str:
        """Escaped text with loopback board URLs shortened to 'board' (for inside a link)."""
        return esc(BOARD_RE.sub("board", value))

    def boards(self, value: str) -> list[str]:
        """The tailnet URL of every loopback board link in ``value``."""
        return [f"{self.board_url}/{m[1]}" for m in BOARD_RE.finditer(value)]

    def text(self, value: str) -> str:
        """Escaped text where loopback board URLs become 'Open board' links."""
        out, last = [], 0
        for m in BOARD_RE.finditer(value):
            out.append(esc(value[last : m.start()]))
            href = esc(f"{self.board_url}/{m[1]}")
            out.append(f'<a href="{href}" rel="noopener noreferrer">Open board</a>')
            last = m.end()
        out.append(esc(value[last:]))
        return "".join(out)

    def time(self, iso: str | None) -> str:
        """A ``<time>`` the page script rewrites to the viewer's zone; server text is the fallback."""
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            return esc(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        try:
            local = dt.astimezone(ZoneInfo(self.tz))
        except Exception:  # unknown zone name or no tz database
            local = dt
        shown = f"{local:%a} {local.day} {local:%b}, {local.hour % 12 or 12}:{local:%M} {local:%p}"
        return f"<time datetime='{esc(dt.isoformat())}'>{esc(shown)}</time>"

    def date(self, epoch: float | None) -> str:
        """A full date with its year ("21 Nov 2026, 4:51 PM"), for dates that may be far off.

        The page script re-renders it in the viewer's zone, like ``time``.
        """
        iso = _iso(epoch)
        if not iso:
            return ""
        local = datetime.fromisoformat(iso)
        try:
            local = local.astimezone(ZoneInfo(self.tz))
        except Exception:  # unknown zone name or no tz database
            pass
        shown = f"{local.day} {local:%b %Y}, {local.hour % 12 or 12}:{local:%M} {local:%p}"
        return f"<time class=date datetime='{esc(iso)}'>{esc(shown)}</time>"


def _resets(at: datetime | None, tz: str, now: datetime) -> str:
    """The reset as a ``<time>`` the page script shows in the viewer's zone; ``tz`` is the fallback."""
    if at is None:
        return "not started"
    if at <= now:
        return "now"
    try:
        zone = ZoneInfo(tz)
    except Exception:  # unknown zone name or no tz database
        zone = UTC
    local, today = at.astimezone(zone), now.astimezone(zone)
    clock = f"{local.hour % 12 or 12}:{local:%M} {local:%p}".lower()
    shown = clock if local.date() == today.date() else f"{local:%a} {clock}"
    return f"resets <time class=reset datetime='{esc(at.isoformat())}'>{esc(shown)}</time>"


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min ago" if minutes < 60 else f"{minutes // 60} h ago"


def _chip(ctx: Ctx, now: datetime | None = None) -> str:
    """The ambient quota chip: busiest window as a percent used; tap shows both windows."""
    now = now or datetime.now(UTC)
    q = ctx.quota.current(now) if ctx.quota else None
    if q is None:
        chip = (
            "<summary class='qchip qchip--unknown' aria-label='Plan quota unknown'>"
            "<span class=qchip__bar><i style='width:0%'></i></span>—</summary>"
        )
        rows = "<div><span>Plan quota unknown</span></div>"
    else:
        worst = q.worst
        chip = (
            f"<summary class='qchip qchip--{q.level}' "
            f"aria-label='Plan quota, busiest window {worst.used} percent used'>"
            f"<span class=qchip__bar><i style='width:{worst.used}%'></i></span><span>{worst.used}%"
            "<span class=tchip__l> used</span></span></summary>"
        )
        rows = "".join(
            f"<div><span>{esc(w.label)}</span><b>{w.used}% used · {_resets(w.resets_at, ctx.tz, now)}"
            "</b></div>"
            for w in q.windows
        )
        if q.as_of is not None and (now - q.as_of).total_seconds() > STALE_AFTER_S:
            rows += (
                "<div class=qpop__note><span>Claude Code figures from "
                f"<time datetime='{esc(q.as_of.isoformat())}'>{_age(now - q.as_of)}</time>"
                "</span></div>"
            )
    return (
        f"<details class=qwrap id=qchip>{chip}<div class=qpop><h3>Plan quota</h3>{rows}"
        "<div class=qpop__note><span>account-wide · shared with your own use</span></div>"
        "<section class=qpop__alerts aria-labelledby=qpop-alerts-h>"
        "<h3 id=qpop-alerts-h>Get alerts</h3>"
        "<p>On iPhone or iPad (iOS 16.4 and up):</p>"
        "<ol><li>Tap Share in Safari.</li><li>Tap Add to Home Screen.</li>"
        "<li>Open the desk from its Home Screen icon, then tap Alerts.</li></ol>"
        "<p id=alerts-note role=status></p></section>"
        "</div></details>"
    )


def _watchdog_panel(ctx: Ctx) -> str:
    """Automatic recovery: its switch (owner-only, like Wake), where the ladder stands and the
    latest events. Shown even while the snapshot is unreadable, like the wake forms."""
    wd = ctx.watchdog
    if wd is None or not ctx.wake_enabled:
        return ""
    if wd.enabled:
        button = (
            "<button name=to value=off class='btn btn--s btn--sm' type=submit>Turn off</button>"
        )
    else:
        button = "<button name=to value=on class='btn btn--s btn--sm' type=submit>Turn on</button>"
    form = ctx.form("watchdog", button, always=True)
    state = f"Automatic recovery: {esc(wd.label)}"
    if wd.config_error:
        state += f" · {esc(wd.config_error)}"
    elif wd.halted:
        state += f" · {esc(wd.halted_reason)}"
    elif wd.enabled and wd.stage:
        state += f" · rung {wd.stage} of 2 taken"
    rows = "".join(
        f"<li><time datetime='{esc(_iso(e.get('at')))}'>{esc(_clock_hm(e.get('at'), ctx.tz))}"
        f"</time> {esc(str(e.get('text', '')))}</li>"
        for e in wd.events
    )
    log = f"<ul class=wdlog>{rows}</ul>" if rows else ""
    return f"<section class=wdpanel><p>{state}</p>{form}{log}</section>"


def _iso(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _clock_hm(value: object, tz: str) -> str:
    try:
        when = datetime.fromtimestamp(float(value), UTC)  # type: ignore[arg-type]
        try:
            when = when.astimezone(ZoneInfo(tz))
        except (ZoneInfoNotFoundError, ValueError):
            pass
        return when.strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


_FM_DOT = {"alive": "ok", "no-beat": "warn", "down": "hot"}


def _fm_chip(ctx: Ctx) -> str:
    """Firstmate liveness from the cache, with Wake when it is down and Restart session when
    it runs with a silent watcher.

    The forms are shown even while the snapshot is unreadable (``always``): that is exactly
    when firstmate may be down. The action still re-checks and confirms before it runs.
    """
    fm = ctx.firstmate
    state = fm.state if fm else "unknown"
    label = fm.label if fm else "checking"
    detail = esc(fm.describe()) if fm else "Firstmate status has not been read yet."
    wake = ""
    if ctx.wake_enabled and fm is not None and fm.can_wake:
        wake = ctx.form(
            "wake",
            "<button class='btn btn--p' type=submit>Wake firstmate</button>",
            always=True,
        )
    elif ctx.wake_enabled and fm is not None and fm.can_restart:
        wake = ctx.form(
            "restart",
            "<button class='btn btn--p' type=submit>Restart session</button>",
            always=True,
        )
    if ctx.terminal:
        wake += (
            "<p class=fmpop__term><a class=lnk href='/terminal'>Open firstmate’s terminal</a></p>"
        )
    wake += _watchdog_panel(ctx)
    cls = _FM_DOT.get(state, "unknown")
    return (
        "<details class=qwrap id=fmchip>"
        f"<summary class='qchip qchip--{cls}' "
        f"aria-label='Firstmate {esc(label)}'>"
        f"<span class=dot></span><span><span class=tchip__l>firstmate · </span>{esc(label)}</span></summary>"
        f"<div class='qpop fmpop'><h3><span class='dot dot--{cls}'></span>Firstmate · {esc(label)}</h3>"
        f"<p>{detail}</p>{wake}</div></details>"
    )


# The hive mark (three cells) and the faint honeycomb behind every page, drawn inline: the CSP
# allows no image but the desk's own files, and an inline SVG is not an image fetch.
MARK = (
    "<svg viewBox='0 0 24 24' aria-hidden=true focusable=false><path d='M6.63 1.5 11.74 4.45v5.9"
    "L6.63 13.3 1.52 10.35v-5.9ZM17.37 1.5l5.11 2.95v5.9L17.37 13.3l-5.11-2.95v-5.9ZM12 10.8l5.11 "
    "2.95v5.9L12 22.6l-5.11-2.95v-5.9Z'/></svg>"
)


COMB = (
    "<svg class=comb aria-hidden=true focusable=false><defs><pattern id=comb width=46.77 height=81 "
    "patternUnits=userSpaceOnUse viewBox='0 0 31.18 54'><path fill=none stroke=currentColor "
    "d='M0-18 15.59-9v18L0 18-15.59 9V-9ZM31.18-18 46.77-9v18L31.18 18 15.59 9V-9ZM15.59 9 31.18 18v18"
    "L15.59 45 0 36V18ZM0 36l15.59 9v18L0 72-15.59 63V45ZM31.18 36l15.59 9v18L31.18 72 15.59 63V45Z'/>"
    "</pattern></defs><rect width=100% height=100% fill=url(#comb) /></svg>"
)


# The top bar's links, the same on every page: (``active`` name, href, label).
NAV = (
    ("desk", "/", "Desk"),
    ("chat", "/chat", "Chat"),
    ("config", "/config", "Config"),
    ("machine", "/machine", "Machine"),
    ("run", "/run", "Run"),
    ("terminal", "/terminal", "Terminal"),
    ("signins", "/signins", "Sign-ins"),
)


def _nav(active: str) -> str:
    links = "".join(
        f"<a href='{href}'{' aria-current=page' if name == active else ''}>{label}</a>"
        for name, href, label in NAV
    )
    return f"<nav aria-label=Pages>{links}</nav>"


def _page(
    title: str,
    body: str,
    active: str = "",
    attrs: str = "",
    ctx: Ctx | None = None,
    css: str = "",
    tail: str = "",
) -> str:
    chips = (_fm_chip(ctx) + _chip(ctx)) if ctx is not None else ""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=color-scheme content='light dark'>"
        # the phone's status bar takes the top bar's colour, in each theme
        "<meta name=theme-color media='(prefers-color-scheme: light)' content='#fffcf6'>"
        "<meta name=theme-color media='(prefers-color-scheme: dark)' content='#181512'>"
        "<meta name=mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-title content='Hive desk'>"
        "<link rel=manifest href='/manifest.webmanifest'>"
        "<link rel=icon href='/icons/icon-192.png'>"
        "<link rel=apple-touch-icon href='/icons/apple-touch-icon-180.png'>"
        f"<title>{esc(title)}</title><style>{CSS}{css}</style></head>"
        f"<body{attrs}{_BODY_CLASS.get(active, '')}>{COMB}<header class=chrome>"
        f"<a class=chrome__brand href='/' aria-label='Hive desk'>{MARK}<span>hive</span></a>"
        f"{_nav(active)}<div class=chrome__st>{chips}"
        "<button id=alerts class=qchip hidden type=button>Alerts</button></div>"
        f"</header><main>{body}</main><script>{SCRIPT}</script>{tail}</body></html>"
    )


_BODY_CLASS = {"desk": " class=wide", "chat": " class=chatpage", "terminal": " class=termpage"}


def _banner(snap: Snapshot, ctx: Ctx) -> str:
    stamp = f" Snapshot {ctx.time(snap.generated)}." if snap.generated else ""
    return (
        "<div class=banner role=status><strong>Read-only fallback.</strong> "
        f"{esc(snap.reason or 'No data.')}{stamp} Nothing here can be trusted until "
        "the desk and firstmate agree on the snapshot schema"
        f"{' (saw ' + esc(snap.schema) + ')' if snap.schema else ''}.</div>"
    )


def _sc(kind: str, word: str) -> str:
    """A state chip: a small hex in the state's colour and its word (never colour alone)."""
    return f"<span class='sc sc--{kind}'><i></i>{esc(word)}</span>"


def _tabs(kind: str, items: list[tuple[str, str, int | str | None]], current: str) -> str:
    """Phone (``p``) or right-column (``r``) tabs over a page's panels; the page script swaps
    which panel shows. The server marks the first panel shown."""
    buttons = "".join(
        f"<button type=button role=tab data-tab={key} "
        f"aria-selected={'true' if key == current else 'false'}>{esc(label)}"
        + (f" <small{' data-rvn' if key == 'reviews' else ''}>{n}</small>" if n is not None else "")
        + "</button>"
        for key, label, n in items
    )
    return f"<div class='tabs tabs--{kind}' role=tablist data-tabset={kind}>{buttons}</div>"
