# ruff: noqa: E501  (inline HTML)
"""A project page (``/p/<name>``): what needs the owner, its crews and its backlog; and one
crew's live output (``/w/<task>``)."""

from __future__ import annotations

from html import escape as esc
from urllib.parse import quote

from hive.gateway.actions import TICKET_FIELDS, new_request_id
from hive.gateway.desk import Crew, Project, Row
from hive.gateway.pages.needs import _need, _queue_state
from hive.gateway.pages.shell import Ctx, _banner, _page, _sc, _tabs
from hive.gateway.snapshot import Snapshot

# a crew's state: its chip colour; the word is the state itself
_CREW_CHIP = {"working": "run", "done": "ok", "idle": "q", "waiting": "you", "stuck": "down"}


# a backlog ticket's state, when it is not queued: (chip colour, chip word)
_ROW_CHIP = {"done": ("ok", "Done"), "in_progress": ("run", "In progress")}


def _word(state: str) -> str:
    return esc(state.replace("_", " ").capitalize())


def _crew(c: Crew, ctx: Ctx) -> str:
    controls = ctx.form(
        "control",
        "<button class='btn btn--s btn--sm' name=verb value=interrupt>Interrupt…</button>"
        "<button class='btn btn--s btn--sm' name=verb value=relaunch>Relaunch…</button>",
        "crew__ctl",
        task=c.id,
    )
    return (
        f"<li class=crew><div class=crew__top>{_sc(_CREW_CHIP.get(c.state, 'unk'), _word(c.state))}"
        f"<b>{esc(c.title or c.id)}</b>"
        f"<a class=lnk href='/w/{esc(quote(c.id, safe=''))}'>Watch live</a></div>"
        f"<p class=crew__d>{esc(c.detail)}"
        f"<span>{' · '.join([f'<code>{esc(c.id)}</code>'] + [esc(x) for x in (c.kind, c.harness) if x])}"
        "</span></p>"
        f"{controls}</li>"
    )


def _ticket_edit(r: Row, project: str, ctx: Ctx) -> str:
    if r.state == "done":
        return ""
    options = "".join(f"<option>{esc(f)}</option>" for f in TICKET_FIELDS)
    return ctx.form(
        "ticket",
        f"<details class=tk><summary>Request an edit</summary><label>Field</label>"
        f"<select name=field>{options}</select><label>New text</label>"
        "<textarea name=text required maxlength=4000></textarea>"
        "<button>Send edit request</button></details>",
        mode="edit",
        ticket=r.id,
        project=project,
        rid=new_request_id(),
    )


def _new_ticket(project: str, ctx: Ctx) -> str:
    return ctx.form(
        "ticket",
        "<details class='fold tk'><summary>New ticket</summary><label>Title</label>"
        "<input type=text name=title required maxlength=200><label>Details</label>"
        "<textarea name=text maxlength=4000></textarea>"
        "<button>Send create request</button></details>",
        mode="create",
        project=project,
        rid=new_request_id(),
    )


def _backlog_row(r: Row, titles: dict[str, str], project: str, ctx: Ctx) -> str:
    if r.state == "queued":
        _, kind, word, _ = _queue_state(r, ctx)
    else:
        kind, word = _ROW_CHIP.get(r.state, ("unk", _word(r.state)))
    extra = [f"<code>{esc(r.id)}</code>"]
    if r.owner and r.owner != "main":
        extra.append(f"owner: {esc(r.owner)}")
    if r.hold:
        extra.append(f"held: {esc(r.hold)}")
    if r.blocked_by:
        extra.append("blocked by " + esc(", ".join(titles.get(b) or b for b in r.blocked_by)))
    pr = ctx.link(r.pr_url)
    if pr:
        extra.append(f'<a href="{esc(pr)}" rel="noopener noreferrer">PR</a>')
    return (
        f"<li class='bl bl--{esc(r.state)}'>{_sc(kind, word)}<div class=bl__b>"
        f"<b class=bl__t>{esc(r.title or r.id)}</b><p class=bl__d>{' · '.join(extra)}</p>"
        f"{_ticket_edit(r, project, ctx)}</div></li>"
    )


def render_project(
    name: str,
    snap: Snapshot,
    project: Project | None,
    ctx: Ctx,
) -> str:
    """A project lens: what needs the owner beside its crews and backlog, as two panels (tabs
    on a phone), the approved board's Needs page."""
    attrs = " data-refresh=30000"
    back = "<a class='lnk ph__back' href='/'>← Desk</a>"
    if project is None:
        body = _banner(snap, ctx) if not snap.ok else "<p>Unknown project.</p>"
        return _page(name, f"<div class=ph>{back}</div>{body}", "", attrs, ctx)
    needs = (
        "".join(_need(n, False, ctx) for n in project.needs_you)
        or "<p class=empty>Nothing needs you.</p>"
    )
    titles = {r.id: r.title for r in project.rows}
    rows = "".join(_backlog_row(r, titles, project.name, ctx) for r in project.rows)
    crews = "".join(_crew(c, ctx) for c in project.crews)
    n_need, n_rows, n_crews = len(project.needs_you), len(project.rows), len(project.crews)
    return _page(
        f"{ctx.show(name)} · Hive desk",
        f"<div class=ph>{back}<h1>{ctx.name_html(name)}</h1></div><div class='pgb npg' data-tabs>"
        + _tabs("p", [("needs", "Needs you", n_need), ("work", "Work", n_rows)], "needs")
        + "<section class='pn is-on' data-tp=needs><div class=pn__h><h2>Needs you</h2>"
        + (f"<span class=hexn>{n_need}</span>" if n_need else "")
        + f"</div><div class=pn__b>{needs}</div></section>"
        "<section class=pn data-tp=work><div class=pn__h><h2>Work</h2>"
        f"<span class=meta>{n_crews} {'crew' if n_crews == 1 else 'crews'} · {n_rows} "
        f"{'ticket' if n_rows == 1 else 'tickets'}</span></div><div class=pn__b>"
        f"<p class=sl>Crews · {n_crews}</p>"
        + (f"<ul class=crews>{crews}</ul>" if crews else "<p class=meta>No live crews.</p>")
        + f"<p class=sl>Backlog · {n_rows}</p>{_new_ticket(project.name, ctx)}"
        + (f"<ul class=bls>{rows}</ul>" if rows else "<p class=meta>Empty.</p>")
        + "</div></section></div>",
        "",
        attrs,
        ctx,
    )


def render_watch(crew: Crew, ctx: Ctx) -> str:
    """One crew's recent output, live: the output scrolls inside its panel."""
    return _page(
        f"{crew.id} · Hive desk",
        "<div class=ph><a class='lnk ph__back' href='/'>← Desk</a>"
        f"<h1>{esc(crew.title or crew.id)}</h1>"
        f"{_sc(_CREW_CHIP.get(crew.state, 'unk'), _word(crew.state))}"
        f"<span class=sub>{esc(crew.detail)} · <code>{esc(crew.id)}</code> · "
        f"{esc(crew.harness)}</span></div>"
        "<section class='pn watch'><div class=pn__h><h2>Recent output</h2>"
        "<span class=meta id=tail-status role=status>Loading…</span></div>"
        "<pre class='pn__b tail' id=tail aria-label='Recent output' aria-live=off></pre></section>",
        "",
        f" data-tail='{esc(crew.id)}'",
        ctx,
    )
