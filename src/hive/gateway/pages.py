# ruff: noqa: E501  (inline CSS)
"""HTML for the desk. Plain escaped strings; no scripts. Forms post to ``/act/*``."""

from __future__ import annotations

from html import escape as esc
from urllib.parse import quote, urlsplit

from hive.gateway.actions import TICKET_FIELDS, Outcome, new_request_id
from hive.gateway.chat import ChatView
from hive.gateway.desk import Crew, Desk, NeedsYou, Project, Row
from hive.gateway.snapshot import Snapshot

CSS = """
:root{--bg:#f6f3ec;--card:#fffdf8;--ink:#1d1b16;--mute:#6b665a;--line:#ddd6c6;--acc:#b4531a;--ok:#2f6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#16140f;--card:#201d16;--ink:#efe9da;
--mute:#a39c8a;--line:#3a352a;--acc:#f0925a;--ok:#7fc48c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif;
padding:env(safe-area-inset-top) 16px env(safe-area-inset-bottom)}
main{max-width:960px;margin:0 auto;padding:16px 0 48px}
h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.05rem;margin:1.6rem 0 .6rem}
a{color:var(--acc)}.mute{color:var(--mute);font-size:.88rem}
.banner{border:1px solid var(--acc);border-radius:10px;padding:12px;margin:12px 0;
background:var(--card)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card.hot{border-color:var(--acc)}.card h3{margin:0 0 6px;font-size:1.05rem}
.need{background:var(--card);border:1px solid var(--acc);border-radius:12px;padding:12px;
margin:8px 0;
overflow-wrap:anywhere}
.tag{display:inline-block;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-right:6px;color:var(--mute)}
.row{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
.row .id{flex:0 0 auto;font-family:ui-monospace,monospace;font-size:.82rem;color:var(--mute)}
.chips span{margin-right:12px;white-space:nowrap}
form{margin:8px 0}textarea,input[type=text],select{width:100%;font:inherit;color:var(--ink);
background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
textarea{min-height:5.5rem}label{display:block;font-size:.88rem;color:var(--mute);margin:6px 0 2px}
button{font:inherit;border:1px solid var(--acc);background:var(--acc);color:var(--bg);
border-radius:8px;padding:8px 14px;min-height:44px;cursor:pointer}
button.quiet{background:transparent;color:var(--acc)}button.danger{background:#a3261e;border-color:#a3261e;color:#fff}
.inline{display:inline-block;margin:4px 6px 4px 0}details{margin-top:8px}summary{cursor:pointer;color:var(--acc)}
.ok{border-color:var(--ok)}.bubble{white-space:pre-wrap;overflow-wrap:anywhere}
nav{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
"""


def _safe_url(url: str | None) -> str | None:
    if url and urlsplit(url).scheme in ("http", "https"):
        return url
    return None


FLASH = {
    "answer": "Answer recorded.",
    "chat": "Sent to the first mate.",
    "ticket": "Ticket request sent to the first mate.",
    "merge": "Merge word recorded for the first mate.",
    "control": "Control verb delivered.",
    "decision": "Answer sent to the first mate.",
}


def flash(code: str | None) -> str:
    msg = FLASH.get(code or "")
    return f"<div class='banner ok' role=status>{esc(msg)}</div>" if msg else ""


class Ctx:
    """Per-render write context: CSRF token and whether writes are allowed at all."""

    def __init__(self, csrf: str, writable: bool = True, nxt: str = "/") -> None:
        self.csrf = csrf
        self.writable = writable
        self.nxt = nxt

    def form(self, action: str, inner: str, **hidden: str) -> str:
        if not self.writable:
            return ""
        fields = {"csrf": self.csrf, "next": self.nxt, **hidden}
        hid = "".join(
            f'<input type=hidden name={esc(k)} value="{esc(v)}">' for k, v in fields.items()
        )
        return f"<form method=post action='/act/{esc(action)}'>{hid}{inner}</form>"


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=color-scheme content='light dark'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _banner(snap: Snapshot) -> str:
    stamp = f" Snapshot {esc(snap.generated)}." if snap.generated else ""
    return (
        "<div class=banner role=status><strong>Read-only fallback.</strong> "
        f"{esc(snap.reason or 'No data.')}{stamp} Nothing here can be trusted until "
        "the desk and firstmate agree on the snapshot schema"
        f"{' (saw ' + esc(snap.schema) + ')' if snap.schema else ''}.</div>"
    )


