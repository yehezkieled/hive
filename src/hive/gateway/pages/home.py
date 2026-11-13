# ruff: noqa: E501  (inline HTML)
"""The Desk (``/``): the Needs-you lane, the project rows and their sheets, the open review
pages, and the delegate bar."""

from __future__ import annotations

from html import escape as esc
from urllib.parse import quote, urlencode

from hive.gateway.actions import new_request_id
from hive.gateway.desk import NO_PROJECT, BacklogGroup, Desk, Glance, NeedsYou, Project, Row, glance
from hive.gateway.pages.needs import _WAIT, _lines, _links, _queue_state
from hive.gateway.pages.shell import Ctx, _banner, _page, _sc, _tabs
from hive.gateway.reviews import Review
from hive.gateway.snapshot import Snapshot

_QUEUED_SHOWN = 4  # queued tickets listed per project before "+N more"


_ROW_ORDER = {"Held": 0, "Blocked": 1, "Queued": 2}


def _nyx(
    *,
    project: str,
    item_id: str,
    title: str,
    cls: str,
    head: str,
    more: tuple[str, str],
    primary: bool,
    ctx: Ctx,
) -> str:
    """One Needs-you item: a shut card or row whose head opens it in place (``is-primary``).
    The page script makes a picked item its group's open one; a double tap opens the project.
    Without the script the head is a plain link to the project. ``more`` is the open item's
    body, before and after the agent-written description."""
    card = cls.startswith("nyc ")
    head_cls = "'nyx__head nyc__head'" if card else "nyx__head"
    body_cls = "'nyx__more nyc__body'" if card else "nyx__more"
    href = "/p/" + quote(project, safe="")
    text = ctx.descriptions.get(f"{project}/{item_id}")
    desc_url = "/describe?" + urlencode({"p": project, "id": item_id})
    desc = (
        "<p class=nyx__desc data-ready=1 tabindex=0 role=button aria-haspopup=dialog>"
        f"{esc(text)}</p>"
        if text
        else "<p class=nyx__desc hidden></p>"
    )
    state = " is-primary" if primary else ""
    return (
        f"<div class='nyx {cls}{state}' data-item='{esc(item_id)}' data-desc='{esc(desc_url)}' "
        f"data-open='{esc(href)}'><a class={head_cls} href='{esc(href)}' "
        f"title='{esc(title or item_id)}'>{head}</a>"
        f"<div class={body_cls}>{more[0]}{desc}{more[1]}</div></div>"
    )


def _wait_card(n: NeedsYou, ctx: Ctx, primary: bool) -> str:
    """A captain hold as a decision card: line 1 what happened, line 2 what it means, then big
    option buttons (recommended marked; a tap fills the reply), the reply and its two answer
    buttons (the one the hold asks for first), and the links."""
    cls, kind, word = _WAIT["hold"]
    ref = n.ref.partition("/")[0]
    entity = f"{ctx.show(n.project)} · {ref}"
    what, mean, opts, full = _lines(n, n.title, ctx)
    links = [(url, "Open board ↗") for url in ctx.boards(n.text)]
    pr = ctx.link(n.url)
    if pr:
        links.append((pr, "Open PR ↗"))
    links.append(("/p/" + quote(n.project, safe=""), "Open project ↗"))
    resume = "<button class='btn btn--{}' name=release value=1>Answer &amp; resume</button>"
    close = "<button class='btn btn--{}' name=release value=0>Answer &amp; close</button>"
    buttons = (
        resume.format("p") + close.format("s")
        if n.gated
        else close.format("p") + resume.format("s")
    )
    act = ctx.form(
        "answer",
        "<input type=text class=nyi__reply name=text required maxlength=512 "
        f"placeholder='Reply to {esc(entity)}…' aria-label='Reply to {esc(entity)}'>{buttons}",
        "nyi__actions nyc__act",
        task=n.ref,
        rid=new_request_id(),
    )
    head = (
        f"{_sc(kind, word)}<span class=nyc__where><span class=nyc__proj title='{esc(ctx.show(n.project))}'>{esc(ctx.show(n.project))}</span>"
        f"<code class=nyx__id>{esc(ref)}</code></span><span class=nyc__chev>Open</span>"
        f"<span class=nyc__what>{what}</span>"
    )
    return _nyx(
        project=n.project,
        item_id=n.ref,
        title=n.title or n.text,
        cls=f"nyc {cls}",
        head=head,
        more=(mean + opts, act + _links(links, full)),
        primary=primary,
        ctx=ctx,
    )


