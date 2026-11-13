"""Needs-you items as cards: a decision's question, options and answer form, shared by the
Desk's lane and a project page."""

from __future__ import annotations

from html import escape as esc

from hive.gateway import fmconfig
from hive.gateway.actions import new_request_id
from hive.gateway.cards import parse_card
from hive.gateway.desk import NeedsYou, Row
from hive.gateway.pages.shell import Ctx, _sc


def _decision(text: str, ctx: Ctx) -> tuple[str, str, str]:
    """A decision text as card parts: its question (HTML), its options as big buttons with the
    recommended one marked (a tap fills the reply), and the full wording behind "Full text"."""
    card = parse_card(text)
    if not card.question:
        return "", "", ""
    opts = ""
    if card.options:
        opts = (
            "<div class=opts role=group aria-label='Answer options'>"
            + "".join(
                f"<button type=button class='opt{' is-rec' if o.recommended else ''}' "
                f"data-pick='{esc(o.letter)}' aria-pressed=false><b>{esc(o.letter)}</b>"
                f"<span>{ctx.plain(o.label)}"
                + ("<em>Recommended</em>" if o.recommended else "")
                + "</span></button>"
                for o in card.options
            )
            + "</div>"
        )
    full = (
        "<details class=nyc__full><summary class=lnk>Full text</summary>"
        f"<p>{ctx.plain(card.more)}</p></details>"
        if card.more
        else ""
    )
    return ctx.plain(card.question), opts, full


_NEED_LABEL = {"hold": "Question on hold", "decision": "Decision", "merge": "Merge approval"}


# Needs-you kinds: (row class, chip kind, chip word).
_WAIT = {
    "decision": ("nyi--decision", "you", "Decision"),
    "hold": ("nyi--gate", "you", "Waiting on you"),
    "merge": ("nyi--mode", "ok", "Merge"),
}


def _posture_tag(project: str, ctx: Ctx) -> str:
    """Firstmate's merge posture for the card's project, read-only; ``unknown`` says why."""
    if ctx.postures is None:
        return ""
    label = fmconfig.posture_label(ctx.postures, project)
    return f"Merge posture: {esc(label)} · <a href='/config'>config</a>"


def _links(links: list[tuple[str, str]], full: str = "") -> str:
    out = "".join(
        f'<a class=lnk href="{esc(url)}" rel="noopener noreferrer">{label}</a>'
        for url, label in links
    )
    return f"<div class=nyc__links>{out}{full}</div>" if out or full else ""


def _lines(n: NeedsYou, lead: str, ctx: Ctx) -> tuple[str, str, str, str]:
    """A needs-you item's card lines: (what happened, what it means, option buttons, full text).
    ``lead`` is the item's own title, if any; the question then says what it means."""
    question, opts, full = _decision(n.text, ctx)
    if lead and n.text and lead != n.text:
        what, mean = ctx.plain(lead), question
    else:
        what, mean = question or ctx.plain(lead or n.ref), ""
    posture = _posture_tag(n.project, ctx) if n.kind == "merge" else ""
    mean = "".join(f"<p class=nyc__mean>{m}</p>" for m in (mean, posture) if m)
    return what, mean, opts, full


def _need(n: NeedsYou, show_project: bool, ctx: Ctx) -> str:
    """A project page's decision card: what happened, what it means, the options and the answer
    form (the page's full form, with its release choice)."""
    links = [(b, "Open board ↗") for b in ctx.boards(n.text)]
    url = ctx.link(n.url)
    if url:
        links.append((url, "Open PR ↗"))
    title = n.title or n.ref.partition("/")[0]
    where = n.project if show_project and not title.lower().startswith(n.project.lower()) else ""
    lead = " · ".join(p for p in (where, title) if p)
    act = ""
    if n.kind == "hold":
        checked = " checked" if n.gated else ""
        act = ctx.form(
            "answer",
            "<label>Your answer</label><textarea name=text required maxlength=512></textarea>"
            f"<label><input type=checkbox name=release value=1{checked}> "
            "Release the work item (resume it) instead of closing the question</label>"
            "<button class='btn btn--p'>Record answer</button>",
            "nyc__form",
            task=n.ref,
            rid=new_request_id(),
        )
    elif n.kind == "decision":
        task, _, key = n.ref.partition("/")
        act = ctx.form(
            "decision",
            "<label>Your answer (sent to the first mate)</label>"
            "<textarea name=text required maxlength=4000></textarea>"
            "<button class='btn btn--p'>Send answer</button>",
            "nyc__form",
            task=task,
            key=key,
            rid=new_request_id(),
        )
    elif n.kind == "merge":
        act = ctx.form(
            "merge",
            "<button class='btn btn--p'>Give merge word…</button>",
            "nyc__form",
            task=n.ref,
            rid=new_request_id(),
        )
    what, mean, opts, full = _lines(n, lead, ctx)
    _, kind, word = _WAIT.get(n.kind, ("", "you", n.kind))
    return (
        f"<article class='need nyc'><div class=nyc__head>{_sc(kind, word)}"
        f"<span class=nyc__where>{esc(_NEED_LABEL.get(n.kind, n.kind))}"
        f"<code>{esc(n.ref)}</code></span></div><div class=nyc__body>"
        f"<p class=nyc__what>{what}</p>{mean}{opts}{act}{_links(links, full)}</div></article>"
    )


def _queue_state(r: Row, ctx: Ctx) -> tuple[str, str, str, str]:
    """A queued ticket's (row class, chip kind, chip word, why it waits)."""
    if r.hold:
        return "nyi--gate", "unk", "Held", f"Held: {ctx.plain(r.hold)}"
    if r.blocked_by:
        why = "Blocked by " + ", ".join(esc(b) for b in r.blocked_by)
        return "nyi--blocked", "down", "Blocked", why
    return "nyi--queued", "q", "Queued", "Queued, nothing blocking it"
