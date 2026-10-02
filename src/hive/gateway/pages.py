# ruff: noqa: E501  (inline CSS)
"""HTML for the desk. Plain escaped strings plus one fixed inline script (``SCRIPT``,
pinned by hash in the CSP) for local times and self-refresh. Forms post to ``/act/*``."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime
from html import escape as esc
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from hive.gateway.actions import TICKET_FIELDS, Outcome, new_request_id
from hive.gateway.chat import ChatView
from hive.gateway.desk import Crew, Desk, NeedsYou, Project, Row
from hive.gateway.settings import DEFAULT_BOARD_URL, DEFAULT_TZ
from hive.gateway.snapshot import Snapshot

CSS = """
:root{--bg:#f6f3ec;--card:#fffdf8;--ink:#1d1b16;--mute:#6b665a;--line:#ddd6c6;--acc:#b4531a;--ok:#2f6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#16140f;--card:#201d16;--ink:#efe9da;
--mute:#a39c8a;--line:#3a352a;--acc:#f0925a;--ok:#7fc48c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif;
padding:env(safe-area-inset-top) 16px env(safe-area-inset-bottom)}
main{max-width:960px;margin:0 auto;padding:8px 0 48px}
h1{font-size:1.4rem;margin:.6rem 0}h2{font-size:1.05rem;margin:1.6rem 0 .6rem}
a{color:var(--acc)}.mute{color:var(--mute);font-size:.88rem}
.top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
margin:0 -16px;padding:6px 16px}
.top div{max-width:960px;margin:0 auto;display:flex;justify-content:space-between;
align-items:center;gap:8px}
.brand{font-weight:600;text-decoration:none;color:var(--ink)}
.top nav{display:flex;align-items:center;flex-wrap:wrap;justify-content:flex-end}
#alerts{margin-left:4px;padding:0 10px;font-size:.9rem}
.tail{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;
font:12px/1.35 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;
min-height:12rem;max-height:70vh;overflow:auto}
.top nav a{display:inline-flex;align-items:center;min-height:44px;padding:0 12px;
border-radius:8px;text-decoration:none}
.top nav a[aria-current]{background:var(--card);border:1px solid var(--line);font-weight:600}
.banner{border:1px solid var(--acc);border-radius:10px;padding:12px;margin:12px 0;
background:var(--card)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card.hot{border-color:var(--acc)}.card h3{margin:0 0 6px;font-size:1.05rem}
.need{background:var(--card);border:1px solid var(--acc);border-radius:12px;padding:12px;
margin:8px 0;overflow-wrap:anywhere}
.need .what{font-weight:600}.sub{display:block;color:var(--mute);font-size:.8rem;margin-top:2px}
.tag{display:inline-block;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-right:6px;color:var(--mute)}
.row{padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
.chips span{margin-right:12px;white-space:nowrap}
form{margin:8px 0}textarea,input[type=text],select{width:100%;font:inherit;color:var(--ink);
background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
textarea{min-height:5.5rem}label{display:block;font-size:.88rem;color:var(--mute);margin:6px 0 2px}
button{font:inherit;border:1px solid var(--acc);background:var(--acc);color:var(--bg);
border-radius:8px;padding:8px 14px;min-height:44px;cursor:pointer}
button.quiet{background:transparent;color:var(--acc)}button.danger{background:#a3261e;border-color:#a3261e;color:#fff}
.inline{display:inline-block;margin:4px 6px 4px 0}details{margin-top:8px}summary{cursor:pointer;color:var(--acc);
min-height:32px}
.ok{border-color:var(--ok)}.bubble{white-space:pre-wrap;overflow-wrap:anywhere}
.compose textarea{min-height:3.5rem}.compose{margin:12px 0}
.thread{display:flex;flex-direction:column;gap:10px;margin:12px 0}
.msg{max-width:85%;border-radius:14px;padding:8px 12px;border:1px solid var(--line);
background:var(--card)}
.msg.me{align-self:flex-end;border-color:var(--acc)}.msg.fm{align-self:flex-start;border-color:var(--ok)}
.msg .meta{display:block;color:var(--mute);font-size:.78rem;margin-top:4px}
.dock{position:sticky;bottom:0;background:var(--bg);padding:8px 0 calc(8px + env(safe-area-inset-bottom));
border-top:1px solid var(--line)}
.dock form{margin:0}.dock textarea{min-height:3rem}
@media (max-width:480px){.msg{max-width:94%}.top nav a{padding:0 8px}}
"""

# Local time, live updates (SSE with a polling fallback), live tail and the alerts button.
# Pinned by hash in the CSP (see ``SCRIPT_CSP_HASH``); no server data is interpolated into
# it, so changing it here changes the hash with it.
SCRIPT = """
(function(){
var FB='Australia/Sydney',Z;
try{Z=Intl.DateTimeFormat().resolvedOptions().timeZone||FB;}catch(e){Z=FB;}
function f(o,d){try{return new Intl.DateTimeFormat('en-AU',Object.assign({timeZone:Z},o)).format(d);}
catch(e){return new Intl.DateTimeFormat('en-AU',Object.assign({timeZone:FB},o)).format(d);}}
function stamp(el){var d=new Date(el.getAttribute('datetime'));if(isNaN(d))return;
var s=(Date.now()-d.getTime())/1000,t;
if(s>=0&&s<60)t='just now';
else if(s>=0&&s<3600)t=Math.floor(s/60)+' min ago';
else if(s>=0&&s<21600)t=Math.floor(s/3600)+' h ago';
else{var h=f({hour:'numeric',minute:'2-digit'},d);
if(f({dateStyle:'short'},d)===f({dateStyle:'short'},new Date()))t='today '+h;
else t=f({weekday:'short',day:'numeric',month:'short'},d)+', '+h;}
el.textContent=t;el.title=f({dateStyle:'medium',timeStyle:'short'},d)+' ('+Z+')';}
function stamps(){var a=document.getElementsByTagName('time');for(var i=0;i<a.length;i++)stamp(a[i]);}
function sel(){var s=window.getSelection&&window.getSelection();return !!(s&&!s.isCollapsed);}
function busy(){var a=document.activeElement;if(sel())return true;
if(a&&/^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName))return true;
if(document.querySelector('details[open]'))return true;
var x=document.querySelectorAll('textarea,input[type=text]');
for(var i=0;i<x.length;i++)if(x[i].value)return true;
x=document.querySelectorAll('input[type=checkbox]');
for(var j=0;j<x.length;j++)if(x[j].checked!==x[j].defaultChecked)return true;
return false;}
function load(cb){fetch(location.href,{credentials:'same-origin',cache:'no-store'})
.then(function(r){return r.ok?r.text():null;}).then(function(t){
if(t)cb(new DOMParser().parseFromString(t,'text/html'));}).catch(function(){});}
var b=document.body,every=+b.getAttribute('data-refresh'),poll=+b.getAttribute('data-poll');
var tail=b.getAttribute('data-tail');
var th=document.getElementById('thread'),c=document.querySelector('main');
var last=th?th.innerHTML:c?c.textContent:null,live=false,want=false,es,seen=0;
function bottom(){window.scrollTo(0,document.documentElement.scrollHeight);}
if(th)bottom();
function refreshThread(){if(document.hidden||sel())return;load(function(doc){
var n=doc.getElementById('thread');if(!n||n.innerHTML===last||sel())return;last=n.innerHTML;
var near=window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-120;
th.innerHTML=n.innerHTML;stamps();if(near)bottom();});}
function refreshMain(){if(document.hidden||busy()){want=true;return;}
want=false;load(function(doc){
var m=doc.querySelector('main');if(!m||m.textContent===last||busy())return;last=m.textContent;
var y=window.scrollY;c.innerHTML=m.innerHTML;stamps();window.scrollTo(0,y);});}
function refreshAny(){if(th)refreshThread();else refreshMain();}
function connect(){es=new EventSource('/events');
es.onopen=function(){live=true;seen=Date.now();};
es.onerror=function(){live=false;};
es.addEventListener('hello',function(){seen=Date.now();live=true;refreshAny();});
es.addEventListener('ping',function(){seen=Date.now();});
es.addEventListener('desk',function(){seen=Date.now();if(!th)refreshMain();});
es.addEventListener('chat',function(){seen=Date.now();if(th)refreshThread();});}
if(poll&&th)setInterval(function(){if(!live)refreshThread();},poll);
else if(every&&c)setInterval(function(){if(!live)refreshMain();},every);
if(!tail&&((poll&&th)||(every&&c))&&window.EventSource){connect();
setInterval(function(){if(live&&Date.now()-seen>45000){live=false;es.close();connect();}
if(want&&!th&&!document.hidden)refreshMain();},2000);
document.addEventListener('visibilitychange',function(){if(!document.hidden&&want&&!th)refreshMain();});}
if(tail){var pre=document.getElementById('tail'),st=document.getElementById('tail-status'),
url='/w/'+encodeURIComponent(tail)+'/out';
function tick(){if(document.hidden)return;fetch(url,{credentials:'same-origin',cache:'no-store'})
.then(function(r){return r.json();}).then(function(j){
if(!j.ok){st.textContent='Cannot read output: '+j.error;return;}
var near=window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-120;
if(pre.textContent!==j.text){pre.textContent=j.text;if(near)bottom();}
st.textContent='Live · updated '+new Date().toLocaleTimeString();})
.catch(function(){st.textContent='Connection lost, retrying…';});}
tick();setInterval(tick,3000);document.addEventListener('visibilitychange',tick);}
var ab=document.getElementById('alerts');
function key(s){s=s.replace(/-/g,'+').replace(/_/g,'/');s+='='.repeat((4-s.length%4)%4);
var r=atob(s),a=new Uint8Array(r.length);for(var i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a;}
function post(path,obj,csrf){return fetch(path,{method:'POST',credentials:'same-origin',
headers:{'content-type':'application/json','x-csrf':csrf},body:JSON.stringify(obj)});}
function note(t){var an=document.getElementById('alerts-note');if(an)an.textContent=t;}
if(ab){var ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
var standalone=window.navigator.standalone===true||(window.matchMedia&&matchMedia('(display-mode: standalone)').matches);
if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){
if(ios&&!standalone)note('To get alerts on iPhone or iPad, add the desk to the Home Screen (Share, Add to Home Screen; iOS 16.4 or later), then open it from there.');
}else{
navigator.serviceWorker.register('/sw.js').then(function(reg){
function show(on){ab.hidden=false;ab.textContent=on?'Alerts on':'Alerts off';ab.setAttribute('aria-pressed',on?'true':'false');}
reg.pushManager.getSubscription().then(function(sub){show(!!sub);});
ab.onclick=function(){reg.pushManager.getSubscription().then(function(sub){
return fetch('/push/key',{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(k){
if(sub){return post('/push/unsubscribe',{endpoint:sub.endpoint},k.csrf).then(function(){return sub.unsubscribe();})
.then(function(){show(false);note('');});}
if(!k.enabled){note('Alerts are not set up on the server.');return;}
return Notification.requestPermission().then(function(p){
if(p!=='granted'){note('Notifications are blocked for this site in the browser settings.');return;}
return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key(k.key)}).then(function(s){
return post('/push/subscribe',s.toJSON(),k.csrf).then(function(r){
if(!r.ok){s.unsubscribe();note('Could not save the subscription.');return;}show(true);note('');});});});
});}).catch(function(){note('Could not change alerts. Try again.');});};
}).catch(function(){});}}
stamps();setInterval(stamps,30000);
})();
"""
SCRIPT_CSP_HASH = "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()

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
    ) -> None:
        self.csrf = csrf
        self.writable = writable
        self.nxt = nxt
        self.board_url = board_url.rstrip("/")
        self.tz = tz

    def form(self, action: str, inner: str, **hidden: str) -> str:
        if not self.writable:
            return ""
        fields = {"csrf": self.csrf, "next": self.nxt, **hidden}
        hid = "".join(
            f'<input type=hidden name={esc(k)} value="{esc(v)}">' for k, v in fields.items()
        )
        return f"<form method=post action='/act/{esc(action)}'>{hid}{inner}</form>"

    def link(self, url: str | None) -> str | None:
        """An http(s) URL, with loopback Lavish board links pointed at the tailnet origin."""
        safe = _safe_url(url)
        return BOARD_RE.sub(lambda m: f"{self.board_url}/{m[1]}", safe) if safe else None

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


def _page(title: str, body: str, active: str = "", attrs: str = "") -> str:
    def cur(name: str) -> str:
        return " aria-current=page" if active == name else ""

    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=color-scheme content='light dark'>"
        "<meta name=theme-color content='#b4531a'>"
        "<meta name=apple-mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-title content='Hive desk'>"
        "<link rel=manifest href='/manifest.webmanifest'>"
        "<link rel=icon href='/icons/icon-192.png'>"
        "<link rel=apple-touch-icon href='/icons/apple-touch-icon-180.png'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head>"
        f"<body{attrs}><header class=top><div><a class=brand href='/'>Hive desk</a>"
        f"<nav><a href='/'{cur('desk')}>Desk</a><a href='/chat'{cur('chat')}>Chat</a>"
        "<button id=alerts class=quiet hidden type=button>Alerts</button></nav>"
        f"</div></header><main>{body}</main><script>{SCRIPT}</script></body></html>"
    )


def _banner(snap: Snapshot, ctx: Ctx) -> str:
    stamp = f" Snapshot {ctx.time(snap.generated)}." if snap.generated else ""
    return (
        "<div class=banner role=status><strong>Read-only fallback.</strong> "
        f"{esc(snap.reason or 'No data.')}{stamp} Nothing here can be trusted until "
        "the desk and firstmate agree on the snapshot schema"
        f"{' (saw ' + esc(snap.schema) + ')' if snap.schema else ''}.</div>"
    )


_NEED_LABEL = {"hold": "Question on hold", "decision": "Decision", "merge": "Merge approval"}


def _need(n: NeedsYou, show_project: bool, ctx: Ctx) -> str:
    url = ctx.link(n.url)
    link = f' <a href="{esc(url)}" rel="noopener noreferrer">Open PR</a>' if url else ""
    title = n.title or n.ref.partition("/")[0]
    where = n.project if show_project and not title.lower().startswith(n.project.lower()) else ""
    lead = " · ".join(p for p in (where, title) if p)
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
            rid=new_request_id(),
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
    detail = f"{_NEED_LABEL.get(n.kind, n.kind)} · {n.ref}"
    return (
        f"<div class=need><span class=what>{esc(lead)}</span>{link}"
        f"<div>{ctx.text(n.text)}</div><span class=sub>{esc(detail)}</span>{act}</div>"
    )


def _compose(ctx: Ctx, nxt: str = "/chat") -> str:
    box = ctx.form(
        "chat",
        "<label>Message the first mate</label><textarea name=text required maxlength=4000 "
        "placeholder='Ask, delegate, or steer…'></textarea><button>Send</button>",
        rid=new_request_id(),
        next=nxt,
    )
    return f"<div class='card compose'>{box}</div>" if box else ""


def render_home(snap: Snapshot, desk: Desk | None, ctx: Ctx) -> str:
    attrs = " data-refresh=30000"
    if desk is None:
        return _page("Hive desk", f"<h1>Hive desk</h1>{_banner(snap, ctx)}", "desk", attrs)
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
    stamp = f"<p class=mute>Updated {ctx.time(desk.generated)}</p>" if desk.generated else ""
    return _page(
        "Hive desk",
        f"<h1>Hive desk</h1>{stamp}{_compose(ctx)}<h2>Needs you ({len(desk.needs_you)})</h2>{needs}"
        f"<h2>Projects</h2><div class=grid>{''.join(cards)}</div><p class=mute id=alerts-note></p>",
        "desk",
        attrs,
    )


def _crew(c: Crew, ctx: Ctx) -> str:
    controls = ctx.form(
        "control",
        "<button class='quiet inline' name=verb value=interrupt>Interrupt…</button>"
        "<button class='quiet inline' name=verb value=relaunch>Relaunch…</button>",
        task=c.id,
    )
    return (
        f"<div class=row><span class=tag>{esc(c.state)}</span><strong>{esc(c.title or c.id)}"
        f"</strong> <span class=mute>{esc(c.detail)}</span>"
        f"<span class=sub>{esc(c.id)} · {esc(c.kind)} · {esc(c.harness)} · "
        f"<a href='/w/{esc(quote(c.id, safe=''))}'>Watch live</a></span>{controls}</div>"
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
) -> str:
    attrs = " data-refresh=30000"
    if project is None:
        body = _banner(snap, ctx) if not snap.ok else "<p>Unknown project.</p>"
        return _page(name, f"<p><a href='/'>← Desk</a></p>{body}", "", attrs)
    needs = (
        "".join(_need(n, False, ctx) for n in project.needs_you)
        or "<p class=mute>Nothing needs you.</p>"
    )
    titles = {r.id: r.title for r in project.rows}
    rows = []
    for r in project.rows:
        extra = [r.id]
        if r.hold:
            extra.append(f"held: {esc(r.hold)}")
        if r.blocked_by:
            extra.append("blocked by " + esc(", ".join(titles.get(b) or b for b in r.blocked_by)))
        pr = ctx.link(r.pr_url)
        if pr:
            extra.append(f'<a href="{esc(pr)}" rel="noopener noreferrer">PR</a>')
        sub = f"<span class=sub>{' · '.join(extra)}</span>"
        rows.append(
            f"<div class=row><span class=tag>{esc(r.state.replace('_', ' '))}</span>"
            f"<strong>{esc(r.title or r.id)}</strong>{sub}"
            f"{_ticket_edit(r, project.name, ctx)}</div>"
        )
    crews = "".join(_crew(c, ctx) for c in project.crews) or "<p class=mute>No live crews.</p>"
    return _page(
        f"{name} · Hive desk",
        f"<p><a href='/'>← Desk</a></p><h1>{esc(name)}</h1>"
        f"<h2>Needs you ({len(project.needs_you)})</h2>"
        f"{needs}<h2>Crews</h2>{crews}<h2>Backlog ({len(project.rows)})</h2>"
        f"{_new_ticket(project.name, ctx)}{''.join(rows) or '<p class=mute>Empty.</p>'}",
        "",
        attrs,
    )


_KIND_LABEL = {
    "chat": "",
    "ticket": "ticket request",
    "merge": "merge word",
    "decision": "answer",
}
_STATE_LABEL = {
    "pending": "Sent · waiting for the first mate",
    "seen": "Read by the first mate",
    "replied": "Answered",
}


def _thread(view: ChatView, ctx: Ctx) -> str:
    if not view.available:
        return "<p class=mute>Messages are unavailable (firstmate inbox script did not answer).</p>"
    msgs = []
    for r in sorted(view.receipts, key=lambda r: r.at):  # oldest first, newest at the bottom
        kind = f"<span class=tag>{esc(_KIND_LABEL[r.kind])}</span>" if _KIND_LABEL[r.kind] else ""
        msgs.append(
            f"<div class='msg me'>{kind}<div class=bubble>{esc(r.body)}</div>"
            f"<span class=meta>{ctx.time(r.at)} · {esc(_STATE_LABEL[r.state])}</span></div>"
        )
        if r.reply is not None:
            msgs.append(
                f"<div class='msg fm'><span class=tag>first mate</span>"
                f"<div class=bubble>{esc(r.reply)}</div>"
                f"<span class=meta>{ctx.time(r.reply_at)}</span></div>"
            )
    out = "".join(msgs) or "<p class=mute>No messages yet. Say hello below.</p>"
    if view.omitted:
        out = "<p class=mute>Older entries omitted: " + esc("; ".join(view.omitted)) + "</p>" + out
    return out


def render_watch(crew: Crew, ctx: Ctx) -> str:
    return _page(
        f"{crew.id} · Hive desk",
        f"<p><a href='/'>← Desk</a></p><h1>{esc(crew.title or crew.id)}</h1>"
        f"<p class=mute><span class=tag>{esc(crew.state)}</span>{esc(crew.detail)} · "
        f"{esc(crew.id)} · {esc(crew.harness)}</p>"
        "<p class=mute id=tail-status role=status>Loading…</p>"
        "<pre class=tail id=tail aria-label='Recent output' aria-live=off></pre>",
        "",
        f" data-tail='{esc(crew.id)}'",
    )


def render_chat(view: ChatView, ctx: Ctx) -> str:
    if view.can_receive is False:
        status = (
            "<div class=banner role=status>The first mate session is not receiving right now. "
            "Notes are saved and will be read when it is back.</div>"
        )
    else:
        status = ""
    form = ctx.form(
        "chat",
        "<label for=chat-text>Message the first mate</label><textarea id=chat-text name=text "
        "required maxlength=4000 placeholder='Ask, delegate, or steer…'></textarea>"
        "<button>Send</button>",
        rid=new_request_id(),
    )
    return _page(
        "Chat · Hive desk",
        f"<h1>Chat with the first mate</h1>{status}"
        f"<div class=thread id=thread aria-live=polite>{_thread(view, ctx)}</div>"
        f"<div class=dock>{form}</div>",
        "chat",
        " data-poll=4000",
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
    return render_result(out.action.replace("-", " ").capitalize(), out.summary, nxt, True)