def _need(n: NeedsYou, show_project: bool, ctx: Ctx) -> str:
    url = _safe_url(n.url)
    link = f' <a href="{esc(url)}" rel="noopener noreferrer">PR</a>' if url else ""
    where = f"{esc(n.project)} · " if show_project else ""
    act = ""
    if n.kind == "hold":
        checked = " checked" if n.gated else ""
        act = ctx.form(
            "answer",
            "<label>Your answer</label><textarea name=text required maxlength=6000></textarea>"
            f"<label><input type=checkbox name=release value=1{checked}> "
            "Release the work item (resume it) instead of closing the question</label>"
            "<button>Record answer</button>",
            task=n.ref,
        )
    elif n.kind == "decision":
        task, _, key = n.ref.partition("/")
        act = ctx.form(
            "decision",
            "<label>Your answer (sent to the first mate)</label>"
            "<textarea name=text required maxlength=4000></textarea><button>Send answer</button>",
            task=task,
            key=key,
            rid=new_request_id(),
        )
    elif n.kind == "merge":
        act = ctx.form(
            "merge",
            "<button class=quiet>Give merge word…</button>",
            task=n.ref,
            rid=new_request_id(),
        )
    return (
        f"<div class=need><span class=tag>{esc(n.kind)}</span>"
        f"<strong>{where}{esc(n.ref)}</strong>{link}<div>{esc(n.text)}</div>{act}</div>"
    )


def _nav(title: str, snap_ok: bool) -> str:
    mode = "" if snap_ok else "read-only"
    return (
        f"<nav><h1>{esc(title)}</h1><span class=mute><a href='/chat'>Chat</a>"
        f"{' · ' + mode if mode else ''}</span></nav>"
    )


def render_home(snap: Snapshot, desk: Desk | None, ctx: Ctx, flash_code: str | None = None) -> str:
    head = _nav("Hive desk", snap.ok) + flash(flash_code)
    if desk is None:
        return _page("Hive desk", head + _banner(snap))
    needs = (
        "".join(_need(n, True, ctx) for n in desk.needs_you)
        or "<p class=mute>Nothing needs you.</p>"
    )
    cards = []
    for p in desk.projects.values():
        hot = " hot" if p.needs_you else ""
        link = esc(quote(p.name, safe=""))
        cards.append(
            f"<a class='card{hot}' href='/p/{link}' style='text-decoration:none;color:inherit'>"
            f"<h3>{esc(p.name)}</h3><div class=chips>"
            f"<span>{p.count('in_flight')} in flight</span><span>{p.count('queued')} queued</span>"
            f"<span>{len(p.needs_you)} need you</span></div></a>"
        )
    stamp = f"<p class=mute>Snapshot {esc(desk.generated)}</p>" if desk.generated else ""
    return _page(
        "Hive desk",
        f"{head}{stamp}<h2>Needs you ({len(desk.needs_you)})</h2>{needs}"
        f"<h2>Projects</h2><div class=grid>{''.join(cards)}</div>",
    )


def _crew(c: Crew, ctx: Ctx) -> str:
    controls = ctx.form(
        "control",
        "<button class='quiet inline' name=verb value=interrupt>Interrupt…</button>"
        "<button class='quiet inline' name=verb value=relaunch>Relaunch…</button>",
        task=c.id,
    )
    return (
        f"<div class=row><span class=id>{esc(c.id)}</span><div>"
        f"<span class=tag>{esc(c.kind)}</span>{esc(c.state)} "
        f"<span class=mute>{esc(c.harness)} {esc(c.detail)}</span>{controls}</div></div>"
    )


def _ticket_edit(r: Row, project: str, ctx: Ctx) -> str:
    if r.state == "done":
        return ""
    options = "".join(f"<option>{esc(f)}</option>" for f in TICKET_FIELDS)
    return ctx.form(
        "ticket",
        f"<details><summary>Request an edit</summary><label>Field</label>"
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
        "<details><summary>New ticket</summary><label>Title</label>"
        "<input type=text name=title required maxlength=200><label>Details</label>"
        "<textarea name=text maxlength=4000></textarea>"
        "<button>Send create request</button></details>",
        mode="create",
        project=project,
        rid=new_request_id(),
    )