def _queue_row(r: Row, project: str, ctx: Ctx) -> str:
    """A held, blocked or queued ticket: its chip, title and project on one line; opened, why
    it waits and the agent-written description."""
    cls, kind, word, why = _queue_state(r, ctx)
    href = "/p/" + quote(project, safe="")
    head = (
        f"{_sc(kind, word)}<span class=r__t>{esc(r.title or r.id)}</span>"
        f"<span class=r__p title='{esc(ctx.show(project))}'>{esc(ctx.show(project))}</span>"
    )
    return _nyx(
        project=project,
        item_id=r.id,
        title=r.title,
        cls=f"nyr {cls}",
        head=head,
        more=(f"<p class=nyr__why>{why}</p>", _links([(href, "Open project ↗")])),
        primary=False,
        ctx=ctx,
    )


def _sheet(sid: str, title: str, body: str, close: str, head: str = "") -> str:
    """A modal sheet (``<dialog>``) the page script opens from any ``data-sheet-open=<sid>``
    and closes on its X, a tap outside or Escape. ``title`` and ``body`` are HTML."""
    return (
        f"<dialog id={sid} class=sheet aria-labelledby={sid}-title data-sheet>"
        f"<div class=sheet__head><h2 id={sid}-title data-sheet-title>{title}</h2>{head}"
        f"<button type=button class=sheet__x data-sheet-close aria-label='{esc(close)}'>×</button>"
        f"</div>{body}</dialog>"
    )


def _open_project(href: str) -> str:
    return f"<a class='btn btn--s btn--sm' href='{esc(href)}'>Open project ↗</a>"


def _sheet_row(href: str, item_id: str, title: str, chip: tuple[str, str]) -> str:
    return (
        f"<a class=rv href='{esc(href)}'><span class=rv__title>{esc(title or item_id)}</span>"
        f"<span class=rv__meta>{_sc(*chip)}<span class=rv__proj>{esc(item_id)}</span></span></a>"
    )


def _items_list(g: BacklogGroup | None, ctx: Ctx) -> str:
    """Every item of a project's Needs-you slice (held first, then queued), for a sheet."""
    if g is None or not g.count:
        return "<p class=sheet__empty>Nothing waiting on you or queued.</p>"
    href = "/p/" + quote(g.project, safe="")
    rows = [
        _sheet_row(href, n.ref, n.title or n.text, _WAIT.get(n.kind, ("", "you", n.kind))[1:])
        for n in g.waiting
    ]
    rows += [_sheet_row(href, r.id, r.title, _queue_state(r, ctx)[1:3]) for r in g.queued]
    return f"<div class=sheet__list>{''.join(rows)}</div>"


