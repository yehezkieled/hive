# ruff: noqa: E501  (inline HTML)
"""The Machine page: the chain that has to hold and the upcoming risks, read-only. Its state
chips are shared with the other pages that show a machine reading."""

from __future__ import annotations

from html import escape as esc

from hive.gateway import machine
from hive.gateway.pages.shell import Ctx, _iso, _page, _sc, _tabs

MACHINE_REFRESH_MS = 30000  # an open Machine page re-reads this often; each read may start a round


_MSTATE = {"ok": "OK", "warn": "Warning", "down": "Down", "unknown": "Unknown"}


def _mstate(state: str) -> str:
    return state if state in _MSTATE else "unknown"


def _moment(m: machine.Moment | None, ctx: Ctx, lead: bool = False) -> str:
    """ "expires <date>", or "Expires <date>" when it opens the line."""
    if m is None:
        return ""
    label = m.label[:1].upper() + m.label[1:] if lead else m.label
    return f"{esc(label)} {ctx.date(m.at) if m.exact else ctx.time(_iso(m.at))}"


# a state as a chip colour and as the glyph in the chain's hex (the chip carries the word)
_MCHIP = {"ok": "ok", "warn": "warn", "down": "down", "unknown": "unk"}


_MGLYPH = {
    "ok": "M3 7.4 5.8 10 11 4",
    "warn": "M7 3.5v4.2M7 10.4v.2",
    "down": "M4.2 4.2l5.6 5.6M9.8 4.2l-5.6 5.6",
    "unknown": "M5 5.3a2 2 0 1 1 2.7 1.9c-.5.2-.7.5-.7 1v.3M7 10.4v.2",
}


def _mchip(state: str, word: str = "") -> str:
    state = _mstate(state)
    return _sc(_MCHIP[state], word or _MSTATE[state])


def _mrow(state: str, urgent: bool, label: str, head: str, detail: list[str]) -> str:
    """One link of the chain: a hex mark, its label and reading, then a state chip (a word,
    not colour alone)."""
    state = _mstate(state)
    cls = f"ch ch--{state}" + (" ch--urgent" if urgent else "")
    line = " · ".join(d for d in detail if d)
    return (
        f"<li class='{cls}'><span class=ch__n><svg viewBox='0 0 14 14' aria-hidden=true>"
        f"<path d='{_MGLYPH[state]}'/></svg></span><div class=ch__b><p class=ch__l><b>{esc(label)}</b>"
        + (f" · {esc(head)}" if head else "")
        + "</p>"
        + (f"<p class=ch__d>{line}</p>" if line else "")
        + f"</div>{_mchip(state)}</li>"
    )


def _timeline(view: machine.MachineView) -> str:
    """The dated risks as marks on a line from now, the red band's days shaded."""
    now = view.checked_at
    if now is None:
        return ""
    dated = [(k, max(0.0, (k.order - now) / 86400)) for k in view.risks if k.order != float("inf")]
    if not dated:
        return ""
    span = max(30.0, max(d for _, d in dated) * 1.08)

    def x(days: float) -> str:
        return f"{min(days, span) / span * 100:.2f}%"

    marks = "".join(
        f"<span class='tl__m tl__m--{_MCHIP[_mstate(k.state)]}' style='left:{x(d)}'></span>"
        f"<span class='tl__lab tl__lab--{'dn' if i % 2 else 'up'}"
        f"{' tl__lab--first' if d < span * 0.04 else ''}' style='left:{x(d)}'>"
        f"{'Now' if d < 1 else f'{int(d)} d'}</span>"
        for i, (k, d) in enumerate(dated)
    )
    return (
        "<div class=tl role=img aria-label='Upcoming risks by days from now'>"
        f"<div class=tl__ax><span class=tl__w style='width:{x(machine.URGENT_DAYS)}'></span></div>"
        f"{marks}</div>"
    )


def render_machine(view: machine.MachineView, ctx: Ctx) -> str:
    """The chain that has to hold and the upcoming risks, read-only.

    Every row always renders: a probe that could not answer shows ``unknown`` with a fixed
    reason, so the page itself never fails. It holds no form and no button.
    """
    rows = "".join(
        _mrow(
            r.state,
            r.urgent,
            r.label,
            r.headline,
            [
                esc(r.detail),
                _moment(r.moment, ctx),
                f"<span class=ch__at>checked {ctx.time(_iso(r.checked_at))}</span>"
                if r.checked_at and (view.checked_at is None or r.checked_at < view.checked_at - 60)
                else "",  # a reading older than the round says when it was taken
            ],
        )
        for r in view.rows
    )
    risks = "".join(
        f"<li class='risk risk--{_mstate(k.state)}{' risk--urgent' if k.urgent else ''}'>"
        f"{_mchip(k.state)}<b>{esc(k.label)}</b><span class=when>{esc(k.due)}</span>"
        + (
            f"<span class=d>{line}</span>"
            if (
                line := " · ".join(
                    d for d in (_moment(k.moment, ctx, lead=True), esc(k.detail)) if d
                )
            )
            else ""
        )
        + "</li>"
        for k in view.risks
    )
    counts = {
        s: sum(_mstate(r.state) == s for r in view.rows) for s in ("down", "warn", "unknown", "ok")
    }
    summary = "".join(f"<li>{_mchip(s, f'{n} {_MSTATE[s]}')}</li>" for s, n in counts.items() if n)
    worst = next((f"{n} {_MSTATE[s]}" for s, n in counts.items() if n), "")
    stamp = (
        f"<span class=stamp>Checked {ctx.time(_iso(view.checked_at))}</span>"
        if view.checked_at
        else ""
    )
    note = esc(view.note) or "Nothing to report."
    a = view.alarm
    alarm = (
        "<p class=sl>Gone-dark alarm</p><ol class='chain chain--one'>"
        + _mrow(a.state, False, a.label, a.headline, [esc(a.detail), _moment(a.moment, ctx)])
        + "</ol>"
        if a is not None
        else ""
    )
    return _page(
        "Machine · Hive desk",
        f"<div class=ph><h1>Machine</h1><ul class=mach__sum aria-label='Chain summary'>{summary}</ul>"
        "<span class=sub>Every link has to hold to reach firstmate. Renew a sign-in on the "
        f"<a href='/signins'>Sign-ins page</a>.</span>{stamp}"
        + (
            "<a class='lnk mach__term' href='/terminal'>Open firstmate’s terminal</a>"
            if ctx.terminal
            else ""
        )
        + "</div><div class='pgb mach' data-tabs>"
        + _tabs(
            "p",
            [("chain", "The chain", worst or None), ("risks", "Upcoming risks", len(view.risks))],
            "chain",
        )
        + "<section class='pn mach__col is-on' data-tp=chain><div class=pn__h><h2>The chain</h2>"
        "<span class=meta>Read-only</span></div>"
        f"<div class=pn__b><ol class=chain>{rows}</ol>{alarm}</div></section>"
        "<section class='pn mach__col' data-tp=risks><div class=pn__h><h2>Upcoming risks</h2>"
        + (f"<span class='hexn hexn--q'>{len(view.risks)}</span>" if view.risks else "")
        + "</div><div class=pn__b>"
        + (
            f"{_timeline(view)}<ul class=risks>{risks}</ul>"
            if risks
            else f"<p class=empty>{note}</p>"
        )
        + "</div></section></div>",
        "machine",
        f" data-refresh={MACHINE_REFRESH_MS} data-machine",
        ctx,
    )
