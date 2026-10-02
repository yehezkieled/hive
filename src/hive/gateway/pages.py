# ruff: noqa: E501  (inline CSS)
"""HTML for the read-only desk. Plain escaped strings; no scripts, no forms."""

from __future__ import annotations

from html import escape as esc
from urllib.parse import quote, urlsplit

from hive.gateway.desk import Desk, NeedsYou, Project
from hive.gateway.snapshot import Snapshot

CSS = """
:root{--bg:#f6f3ec;--card:#fffdf8;--ink:#1d1b16;--mute:#6b665a;--line:#ddd6c6;--acc:#b4531a;--ok:#2f6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#16140f;--card:#201d16;--ink:#efe9da;--mute:#a39c8a;--line:#3a352a;--acc:#f0925a;--ok:#7fc48c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif;
padding:env(safe-area-inset-top) 16px env(safe-area-inset-bottom)}
main{max-width:960px;margin:0 auto;padding:16px 0 48px}
h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.05rem;margin:1.6rem 0 .6rem}
a{color:var(--acc)}.mute{color:var(--mute);font-size:.88rem}
.banner{border:1px solid var(--acc);border-radius:10px;padding:12px;margin:12px 0;background:var(--card)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card.hot{border-color:var(--acc)}.card h3{margin:0 0 6px;font-size:1.05rem}
.need{background:var(--card);border:1px solid var(--acc);border-radius:12px;padding:12px;margin:8px 0;
overflow-wrap:anywhere}
.tag{display:inline-block;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-right:6px;color:var(--mute)}
.row{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
.row .id{flex:0 0 auto;font-family:ui-monospace,monospace;font-size:.82rem;color:var(--mute)}
.chips span{margin-right:12px;white-space:nowrap}
nav{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
"""


def _safe_url(url: str | None) -> str | None:
    if url and urlsplit(url).scheme in ("http", "https"):
        return url
    return None


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


def _need(n: NeedsYou, show_project: bool) -> str:
    url = _safe_url(n.url)
    link = f' <a href="{esc(url)}" rel="noopener noreferrer">PR</a>' if url else ""
    where = f"{esc(n.project)} · " if show_project else ""
    return (
        f"<div class=need><span class=tag>{esc(n.kind)}</span>"
        f"<strong>{where}{esc(n.ref)}</strong>{link}<div>{esc(n.text)}</div></div>"
    )


def render_home(snap: Snapshot, desk: Desk | None) -> str:
    head = "<nav><h1>Hive desk</h1><span class=mute>read-only</span></nav>"
    if desk is None:
        return _page("Hive desk", head + _banner(snap))
    needs = (
        "".join(_need(n, True) for n in desk.needs_you) or "<p class=mute>Nothing needs you.</p>"
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


def render_project(name: str, snap: Snapshot, project: Project | None) -> str:
    back = "<p><a href='/'>← Desk</a></p>"
    if project is None:
        return _page(name, back + (_banner(snap) if not snap.ok else "<p>Unknown project.</p>"))
    needs = (
        "".join(_need(n, False) for n in project.needs_you)
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
            f"{esc(r.title)}{sub}</div></div>"
        )
    crews = (
        "".join(
            f"<div class=row><span class=id>{esc(c.id)}</span><div>"
            f"<span class=tag>{esc(c.kind)}</span>{esc(c.state)} <span class=mute>{esc(c.harness)} "
            f"{esc(c.detail)}</span></div></div>"
            for c in project.crews
        )
        or "<p class=mute>No live crews.</p>"
    )
    return _page(
        f"{name} · Hive desk",
        f"{back}<h1>{esc(name)}</h1><h2>Needs you ({len(project.needs_you)})</h2>{needs}"
        f"<h2>Crews</h2>{crews}<h2>Backlog ({len(project.rows)})</h2>"
        f"{''.join(rows) or '<p class=mute>Empty.</p>'}",
    )