def _lane(desk: Desk, ctx: Ctx) -> str:
    """ "Needs you": what waits on the owner, as decision cards (the first open), then every
    held, blocked or queued ticket across the projects, as rows (the first few per project;
    "+N more" opens that project's whole slice in a sheet)."""
    waiting = [n for g in desk.backlog for n in g.waiting]
    queued = sum(len(g.queued) for g in desk.backlog)
    hexn = "hexn" if waiting else "hexn hexn--q"
    meta = f"<span class=meta>{len(waiting)} waiting · {queued} held or queued</span>"
    head = (
        f"<div class=pn__h><h2>Needs you</h2><span class='{hexn} nyl__count'>"
        f"{desk.backlog_count}</span>{meta if desk.backlog else ''}</div>"
    )
    if not desk.backlog:
        n = desk.loops_running
        return (
            f"<section class='pn nyl nyl--calm is-on' data-tp=needs>{head}"
            "<div class='pn__b nyl__body'><div class='nyc nyc--calm'><div class=nyc__head>"
            f"{_sc('ok', 'All clear')}</div><div class=nyc__body>"
            "<p class=nyc__what>Nothing waits on you.</p>"
            f"<p class=nyc__mean>Backlog is empty · {n} loop{'' if n == 1 else 's'} running</p>"
            "</div></div></div></section>"
        )
    body = ""
    if waiting:
        cards = "".join(_wait_card(n, ctx, i == 0) for i, n in enumerate(waiting))
        body += (
            f"<p class=sl>Waiting on you · {len(waiting)}</p>"
            f"<div class='nyg nyg--cards' data-group=waiting>{cards}</div>"
        )
    if queued:
        rows: list[tuple[tuple[int, int, int], str]] = []
        more = ""
        for i, g in enumerate(desk.backlog):
            shown = g.queued[:_QUEUED_SHOWN]
            for j, r in enumerate(shown):
                rank = _ROW_ORDER[_queue_state(r, ctx)[2]]
                rows.append(((rank, i, j), _queue_row(r, g.project, ctx)))
            rest = len(g.queued) - len(shown)
            if rest:
                sid, href = f"nys-{i}", "/p/" + quote(g.project, safe="")
                more += (
                    f"<a class='nyq__more lnk' href='{esc(href)}' data-sheet-open={sid} "
                    f"aria-haspopup=dialog aria-controls={sid}>+{rest} more · "
                    f"{esc(ctx.show(g.project))}</a>"
                    + _sheet(
                        sid,
                        f"{ctx.name_html(g.project)} · {g.count}",
                        _items_list(g, ctx),
                        f"Close {ctx.show(g.project)}",
                        _open_project(href),
                    )
                )
        rows.sort(key=lambda row: row[0])
        body += (
            f"<p class=sl>Held or queued · {queued}</p>"
            f"<div class='nyg nyg--rows' data-group=queued>{''.join(h for _, h in rows)}</div>"
            + (f"<div class=nyq>{more}</div>" if more else "")
        )
    return f"<section class='pn nyl is-on' data-tp=needs>{head}<div class='pn__b nyl__body'>{body}</div></section>"


_CHIP = {"blocked": ("down", "Blocked"), "running": ("run", "Running"), "idle": ("unk", "Idle")}


def _target(p: Project, ctx: Ctx) -> tuple[str, str]:
    """(delegate-bar label, project field) for a focused card."""
    if p.name == NO_PROJECT:
        return "first mate", ""
    return f"{'second mate' if p.mate else 'first mate'} · {ctx.show(p.name)}", p.name


def _card_text(p: Project) -> tuple[Glance, str, str]:
    """A project's glance, its activity line (HTML) and its progress label, for its sheet."""
    g = glance(p)
    prog = "done" if g.total and g.done == g.total else f"{g.done} / {g.total} tasks"
    now = f"<b>{esc(g.lead)}</b> — {esc(g.now)}" if g.lead else esc(g.now)
    return g, now, prog


def _row_now(g: Glance) -> str:
    """The project row's one short line: who waits, what runs, or what is queued."""
    if g.status == "blocked":
        return g.lead
    if g.status == "running":
        return f"{g.lead} · {g.now}"
    head, _, rest = g.now.partition(" · ")
    line = rest.replace(" · ", ", ") if head == "Idle" and rest else g.now
    return line[:1].upper() + line[1:]


def _card(p: Project, selected: bool, ctx: Ctx, sid: str) -> str:
    """A project row: a tap focuses it (retargets the delegate bar); a tap on the focused row
    opens its detail sheet ``sid``. Without the script the focused row opens the project."""
    g = glance(p)
    label, field = _target(p, ctx)
    nxt = "/?focus=" + quote(p.name, safe="")
    open_href = "/p/" + quote(p.name, safe="")
    pct = round(100 * g.done / g.total) if g.total else 0
    sel = " is-selected" if selected else ""
    return (
        f"<a class='pc pc--{g.status}{sel}' href='{esc(open_href if selected else nxt)}' "
        f"data-card data-name='{esc(p.name)}' data-label='{esc(label)}' "
        f"data-project='{esc(field)}' data-next='{esc(nxt)}' data-open='{esc(open_href)}' "
        f"data-detail={sid}{' aria-current=true' if selected else ''}>"
        f"<span class=pc__n><span class=pc__name title='{esc(ctx.show(p.name))}'>{ctx.name_html(p.name, breaks=False)}</span>"
        f"<small><span class=pc__mae>{'second mate' if p.mate else 'first mate'}</span> · "
        f"{esc(_row_now(g))}</small></span>{_sc(*_CHIP[g.status])}"
        f"<span class=pc__prog><span class=bar><i style='width:{pct}%'></i></span>"
        f"{g.done}/{g.total}</span><span class='pc__open sr'>, tap for details</span></a>"
    )


