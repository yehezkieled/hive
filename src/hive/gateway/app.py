"""ASGI app: auth middleware, the desk pages, and the owner's write actions."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from hive.gateway import actions, pages
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.auth import forbidden, is_authorised, is_same_origin_write, method_not_allowed
from hive.gateway.chat import load_chat
from hive.gateway.desk import Desk, build_desk
from hive.gateway.live import LiveHub
from hive.gateway.push import PushService, valid_subscription
from hive.gateway.quota import Quota, QuotaProvider
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import Snapshot, SnapshotProvider
from hive.gateway.tail import peek

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src '{pages.SCRIPT_CSP_HASH}'; connect-src 'self'; "
        "manifest-src 'self'; img-src 'self'; worker-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'"
    ),
}
MAX_BODY = 64 * 1024
PUSH_POSTS = ("/push/subscribe", "/push/unsubscribe")
ICONS_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "icons"
FONTS_DIR = Path(__file__).resolve().parent / "static" / "fonts"
FONT_FILES = frozenset(
    {
        "ibm-plex-mono-400.woff2",
        "ibm-plex-mono-500.woff2",
        "ibm-plex-mono-600.woff2",
        "ibm-plex-mono-700.woff2",
        "nunito.woff2",
        "nunito-sans.woff2",
    }
)
ICON_FILES = {
    "icon-192.png": "icon-192.png",
    "icon-512.png": "icon-512.png",
    "apple-touch-icon-180.png": "apple-touch-icon-180.png",
}
MANIFEST = {
    "name": "Hive desk",
    "short_name": "Hive desk",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#16140f",
    "theme_color": "#b4531a",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}
SERVICE_WORKER = """
self.addEventListener('install', function(){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('push', function(e){
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  var url = typeof d.url === 'string' && d.url.charAt(0) === '/' ? d.url : '/';
  e.waitUntil(self.registration.showNotification(String(d.title || 'Hive desk'), {
    body: String(d.body || ''), tag: d.tag ? String(d.tag) : undefined,
    icon: '/icons/icon-192.png', data: { url: url }
  }));
});
self.addEventListener('notificationclick', function(e){
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function(list){
    for (var i = 0; i < list.length; i++) {
      if ('focus' in list[i]) { list[i].navigate(url); return list[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
"""
NEXT_RE = re.compile(r"^/(chat|p/[A-Za-z0-9%._~-]{1,200}|\?focus=[A-Za-z0-9%._~-]{1,200})?$")


def _safe_next(value: str) -> str:
    return value if NEXT_RE.fullmatch(value) else "/"


def _quote_project(name: str) -> str:
    return "/p/" + quote(name, safe="")


def create_app(
    settings: GatewaySettings | None = None,
    provider: SnapshotProvider | None = None,
    tokens: Tokens | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    provider = provider or SnapshotProvider(settings)
    quota = QuotaProvider(settings)
    tokens = tokens or Tokens()
    runs = RunOnce()
    push = PushService(settings.data_dir, f"mailto:{settings.owner_login}")
    hub = LiveHub(settings, provider, push.notify)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.live:
            hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def owner_only(request: Request, call_next) -> Response:
        if not is_authorised(request, settings):
            return forbidden()
        if request.method == "POST":
            path = request.url.path
            if not (path.startswith("/act/") or path in PUSH_POSTS):
                return method_not_allowed()
            if not is_same_origin_write(request):
                return forbidden()
        elif request.method not in ("GET", "HEAD"):
            return method_not_allowed()
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/fonts/") and response.status_code == 200:
            response.headers["Cache-Control"] = "private, max-age=604800"
        return response

    def make_ctx(snap: Snapshot, nxt: str, q: Quota | None) -> pages.Ctx:
        return pages.Ctx(
            tokens.csrf(),
            writable=snap.ok,
            nxt=nxt,
            board_url=settings.board_url,
            tz=settings.default_tz,
            quota=q,
        )

    async def ctx_for(snap: Snapshot, nxt: str) -> pages.Ctx:
        return make_ctx(snap, nxt, await quota.get())

    @app.get("/", response_class=HTMLResponse)
    async def home(focus: str = "") -> HTMLResponse:
        snap, q = await asyncio.gather(provider.get(), quota.get())
        desk = build_desk(snap.data) if snap.data is not None else None
        selected = focus if desk is not None and focus in desk.projects else None
        nxt = "/?focus=" + quote(selected, safe="") if selected else "/"
        ctx = make_ctx(snap, nxt, q)
        return HTMLResponse(pages.render_home(snap, desk, ctx, selected))

    @app.get("/p/{name}", response_class=HTMLResponse)
    async def project(name: str) -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        proj = desk.projects.get(name) if desk else None
        status = 200 if (proj or desk is None) else 404
        ctx = await ctx_for(snap, _quote_project(name))
        return HTMLResponse(pages.render_project(name, snap, proj, ctx), status_code=status)

    @app.get("/chat", response_class=HTMLResponse)
    async def chat() -> HTMLResponse:
        snap = await provider.get()
        view = await load_chat(settings)
        return HTMLResponse(pages.render_chat(view, await ctx_for(snap, "/chat")))

    @app.get("/w/{task}", response_class=HTMLResponse)
    async def watch(task: str) -> HTMLResponse:
        snap = await provider.get()
        crew = _find_crew(snap, task)
        if crew is None:
            return pages_error("Not found", "That worker is not listed.", "/", 404)
        return HTMLResponse(pages.render_watch(crew, await ctx_for(snap, "/")))

    @app.get("/w/{task}/out")
    async def watch_out(task: str) -> Response:
        snap = await provider.get()
        if _find_crew(snap, task) is None:
            return JSONResponse({"ok": False, "error": "not listed"}, status_code=404)
        try:
            text = await peek(settings, task)
        except ActionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)
        return JSONResponse({"ok": True, "text": text, "at": int(time.time())})

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream():
            q = hub.subscribe()
            try:
                yield "retry: 3000\nevent: hello\ndata: {}\n\n"
                while True:
                    try:
                        yield await asyncio.wait_for(q.get(), 15)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield "event: ping\ndata: {}\n\n"
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/push/key")
    async def push_key() -> Response:
        try:
            key = await asyncio.to_thread(push.public_key)
        except Exception:
            return JSONResponse({"enabled": False})
        return JSONResponse({"enabled": True, "key": key, "csrf": tokens.csrf()})

    async def _push_body(request: Request) -> dict | None:
        raw = await request.body()
        if len(raw) > MAX_BODY or not tokens.check_csrf(request.headers.get("x-csrf", "")):
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    @app.post("/push/subscribe")
    async def push_subscribe(request: Request) -> Response:
        data = await _push_body(request)
        sub = valid_subscription(data)
        if sub is None:
            return forbidden()
        if not await asyncio.to_thread(push.add, sub):
            return JSONResponse({"ok": False, "error": "too many devices"}, status_code=409)
        actions.audit("push-subscribe", "-", "added")
        await push.notify_one(sub, "Alerts are on", "You will be pinged when something needs you.")
        return JSONResponse({"ok": True})

    @app.post("/push/unsubscribe")
    async def push_unsubscribe(request: Request) -> Response:
        data = await _push_body(request)
        endpoint = data.get("endpoint") if data else None
        if not isinstance(endpoint, str):
            return forbidden()
        await asyncio.to_thread(push.remove, endpoint)
        actions.audit("push-subscribe", "-", "removed")
        return JSONResponse({"ok": True})

    @app.get("/sw.js")
    async def service_worker() -> Response:
        return Response(
            SERVICE_WORKER,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/icons/{name}")
    async def icon(name: str) -> Response:
        fname = ICON_FILES.get(name)
        if fname is None or not (ICONS_DIR / fname).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((ICONS_DIR / fname).read_bytes(), media_type="image/png")

    @app.get("/fonts/{name}")
    async def font(name: str) -> Response:
        if name not in FONT_FILES or not (FONTS_DIR / name).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((FONTS_DIR / name).read_bytes(), media_type="font/woff2")

    @app.post("/act/{name}")
    async def act(name: str, request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_BODY:
            return pages_error("Request too large", "The request was too large.", "/", 413)
        form = {
            k: v[0]
            for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()
        }
        nxt = _safe_next(form.get("next", "/"))
        wants_json = request.headers.get("accept", "") == "application/json"
        if not tokens.check_csrf(form.get("csrf", "")):
            return forbidden()
        try:
            snap = await provider.get(fresh=True)
            if snap.data is None:
                raise ActionError(
                    "The desk is read-only until firstmate's snapshot is readable.", 503
                )
            desk = build_desk(snap.data)
            result = await _dispatch(name, form, desk, settings, tokens, runs, nxt)
        except ActionError as exc:
            actions.audit(name, "-", "refused", status=exc.status)
            if wants_json:
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
            return pages_error(name, str(exc), nxt, exc.status)
        if isinstance(result, HTMLResponse):  # a confirm page
            return result
        if wants_json:
            return JSONResponse({"ok": True, "message": result.summary})
        return HTMLResponse(pages.render_outcome(result, nxt))

    return app


def _find_crew(snap: Snapshot, task: str):
    if snap.data is None:
        return None
    desk = build_desk(snap.data)
    return next((c for p in desk.projects.values() for c in p.crews if c.id == task), None)


def pages_error(title: str, message: str, nxt: str, status: int) -> HTMLResponse:
    return HTMLResponse(pages.render_result(title, message, nxt, False), status_code=status)


def _need(desk: Desk, kind: str, ref: str):
    for n in desk.needs_you:
        if n.kind == kind and n.ref == ref:
            return n
    raise ActionError("That item is no longer waiting on you. Refresh the desk.", 409)


async def _dispatch(
    name: str,
    form: dict[str, str],
    desk: Desk,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
) -> Outcome | HTMLResponse:
    """Run one action. Returns its outcome, or a confirm page for step-up actions."""
    owner = settings.owner_login
    rid = form.get("rid", "")

    def ctx() -> pages.Ctx:
        return pages.Ctx(tokens.csrf(), True, nxt, settings.board_url, settings.default_tz)

    if name == "answer":
        task = actions.check_id(form.get("task", ""), "task id")
        if not actions.REQUEST_ID_RE.fullmatch(rid):
            raise ActionError("invalid request id")
        release = form.get("release") == "1"
        answer_body = actions.answer_body(form.get("text", ""))

        async def answer() -> Outcome:
            _need(desk, "hold", task)
            return await actions.answer_hold(settings, task, answer_body, release)

        return await runs.run(rid, answer)

    if name == "decision":
        task = actions.check_id(form.get("task", ""), "task id")
        key = actions.check_id(form.get("key", ""), "decision key")
        _need(desk, "decision", f"{task}/{key}")
        text = actions.check_text(form.get("text", ""), "answer")
        body = actions.decision_note_body(task, key, text, owner)
        return await actions.send_note(settings, body, rid, "decision", f"{task}/{key}")

    if name == "chat":
        text = actions.check_text(form.get("text", ""), "message")
        return await actions.send_note(settings, text, rid, "chat", "-")

    if name == "delegate":
        text = actions.check_text(form.get("text", ""), "goal")
        focus = form.get("project", "")
        if not focus:  # no project focus: a plain message to the first mate
            return await actions.send_note(settings, text, rid, "delegate", "-")
        project = desk.projects.get(focus)
        if project is None:
            raise ActionError("Unknown project. Refresh the desk.", 404)
        to = "second mate" if project.mate else "first mate"
        body = actions.delegate_body(project.name, to, text, owner)
        return await actions.send_note(settings, body, rid, "delegate", project.name)

    if name == "ticket":
        return await _ticket(form, desk, settings)

    if name == "merge":
        task = actions.check_id(form.get("task", ""), "task id")
        need = _need(desk, "merge", task)
        if not tokens.check_step_up(form.get("step", ""), "merge", task):
            step = tokens.step_up("merge", task)
            return HTMLResponse(
                pages.render_confirm(
                    "Give merge word",
                    f"This records your word to merge {task}"
                    f"{' (' + need.url + ')' if need.url else ''} as a note to the first mate. "
                    "The website never merges; the first mate does.",
                    "merge",
                    ctx(),
                    {"task": task, "rid": rid or actions.new_request_id(), "step": step},
                )
            )
        body = actions.merge_word_body(task, need.url or "", owner)
        return await actions.send_note(settings, body, rid, "merge", task)

    if name == "control":
        task = actions.check_id(form.get("task", ""), "task id")
        verb = form.get("verb", "")
        if verb not in actions.CONTROL_VERBS:
            raise ActionError("unsupported control verb")
        if not any(c.id == task for p in desk.projects.values() for c in p.crews):
            raise ActionError("That worker is not listed. Refresh the desk.", 409)
        if not actions.REQUEST_ID_RE.fullmatch(rid) or not tokens.check_step_up(
            form.get("step", ""), "control", f"{task}:{verb}:{rid}"
        ):
            rid = actions.new_request_id()
            step = tokens.step_up("control", f"{task}:{verb}:{rid}")
            extra = {}
            inner_note = ""
            if verb == "relaunch":
                inner_note = form.get("note") or "Relaunched from the Hive website by the owner."
                extra["note"] = inner_note
            return HTMLResponse(
                pages.render_confirm(
                    f"{verb.capitalize()} {task}",
                    f"This will {verb} the worker {task}."
                    + (
                        " Its agent is replaced in the same worktree." if verb == "relaunch" else ""
                    ),
                    "control",
                    ctx(),
                    {"task": task, "verb": verb, "rid": rid, "step": step, **extra},
                )
            )
        note = (
            actions.check_text(form.get("note", ""), "relaunch note", 1000)
            if verb == "relaunch"
            else None
        )
        return await runs.run(rid, lambda: actions.control(settings, task, verb, note))

    raise ActionError("unknown action", 404)


async def _ticket(form: dict[str, str], desk: Desk, settings: GatewaySettings) -> Outcome:
    mode = form.get("mode", "")
    project = desk.projects.get(form.get("project", ""))
    if project is None:
        raise ActionError("Unknown project.", 404)
    if mode == "create":
        title = actions.check_line(form.get("title", ""), "title")
        details = form.get("text", "").replace("\r\n", "\n").strip()
        text = actions.check_text(f"{title}\n\n{details}" if details else title, "ticket")
        body = actions.ticket_request_body(
            "create", "(new)", project.name, "new", text, settings.owner_login
        )
        return await actions.send_note(
            settings, body, form.get("rid", ""), "ticket-create", project.name
        )
    if mode == "edit":
        ticket = actions.check_id(form.get("ticket", ""), "ticket id")
        row = next((r for r in project.rows if r.id == ticket and r.state != "done"), None)
        if row is None:
            raise ActionError("That ticket is not editable here. Refresh the desk.", 409)
        field = form.get("field", "")
        if field not in actions.TICKET_FIELDS:
            raise ActionError("unsupported ticket field")
        text = actions.check_text(form.get("text", ""), "new text")
        body = actions.ticket_request_body(
            "edit", ticket, project.name, field, text, settings.owner_login
        )
        return await actions.send_note(settings, body, form.get("rid", ""), "ticket-edit", ticket)
    raise ActionError("unknown ticket action")