def render_project(
    name: str,
    snap: Snapshot,
    project: Project | None,
    ctx: Ctx,
    flash_code: str | None = None,
) -> str:
    back = "<p><a href='/'>← Desk</a></p>"
    if project is None:
        return _page(name, back + (_banner(snap) if not snap.ok else "<p>Unknown project.</p>"))
    needs = (
        "".join(_need(n, False, ctx) for n in project.needs_you)
        or "<p class=mute>Nothing needs you.</p>"
    )
    rows = []
    for r in project.rows:
        extra = []
        if r.hold:
            extra.append(f"held: {esc(r.hold)}")
        if r.blocked_by:
            extra.append("blocked by " + esc(", ".join(r.blocked_by)))
        pr = _safe_url(r.pr_url)
        if pr:
            extra.append(f'<a href="{esc(pr)}" rel="noopener noreferrer">PR</a>')
        sub = f"<div class=mute>{' · '.join(extra)}</div>" if extra else ""
        rows.append(
            f"<div class=row><span class=id>{esc(r.id)}</span><div>"
            f"<span class=tag>{esc(r.state.replace('_', ' '))}</span>"
            f"{esc(r.title)}{sub}{_ticket_edit(r, project.name, ctx)}</div></div>"
        )
    crews = "".join(_crew(c, ctx) for c in project.crews) or "<p class=mute>No live crews.</p>"
    return _page(
        f"{name} · Hive desk",
        f"{back}<h1>{esc(name)}</h1>{flash(flash_code)}<h2>Needs you ({len(project.needs_you)})</h2>"
        f"{needs}<h2>Crews</h2>{crews}<h2>Backlog ({len(project.rows)})</h2>"
        f"{_new_ticket(project.name, ctx)}{''.join(rows) or '<p class=mute>Empty.</p>'}",
    )


_KIND_LABEL = {
    "chat": "chat",
    "ticket": "ticket request",
    "merge": "merge word",
    "decision": "answer",
}
_STATE_LABEL = {
    "pending": "waiting for the first mate",
    "seen": "picked up",
    "replied": "answered",
}


def render_chat(view: ChatView, ctx: Ctx, flash_code: str | None = None) -> str:
    if view.can_receive is False:
        status = (
            "<div class=banner role=status>The first mate session is not receiving right now. "
            "Notes are saved and will be read when it is back.</div>"
        )
    else:
        status = ""
    form = ctx.form(
        "chat",
        "<label>Message the first mate</label><textarea name=text required maxlength=4000>"
        "</textarea><button>Send</button>",
        rid=new_request_id(),
    )
    if not view.available:
        items = (
            "<p class=mute>Receipts are unavailable (firstmate inbox script did not answer).</p>"
        )
    else:
        cards = []
        for r in view.receipts:
            reply = (
                f"<div class='need ok'><span class=tag>first mate</span>"
                f"<span class=mute>{esc(r.reply_at or '')}</span>"
                f"<div class=bubble>{esc(r.reply or '')}</div></div>"
                if r.reply is not None
                else ""
            )
            cards.append(
                f"<div class=card style='margin:8px 0'><span class=tag>{esc(_KIND_LABEL[r.kind])}"
                f"</span><span class=tag>{esc(_STATE_LABEL[r.state])}</span>"
                f"<span class=mute>{esc(r.at)}</span><div class=bubble>{esc(r.body)}</div>{reply}</div>"
            )
        items = "".join(cards) or "<p class=mute>No messages yet.</p>"
        if view.omitted:
            items += "<p class=mute>Older entries omitted: " + esc("; ".join(view.omitted)) + "</p>"
    return _page(
        "Chat · Hive desk",
        f"<p><a href='/'>← Desk</a></p><h1>Chat with the first mate</h1>{flash(flash_code)}"
        f"{status}{form}<h2>Messages and requests</h2>{items}",
    )


def render_confirm(title: str, detail: str, action: str, ctx: Ctx, hidden: dict[str, str]) -> str:
    """Step-up page: nothing runs until this form is posted back with ``step``."""
    inner = (
        f"<p>{esc(detail)}</p>"
        "<button class=danger name=confirm value=1>Confirm</button> "
        f"<a href='{esc(ctx.nxt)}'>Cancel</a>"
    )
    form = ctx.form(action, inner, **hidden)
    return _page(f"{title} · Hive desk", f"<h1>{esc(title)}</h1><div class=banner>{form}</div>")


def render_result(title: str, message: str, nxt: str, ok: bool) -> str:
    cls = "banner ok" if ok else "banner"
    return _page(
        f"{title} · Hive desk",
        f"<h1>{esc(title)}</h1><div class='{cls}' role=status>{esc(message)}</div>"
        f"<p><a href='{esc(nxt)}'>Back</a></p>",
    )


def render_outcome(out: Outcome, nxt: str) -> str:
    return render_result(out.action, out.summary, nxt, True)