def _card_sheet(p: Project, group: BacklogGroup | None, ctx: Ctx, sid: str) -> str:
    """The focused project's detail: status, activity, progress and its Needs-you items."""
    g, now, prog = _card_text(p)
    meta = (
        f"<p class=sheet__meta><b>{g.status}</b> · {'second mate' if p.mate else 'first mate'}"
        f" · {prog}<br>{now}</p>"
    )
    note = ctx.project_notes.get(p.name)
    about = f"<p class=sheet__about>{esc(note)}</p>" if note else ""
    return _sheet(
        sid,
        ctx.name_html(p.name),
        about + meta + _items_list(group, ctx),
        f"Close {ctx.show(p.name)}",
        _open_project("/p/" + quote(p.name, safe="")),
    )


def _dbar(focus: Project | None, ctx: Ctx, stamp: str = "") -> str:
    """The delegate bar along the bottom: who it sends to, the goal, Delegate, and when the
    desk last read firstmate. Tapping a project row retargets it (page script)."""
    label, field = _target(focus, ctx) if focus else ("first mate", "")
    nxt = "/?focus=" + quote(focus.name, safe="") if focus else "/"
    form = ctx.form(
        "delegate",
        f"<span class=dbar__target data-target>→ {esc(label)}</span>"
        "<input type=text class=dbar__in name=text required maxlength=4000 placeholder='Describe a goal…' "
        "aria-label='Delegate a goal'><button class='dbar__go btn btn--p'>Delegate</button>",
        "dbar",
        rid=new_request_id(),
        project=field,
        next=nxt,
    )
    if not form:
        return f"<div class=dbar-wrap><div class=dbar__row>{stamp}</div></div>" if stamp else ""
    hidden = " hidden" if field else ""
    return (
        "<div class=dbar-wrap><p class=dbar-flash data-flash role=status hidden></p>"
        f"<p class='dbar-note sr'>Delegate to <b data-tname>{esc(label)}</b>"
        f"<span data-nofocus{hidden}> · no project focus</span>"
        f" · tapping a project card retargets this bar</p><div class=dbar__row>{form}{stamp}</div></div>"
    )


_REVIEWS_SHOWN = 10


_CLOSE_BTN = "btn btn--s btn--sm rv__x"


def _review_form(action: str, label: str, ctx: Ctx, cls: str = _CLOSE_BTN, **hidden: str) -> str:
    """A POST form that ends review sessions. The page script turns the first tap into an
    in-place confirm ("Tap again"); without it the server shows a confirm page."""
    btn = f"<button class='{cls}' data-label='{esc(label)}'>{esc(label)}</button>"
    form = ctx.form(action, btn, rid=new_request_id(), **hidden)
    return form.replace("<form ", "<form data-rvclose ", 1) if form else ""


def _review(r: Review, ctx: Ctx) -> str:
    href = f"{ctx.board_url}/session/{r.key}"
    reply = "<span class='sc sc--run rv__reply'><i></i>Reply</span>" if r.reply else ""
    done = "<span class=rv__done>looks done</span>" if r.stale else ""
    where = f"<span class=rv__proj>{esc(ctx.show(r.project))}</span>" if r.project else ""
    close = _review_form("review-close", "Close", ctx, key=r.key) if ctx.can_close_reviews else ""
    return (
        f"<div class='rvrow{' rvrow--stale' if r.stale else ''}'>"
        f"<a class=rv href='{esc(href)}' target=_blank rel='noopener noreferrer'>"
        f"<span class=rv__title>{esc(r.title)}</span><span class=rv__meta>{reply}{where}{done}"
        f"</span></a>{close}</div>"
    )


