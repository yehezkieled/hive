"""The Sign-ins page: each sign-in firstmate depends on, and the page a renewal runs on."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape as esc
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from hive.gateway import signins
from hive.gateway.pages.machine import _MSTATE, _mchip, _moment, _mstate
from hive.gateway.pages.shell import Ctx, _iso, _page, _sc

SIGNINS_REFRESH_MS = 30000  # an open Sign-ins page re-reads its rows this often


def _signin_poll(ctx: Ctx, token: str, visible: bool) -> str:
    """The form the page asks "how is the sign-in going" with. The starting page's token goes
    in the request body, never in a link. Hidden on the page the script drives."""
    button = "<button class='btn btn--s' type=submit>Check again</button>" if visible else ""
    return (
        f"<form id=signin-poll method=post action='/act/signin-flow'{'' if visible else ' hidden'}>"
        f'<input type=hidden name=csrf value="{esc(ctx.csrf)}">'
        "<input type=hidden name=next value='/signins'>"
        f'<input type=hidden name=flow value="{esc(token)}">{button}</form>'
    )


def _time_of_day(epoch: float, ctx: Ctx) -> str:
    """A time of day ("4:10 pm") for something minutes away, in the viewer's zone."""
    at = datetime.fromtimestamp(epoch, UTC)
    try:
        local = at.astimezone(ZoneInfo(ctx.tz))
    except Exception:  # unknown zone name or no tz database
        local = at
    shown = f"{local.hour % 12 or 12}:{local:%M} {local:%p}".lower()
    return f"<time class=reset datetime='{esc(at.isoformat())}'>{esc(shown)}</time>"


def signin_flow(view: signins.FlowView, ctx: Ctx) -> str:
    """The sign-in flow as one page may see it.

    The link, the one-time code and the page's token are in it only for the page that
    started the flow (``view.mine``) and only while it waits for the owner.
    """
    v, name = view, esc(view.label)
    if v.phase == signins.IDLE:
        return ""
    if not v.active:
        cls = {signins.DONE_OK: "sgf--ok", signins.DONE_FAILED: "sgf--failed"}.get(
            v.phase, "sgf--quiet"
        )
        when = f"<span class=mute>{ctx.time(_iso(v.ended_at))}</span>" if v.ended_at else ""
        good = (
            f" Good until {ctx.date(v.expires)}."
            if v.expires and v.phase == signins.DONE_OK
            else ""
        )
        chip = {signins.DONE_OK: _sc("ok", "Done"), signins.DONE_FAILED: _sc("down", "Failed")}
        return (
            f"<div class='sgf {cls}' role=status><div class=sgf__h><p class=sgf__title>{name} "
            f"sign-in</p>{chip.get(v.phase, '')}{when}</div><p>{esc(v.message)}{good}</p></div>"
        )
    cancel = ""
    if v.phase != signins.STOPPING:
        label = "Cancel" if v.mine else "Stop that sign-in"
        cancel = ctx.form(
            "signin-cancel",
            f"<button class='btn btn--s btn--sm' type=submit>{label}</button>",
            always=True,
            flow=v.token,
        )
    stops = f"<span class=meta>Stops by itself at {_time_of_day(v.ends_at, ctx)}.</span>"
    note = f"<p class=sgf__note role=alert>{esc(v.note)}</p>" if v.note else ""
    if not v.mine:
        body = (
            "<p>Its link is shown only on the page that started it. If that page is gone, "
            "stop this sign-in and start again.</p>"
        )
    elif v.phase == signins.STARTING:
        body = f"<p role=status>Starting the {name} login command…</p>"
    elif v.phase == signins.CHECKING:
        body = f"<p role=status>Checking with {name}…</p>"
    elif v.phase == signins.STOPPING or not v.link:
        body = "<p role=status>Stopping…</p>"
    else:
        host = esc(urlsplit(v.link).hostname or "")
        steps = (
            "<li><b>Open the sign-in page and sign in as yourself.</b>"
            f'<a class=sgf__link href="{esc(v.link)}" target=_blank rel="noopener noreferrer">'
            f"Open the {name} sign-in page ↗</a><span class=sgf__host>{host}</span></li>"
        )
        if v.code:
            steps += (
                "<li><b>Enter this one-time code there.</b>"
                f"<strong class=sgf__code>{esc(v.code)}</strong></li>"
                f"<li><b>Come back here.</b> This page shows when {name} has signed the PC in.</li>"
            )
        if v.takes_code:
            box = (
                "<input type=text name=code id=sgcode autocomplete=off autocapitalize=off "
                f"autocorrect=off spellcheck=false maxlength={signins.MAX_CODE} required>"
                "<button class='btn btn--s' type=submit>Send code</button>"
            )
            steps += (
                f"<li><label for=sgcode>{name} then shows a code. Paste it here.</label>"
                + ctx.form("signin-code", box, "sgf__paste", always=True, flow=v.token)
                + f"{note}</li>"  # what the vendor said about the last code, under the box
            )
            note = ""
        body = (
            f"<ol class=sgf__steps>{steps}</ol>"
            "<p class=meta>The link works once and is shown only on this page.</p>"
        )
    ends = f" data-ends='{int(v.ends_at * 1000)}'" if v.mine and v.ends_at else ""
    return (
        f"<div class='sgf sgf--live'{ends}><div class=sgf__h><p class=sgf__title>{name} sign-in</p>"
        f"{_sc('run', 'In progress')}{stops}{cancel}</div>{body}{note}</div>"
    )


