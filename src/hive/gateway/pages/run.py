# ruff: noqa: E501  (inline HTML)
"""The Run page: the fixed fix buttons and the last runs."""

from __future__ import annotations

from html import escape as esc

from hive.gateway import machine, runqueue
from hive.gateway.pages.machine import _MCHIP
from hive.gateway.pages.shell import Ctx, _iso, _page, _sc, _tabs

RUN_REFRESH_MS = 30000  # only while the live stream is down; a run's start and end push a refresh


_RUN_RESULT = {
    runqueue.OK: ("ok", "ok", "OK"),
    runqueue.REFUSED: ("warn", "warn", "Refused"),
    runqueue.FAILED: ("down", "down", "Failed"),
    runqueue.TIMED_OUT: ("down", "down", "Timed out"),
    runqueue.RUNNING: ("unknown", "run", "Running"),
    runqueue.UNKNOWN: ("unknown", "unknown", "Unknown"),
}


def _secs(seconds: float | None) -> str:
    """A run's duration; each number keeps its unit on the same line (a no-break space)."""
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.1f}\u00a0s"
    if seconds < 120:
        return f"{int(seconds)}\u00a0s"
    return f"{int(seconds // 60)}\u00a0min {int(seconds % 60)}\u00a0s"


def _run_card(card: runqueue.Card, ctx: Ctx) -> str:
    """One fix button: what it does, how long it takes, the exact command or helper verb."""
    a = card.action
    cls = "ar" + (" ar--down" if a.down else "") + (" ar--off" if card.why_not else "")
    # off: a disabled button; its reason is said once by the Windows note when it is the same
    form = "<button class='btn btn--s' type=button disabled>Run</button>"
    off = card.why_not if card.why_not and card.why_not != runqueue.NOT_SET_UP else ""
    if not card.why_not:
        pick = ""
        if a.choice:
            fid = f"rq-{a.key}"
            opts = "".join(
                f"<option value={v}{' selected' if v == a.default else ''}>{esc(a.short(v))}</option>"
                for v in a.choices
            )
            pick = f"<label for={fid}>For</label><select id={fid} name=n>{opts}</select>"
        btn = "btn btn--s btn--danger" if a.down else "btn btn--s"
        form = ctx.form(
            "run", f"{pick}<button class='{btn}' type=submit>Run</button>", always=True, do=a.key
        )
    return (
        f"<li class='{cls}'><div class=ar__t><b>{esc(a.label)}</b>"
        f"<span class=meta>{esc(a.took)}</span></div>{form}"
        f"<p class=ar__d title='{esc(a.plain)}'>{esc(a.plain)}</p>"
        f"<code class=ar__cmd>{esc(a.command())}</code>"
        + (f"<p class=ar__off>{esc(off)}</p>" if off else "")
        + "</li>"
    )


def _run_row(r: runqueue.Run, owner: str, ctx: Ctx) -> str:
    row, chip, word = _RUN_RESULT.get(r.result, _RUN_RESULT[runqueue.UNKNOWN])
    who = "you" if r.who == owner else machine.redact(r.who or "-", 40)
    took = _secs(r.duration_s)
    line = " · ".join(
        x
        for x in (f"by {esc(who)}", ctx.time(_iso(r.start)), esc(r.detail), took and f"took {took}")
        if x
    )
    return (
        f"<li class='runrow runrow--{row}'>{_sc(_MCHIP.get(chip, chip), word)}"
        f"<b>{esc(r.label)}</b><span class=d>{line}</span></li>"
    )


def _helper_note(state: str) -> str:
    if state == runqueue.READY:
        return (
            "<p class='meta run__lead'>Through the admin helper only: each button writes one "
            "request line for it and starts its task, and its answer is shown below.</p>"
        )
    chip, more = (
        (
            "Not answered",
            "It has not answered the desk yet. If the sitting is done, tap Check the admin "
            "helper; the other Windows buttons are offered once it answers.",
        )
        if state == runqueue.UNPROVEN
        else (
            "Not set up",
            "The one sitting at the PC sets it up. Until then no Windows button is offered.",
        )
    )
    return (
        f"<div class=note role=status>{_sc('warn', chip)}<span><b>{esc(runqueue.NOT_SET_UP)}</b> "
        f"{esc(more)} ({esc(runqueue.SITTING)})</span></div>"
    )


def render_run(view: runqueue.RunView, ctx: Ctx) -> str:
    """The Run page: fixed fix buttons, then the last runs. Nothing runs from a page load;
    each button needs a confirm step (two for the ones that take things down)."""
    linux = [c for c in view.cards if c.action.side == "linux"]
    windows = [c for c in view.cards if c.action.side == "windows"]
    fold = (
        f"<details class=fold><summary>Not offered · {len(view.not_offered)}</summary><ul>"
        + "".join(
            f"<li>{esc(label)}<span>{esc(why)}</span></li>" for label, why in view.not_offered
        )
        + "</ul></details>"
        if view.not_offered
        else ""
    )
    rows = "".join(_run_row(r, view.owner, ctx) for r in view.runs)
    runs = f"<ol class=runs>{rows}</ol>" if rows else "<p class=empty>No runs yet.</p>"
    return _page(
        "Run · Hive desk",
        "<div class=ph><h1>Run</h1><span class=sub>Fixed fixes only. Tap, check what runs, tap "
        "again. Every run is logged.</span></div><div class='pgb run' data-tabs>"
        + _tabs(
            "p",
            [
                ("linux", "Linux", len(linux)),
                ("win", "Windows", len(windows)),
                ("runs", "Runs", None),
            ],
            "linux",
        )
        + "<section class='pn is-on' data-tp=linux><div class=pn__h><h2>This desk · Linux</h2>"
        "<span class=meta>runs as you, never as root</span></div>"
        f"<div class=pn__b><ul class=ars>{''.join(_run_card(c, ctx) for c in linux)}</ul></div></section>"
        "<section class=pn data-tp=win><div class=pn__h><h2>The PC · Windows</h2></div>"
        f"<div class=pn__b>{_helper_note(view.helper)}"
        f"<ul class=ars>{''.join(_run_card(c, ctx) for c in windows)}</ul>{fold}</div></section>"
        f"<section class=pn data-tp=runs><div class=pn__h><h2>Last {runqueue.SHOWN_RUNS} runs</h2>"
        + (f"<span class='hexn hexn--q'>{len(view.runs)}</span>" if view.runs else "")
        + f"</div><div class=pn__b>{runs}</div></section></div>",
        "run",
        f" data-refresh={RUN_REFRESH_MS} data-run",
        ctx,
    )