def _reviews(reviews: list[Review], ctx: Ctx) -> str:
    """Open Lavish review sessions as rows that open on the tailnet board URL, in a panel.

    The first few are listed; "+N more" is always the list's last row and opens a sheet of
    every session (the same unread-replies-first order). Each row has a Close button; pages
    that look done (old, or beyond the newest eight) are tagged, nudged about above the list,
    and closed together by "Close all old".
    """
    if not reviews:
        return ""
    shown = "".join(_review(r, ctx) for r in reviews[:_REVIEWS_SHOWN])
    n = len(reviews)
    rest = n - _REVIEWS_SHOWN
    stale = sum(1 for r in reviews if r.stale)
    can = ctx.can_close_reviews
    more = sheet = nudge = ""
    if rest > 0:
        more = (
            "<button type=button class=rv__morebtn data-sheet-open=rvs-sheet aria-haspopup=dialog "
            f"aria-controls=rvs-sheet>+{rest} more</button>"
        )
    bulk = f"{_CLOSE_BTN} rv__x--bulk"
    if stale and can:
        word = "page looks" if stale == 1 else "pages look"
        nudge = (
            f"<div class=rvs__nudge role=status><span>{stale} {word} done: close "
            f"{'it' if stale == 1 else 'them'}?</span>"
            "<button type=button class='btn btn--s btn--sm rv__review' data-sheet-open=rvs-sheet "
            "aria-haspopup=dialog aria-controls=rvs-sheet>Review</button>"
            f"{_review_form('review-close-old', f'Close {stale}', ctx, bulk)}</div>"
        )
    if rest > 0 or (stale and can):
        sheet = _sheet(
            "rvs-sheet",
            f"Review pages · {n}",
            f"<div class=sheet__list>{''.join(_review(r, ctx) for r in reviews)}</div>",
            "Close review pages",
            _review_form("review-close-old", "Close all old", ctx, bulk) if stale and can else "",
        )
    return (
        "<section class='pn rvp' data-tp=reviews data-rp=reviews>"
        f"<div class=pn__h><h2>Review pages</h2><span class='hexn hexn--q' data-rvn>{n}</span></div>"
        f"<div class=rvs>{nudge}<div class=rv__box><div class=rv__list tabindex=0 role=region "
        f"aria-label='Open review pages'>{shown}</div>{more}</div>{sheet}</div></section>"
    )


def render_home(
    snap: Snapshot,
    desk: Desk | None,
    ctx: Ctx,
    focus: str | None = None,
    reviews: list[Review] | None = None,
) -> str:
    """The desk: Needs you, Projects and Review pages side by side over the delegate bar.
    Narrower screens tab the right-hand panels; a phone tabs all three."""
    attrs = " data-refresh=30000"
    if desk is None:
        body = f"<div class=ph><h1>Hive desk</h1></div>{_banner(snap, ctx)}"
        return _page("Hive desk", body, "desk", attrs, ctx)
    if focus not in desk.projects:
        focus = desk.default_focus()
    groups = {g.project: g for g in desk.backlog}
    cards = details = ""
    blocked = 0
    for i, p in enumerate(desk.projects.values()):
        cards += _card(p, p.name == focus, ctx, f"pcs-{i}")
        details += _card_sheet(p, groups.get(p.name), ctx, f"pcs-{i}")
        blocked += glance(p).status == "blocked"
    more = "".join(
        f"<p class=stamp>+{n} more owned by {esc(owner)}, not shown</p>"
        for owner, n in desk.more.items()
    )
    count = len(desk.projects)
    projects = (
        "<section class='pn pcsec is-ron' data-tp=projects data-rp=projects>"
        f"<div class=pn__h><h2>Projects</h2><span class='hexn hexn--q'>{count}</span>"
        + (f"<span class=meta>{blocked} blocked</span>" if blocked else "")
        + f"</div><div class='pn__b pcs'>{cards or '<p class=empty>No projects yet</p>'}{more}</div>"
        f"{details}</section>"
    )
    review = _reviews(reviews or [], ctx)
    tabs = [("needs", "Needs you", desk.backlog_count), ("projects", "Projects", count)]
    right = ""
    if review:
        tabs.append(("reviews", "Reviews", len(reviews or [])))
        right = _tabs(
            "r",
            [("projects", "Projects", count), ("reviews", "Review pages", len(reviews or []))],
            "projects",
        )
    stamp = f"<span class=stamp>Updated {ctx.time(desk.generated)}</span>" if desk.generated else ""
    body = (
        f"<div class=screen data-stack><div class='pgb desk{'' if review else ' desk--two'}' data-tabs>"
        f"{_tabs('p', tabs, 'needs')}{_lane(desk, ctx)}<div class=dcol>{right}{projects}{review}</div></div>"
        f"{_dbar(desk.projects.get(focus) if focus else None, ctx, stamp)}"
        + _sheet(
            "desc-sheet",
            "",
            "<div class=sheet__list><p class=sheet__text data-sheet-text></p></div>",
            "Close description",
        )
        + "</div>"
    )
    return _page("Hive desk", body, "desk", attrs, ctx)