def _sgrow(r: signins.SignIn, ctx: Ctx, busy: bool) -> str:
    """One sign-in as a card: its name and state chip, how long it has, then Renew or why not."""
    state = _mstate(r.state)
    cls = f"sgrow sgrow--{state}" + (" sgrow--urgent" if r.urgent else "")
    line = " · ".join(d for d in (esc(r.detail), _moment(r.moment, ctx)) if d)
    why, act = esc(r.why_not), ""
    if r.help_url:  # the board's order: the link, then why Renew is not here
        act = (
            f'<a class=lnk href="{esc(r.help_url)}" target=_blank rel="noopener noreferrer">'
            f"{esc(r.help_label)} ↗</a>"
        )
    if r.renewable and busy:
        why = "A sign-in is running. Renew is back when it ends."
    elif r.renewable:
        act = (
            ctx.form(
                "signin",
                "<button class='btn btn--s' type=submit>Renew</button>",
                always=True,
                provider=r.key,
            )
            + act
        )
    return (
        f"<li class='{cls}'><div class=sgrow__top><b>{esc(r.label)}</b>{_mchip(state)}</div>"
        f"<p class=sgrow__big>{esc(r.headline)}</p>"
        + (f"<p class=sgrow__d>{line}</p>" if line else "")
        + _sgreach(r.reach)
        + f"<div class=sgrow__act>{act}"
        + (f"<p class=sgrow__why>{why}</p>" if why else "")
        + "</div></li>"
    )


def _sgreach(light: signins.Light | None) -> str:
    """The Claude card's live service light: a chip and a few words; a tap (or a hover)
    says what the check is and what it cannot prove."""
    if light is None:
        return ""
    hint = esc(signins.REACH_HINT)
    return (
        f"<details class='sgreach sgreach--{_mstate(light.light)}'>"
        f"<summary title='{hint}'>{_mchip(light.light, light.chip)}"
        f"<span class=sgreach__w>{esc(light.words)}</span></summary>"
        f"<p class=sgreach__why>{hint}</p></details>"
    )


def _signin_box(view: signins.FlowView, ctx: Ctx) -> str:
    active = " data-active" if view.active else ""
    return f"<section id=signin-flow class=sgflow{active}>{signin_flow(view, ctx)}</section>"


def render_signins(panel: signins.Panel, ctx: Ctx) -> str:
    """Each sign-in's state and expiry, and Renew where the desk can do it.

    This page is what any owner page gets, so it never holds a sign-in link or code: those
    reach only the page that started the flow, through ``signin_flow``.
    """
    rows = "".join(_sgrow(r, ctx, panel.flow.active) for r in panel.rows)
    counts = {
        s: sum(_mstate(r.state) == s for r in panel.rows) for s in ("down", "warn", "unknown", "ok")
    }
    summary = "".join(f"<li>{_mchip(s, f'{n} {_MSTATE[s]}')}</li>" for s, n in counts.items() if n)
    stamp = (
        f"<span class=stamp>Checked {ctx.time(_iso(panel.checked_at))}</span>"
        if panel.checked_at
        else ""
    )
    return _page(
        "Sign-ins · Hive desk",
        "<div class=ph><h1>Sign-ins</h1>"
        f"<ul class=mach__sum aria-label='Sign-in summary'>{summary}</ul>"
        f"<span class=sub>Renew a sign-in here before it runs out.</span>{stamp}</div>"
        "<div class='pgb sgb'>"
        + _signin_box(panel.flow, ctx)
        + _signin_poll(ctx, "", visible=False)
        + f"<ul class=sg>{rows}</ul></div>",
        "signins",
        f" data-refresh={SIGNINS_REFRESH_MS} data-signins",
        ctx,
    )


def render_signin_flow(view: signins.FlowView, ctx: Ctx) -> str:
    """The answer to a sign-in action when the page script is not running: the same panel on
    a page of its own, with a button to ask again."""
    poll = _signin_poll(ctx, view.token, visible=True) if view.active else ""
    return _page(
        "Sign-ins · Hive desk",
        "<div class=ph><h1>Sign-ins</h1></div><div class='pgb sgb'>"
        + _signin_box(view, ctx)
        + poll
        + "<p><a href='/signins'>Back to Sign-ins</a></p></div>",
        "signins",
    )
