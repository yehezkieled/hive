"""ASGI app: auth middleware, the desk pages, and the owner's write actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
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

from hive.gateway import actions, fmconfig, pages
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.auth import forbidden, is_authorised, is_same_origin_write, method_not_allowed
from hive.gateway.chat import load_chat
from hive.gateway.describe import Describer, Item
from hive.gateway.desk import Desk, build_desk
from hive.gateway.live import LiveHub
from hive.gateway.push import PushService, valid_subscription
from hive.gateway.quota import Quota, QuotaProvider
from hive.gateway.repos import RepoNames
from hive.gateway.reviews import Review, read_reviews, session_file
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import Snapshot, SnapshotProvider, project_notes
from hive.gateway.tail import peek
from hive.gateway.wake import WakeService

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
REVIEW_ACTIONS = ("review-close", "review-close-old")
# Every name `POST /act/{name}` answers to. A test times each one; add new actions here.
ACT_NAMES = (
    "chat",
    "answer",
    "decision",
    "delegate",
    "ticket",
    "merge",
    "control",
    *REVIEW_ACTIONS,
)
MAX_REVIEW_KEYS = 100
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
NEXT_RE = re.compile(r"^/(chat|config|p/[A-Za-z0-9%._~-]{1,200}|\?focus=[A-Za-z0-9%._~-]{1,200})?$")


def _safe_next(value: str) -> str:
    return value if NEXT_RE.fullmatch(value) else "/"


def _quote_project(name: str) -> str:
    return "/p/" + quote(name, safe="")


def create_app(
    settings: GatewaySettings | None = None,
    provider: SnapshotProvider | None = None,
    tokens: Tokens | None = None,
    describer: Describer | None = None,
    wake: WakeService | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    provider = provider or SnapshotProvider(settings)
    quota = QuotaProvider(settings)
    describer = describer or Describer(settings)
    repo_names = RepoNames(settings.fm_home / "projects")
    tokens = tokens or Tokens()
    runs = RunOnce()
    wake = wake or WakeService(settings)
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

    def make_ctx(
        snap: Snapshot,
        nxt: str,
        q: Quota | None,
        postures: fmconfig.Projects,
        desk: Desk | None = None,
        repos: dict[str, str] | None = None,
    ) -> pages.Ctx:
        return pages.Ctx(
            tokens.csrf(),
            writable=snap.ok,
            nxt=nxt,
            board_url=settings.board_url,
            tz=settings.default_tz,
            quota=q,
            descriptions=_cached_descriptions(describer, desk),
            repos=repos,
            can_close_reviews=settings.lavish_axi is not None,
            project_notes=project_notes(snap),
            postures=postures,
            firstmate=wake.status.peek(),
            wake_enabled=wake.available,
        )

    async def ctx_for(snap: Snapshot, nxt: str, names: list[str] | None = None) -> pages.Ctx:
        repos = await repo_names.get(names or [])
        postures = await asyncio.to_thread(fmconfig.read_projects, settings.fm_home)
        return make_ctx(snap, nxt, await quota.get(), postures, repos=repos)

    @app.get("/", response_class=HTMLResponse)
    async def home(focus: str = "") -> HTMLResponse:
        snap, q = await asyncio.gather(provider.get(), quota.get())
        desk = build_desk(snap.data) if snap.data is not None else None
        selected = focus if desk is not None and focus in desk.projects else None
        nxt = "/?focus=" + quote(selected, safe="") if selected else "/"
        names = set(desk.projects) if desk else set()
        postures = await asyncio.to_thread(fmconfig.read_projects, settings.fm_home)
        ctx = make_ctx(snap, nxt, q, postures, desk, await repo_names.get(names))
        reviews = await asyncio.to_thread(read_reviews, settings.lavish_state, names)
        return HTMLResponse(pages.render_home(snap, desk, ctx, selected, reviews))

    @app.get("/describe")
    async def describe(p: str = "", id: str = "") -> Response:
        """The agent's plain description of one lane item: ready, still pending, or unavailable.

        The item is looked up in the current snapshot, never taken from the request, so only a
        real backlog item can start a turn. Behind the same owner gate as every page.
        """
        snap = await provider.get()
        item = _lane_item(build_desk(snap.data), p, id) if snap.data is not None else None
        if item is None:
            return JSONResponse({"state": "unavailable"}, status_code=404)
        state, text = await describer.request(item)
        return JSONResponse({"state": state, "text": text} if text else {"state": state})

    @app.get("/p/{name}", response_class=HTMLResponse)
    async def project(name: str) -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        proj = desk.projects.get(name) if desk else None
        status = 200 if (proj or desk is None) else 404
        ctx = await ctx_for(snap, _quote_project(name), [name])
        return HTMLResponse(pages.render_project(name, snap, proj, ctx), status_code=status)

    @app.get("/config", response_class=HTMLResponse)
    async def config() -> HTMLResponse:
        snap = await provider.get()
        cfg = await asyncio.to_thread(fmconfig.load, settings.fm_home)
        return HTMLResponse(pages.render_config(cfg, await ctx_for(snap, "/config")))

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
        if name == "chat":  # needs no snapshot, so a send never waits on one
            return await _chat(form, settings, nxt, wants_json)
        if name in REVIEW_ACTIONS:  # Lavish state, not the snapshot
            return await _review_action(
                name, form, settings, tokens, runs, nxt, wants_json, hub.publish
            )
        rid = form.get("rid", "")
        try:
            if name in FIRSTMATE_ACTIONS:  # must work while firstmate's snapshot is unreadable
                result = await _firstmate(name, form, wake, settings, tokens, runs)
                if isinstance(result, Confirm):
                    return confirm_response(result, nxt, tokens, wants_json)
                if wants_json:
                    return JSONResponse({"ok": True, "message": result.summary})
                return HTMLResponse(pages.render_outcome(result, nxt))
            try:
                # The cached snapshot is enough to check an id against; a write never waits on a
                # fresh fleet snapshot (about 5 s on a real fleet).
                snap = await provider.quick(actions.SNAPSHOT_WAIT_S)
                if snap is None or snap.data is None:
                    raise ActionError(
                        "The desk is read-only until firstmate's snapshot is readable.", 503
                    )
                result = await _dispatch(
                    name,
                    form,
                    build_desk(snap.data),
                    settings,
                    tokens,
                    runs,
                    nxt,
                    lambda: hub.publish("desk"),
                )
            except ActionError as exc:
                # A repeat of a request already taken (the card is gone by now, or the snapshot
                # moved on) reports that request's outcome instead of a stale refusal.
                if exc.status not in (409, 503) or not runs.known(rid):
                    raise
                result = await runs.run(rid, None, actions.ACT_WAIT_S)
        except ActionError as exc:
            actions.audit(name, "-", "refused", status=exc.status)
            if wants_json:
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
            return pages_error(name, str(exc), nxt, exc.status)
        if isinstance(result, Outcome) and not result.pending:
            hub.publish("desk")
        if isinstance(result, Confirm):
            return confirm_response(result, nxt, tokens, wants_json)
        if wants_json:
            return JSONResponse(
                {"ok": True, "message": result.summary, "pending": result.pending},
                status_code=202 if result.pending else 200,
            )
        return HTMLResponse(pages.render_outcome(result, nxt))

    return app


async def _chat(
    form: dict[str, str], settings: GatewaySettings, nxt: str, wants_json: bool
) -> Response:
    try:
        text = actions.check_text(form.get("text", ""), "message")
        result = await actions.send_note_bounded(settings, text, form.get("rid", ""), "chat", "-")
    except ActionError as exc:
        actions.audit("chat", "-", "refused", status=exc.status)
        if wants_json:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        return pages_error("chat", str(exc), nxt, exc.status)
    if wants_json:
        return JSONResponse(
            {"ok": True, "message": result.summary, "pending": result.pending},
            status_code=202 if result.pending else 200,
        )
    return HTMLResponse(pages.render_outcome(result, nxt))


def _step_subject(name: str, keys: list[str]) -> str:
    """What a confirm step is bound to: the action and exactly the pages it will end."""
    return f"{name}:{hashlib.sha256(chr(31).join(sorted(keys)).encode()).hexdigest()[:24]}"


def _titles(reviews: list[Review], keys: list[str]) -> str:
    names = {r.key: r.title for r in reviews}
    shown = [f"“{names.get(k, k)}”" for k in keys[:8]]
    more = f" and {len(keys) - 8} more" if len(keys) > 8 else ""
    return ", ".join(shown) + more


async def _review_action(
    name: str,
    form: dict[str, str],
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    wants_json: bool,
    publish: Callable[[str], None],
) -> Response:
    """End Lavish review sessions: ``review-close`` one page, ``review-close-old`` every page
    that looks done. Nothing runs until the owner's confirm step returns the step token for
    exactly these pages. The session's file is read from Lavish's state, never from the request.
    """

    def fail(exc: ActionError) -> Response:
        actions.audit(name, "-", "refused", status=exc.status)
        if wants_json:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        return pages_error(name, str(exc), nxt, exc.status)

    def done(summary: str, pending: bool = False) -> Response:
        if not pending:
            publish("desk")
        if wants_json:
            return JSONResponse(
                {"ok": True, "message": summary, "pending": pending},
                status_code=202 if pending else 200,
            )
        return HTMLResponse(pages.render_outcome(Outcome(name, "-", summary), nxt))

    try:
        reviews = await asyncio.to_thread(read_reviews, settings.lavish_state, set())
        if name == "review-close":
            key = actions.check_id(form.get("key", ""), "review page")
            keys = [key] if any(r.key == key for r in reviews) else []
            if not keys:
                return done("That review page is already closed.")
            label, what = (
                "Close review page",
                f"end the Lavish review session {_titles(reviews, keys)}",
            )
        else:
            if "keys" in form:  # the confirmed list; pages closed since are skipped in run()
                keys = [k for k in form["keys"].split(",") if k]
                if len(keys) > MAX_REVIEW_KEYS or not all(actions.ID_RE.fullmatch(k) for k in keys):
                    raise ActionError("invalid review page list")
            else:
                keys = [r.key for r in reviews if r.stale]
            if not keys:
                return done("No review pages look done.")
            label, what = (
                "Close old review pages",
                (
                    f"end {len(keys)} Lavish review session{'s' if len(keys) != 1 else ''}: "
                    f"{_titles(reviews, keys)}"
                ),
            )
        subject = _step_subject(name, keys)
        rid = form.get("rid", "")
        if not tokens.check_step_up(form.get("step", ""), "review", subject):
            step = tokens.step_up("review", subject)
            rid = rid if actions.REQUEST_ID_RE.fullmatch(rid) else actions.new_request_id()
            extra = {"keys": ",".join(keys)} if name == "review-close-old" else {}
            if wants_json:
                return JSONResponse(
                    {
                        "ok": True,
                        "confirm": True,
                        "step": step,
                        "rid": rid,
                        "extra": extra,
                        "label": "Tap again to close"
                        if len(keys) == 1
                        else f"Tap again: close {len(keys)}",
                    }
                )
            ctx = pages.Ctx(tokens.csrf(), True, nxt, settings.board_url, settings.default_tz)
            return HTMLResponse(
                pages.render_confirm(
                    label,
                    f"This will {what}. The pages themselves are kept.",
                    name,
                    ctx,
                    {"key": keys[0], "rid": rid, "step": step, **extra}
                    if name == "review-close"
                    else {"rid": rid, "step": step, **extra},
                )
            )
        if not actions.REQUEST_ID_RE.fullmatch(rid):
            raise ActionError("invalid request id")

        async def run() -> Outcome:
            ended = failed = 0
            for key in keys:
                file = await asyncio.to_thread(session_file, settings.lavish_state, key)
                if file is None:  # closed meanwhile: nothing to do
                    continue
                if await actions.end_review(settings, key, str(file)):
                    ended += 1
                else:
                    failed += 1
            text = f"Closed {ended} review page{'s' if ended != 1 else ''}."
            if failed:
                text += f" {failed} could not be closed."
                if not ended:
                    raise ActionError(text, 502)
            return Outcome(name, "-", text)

        result = await runs.run(rid, run, actions.ACT_WAIT_S, (name, "-"), lambda: publish("desk"))
    except ActionError as exc:
        return fail(exc)
    return done(result.summary, result.pending)


def _lane_item(desk: Desk, project: str, item_id: str) -> Item | None:
    """What a description is generated from: the lane item's snapshot record, if it exists."""
    proj = desk.projects.get(project)
    row = next((r for r in proj.rows if r.id == item_id), None) if proj else None
    if row is None:
        return None
    return Item(
        project, row.id, row.title, row.body, row.hold or "", row.state, tuple(row.blocked_by)
    )


def _cached_descriptions(describer: Describer, desk: Desk | None) -> dict[str, str]:
    """Cached text for every lane item, read from memory only (a page never waits on one)."""
    if desk is None:
        return {}
    out: dict[str, str] = {}
    for g in desk.backlog:
        for ref in [*(n.ref for n in g.waiting), *(r.id for r in g.queued)]:
            item = _lane_item(desk, g.project, ref)
            text = describer.cached(item) if item else None
            if item and text:
                out[item.key] = text
    return out


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


@dataclass(frozen=True)
class Confirm:
    """A step-up the owner must pass before an action runs: a page, or JSON for the page script."""

    title: str
    detail: str
    action: str
    hidden: dict[str, str]  # carries ``step`` and ``rid``; the rest is echoed back unchanged
    label: str  # the armed button's text
    warn: bool = False  # the page script shows ``detail`` beside the armed button


def confirm_response(c: Confirm, nxt: str, tokens: Tokens, wants_json: bool) -> Response:
    if wants_json:
        extra = {k: v for k, v in c.hidden.items() if k not in ("step", "rid")}
        body = {
            "ok": True,
            "confirm": True,
            "step": c.hidden["step"],
            "rid": c.hidden["rid"],
            "extra": extra,
            "label": c.label,
        }
        if c.warn:
            body["detail"] = c.detail
        return JSONResponse(body)
    ctx = pages.Ctx(tokens.csrf(), True, nxt, "", "")
    return HTMLResponse(pages.render_confirm(c.title, c.detail, c.action, ctx, c.hidden))


WAKE_STATUS = {"refused": 409, "rate-limited": 429, "unavailable": 503, "failed": 502}

# action -> (confirm title, confirm text, armed button label); both run through the same owner
# gate and step-up.
FIRSTMATE_ACTIONS = {
    "wake": (
        "Wake firstmate",
        "This runs the fleet-up firstmate step: it starts a firstmate session only if "
        "none is running, and never a second one.",
        "Tap again to wake",
    ),
    "restart": (
        "Restart firstmate session",
        "This STOPS the running firstmate session (the one claude process in the firstmate "
        "home; no worker or crew is touched) and then starts a fresh one with the fleet-up "
        "firstmate step. Anything it was doing in that session is lost. It refuses, "
        "stopping nothing, if firstmate was mid-turn in the last few minutes.",
        "Tap again to restart",
    ),
}


async def _firstmate(
    name: str,
    form: dict[str, str],
    wake: WakeService,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
) -> Outcome | Confirm:
    """Confirm step, then one wake or restart. The owner gate already ran in the middleware."""
    rid = form.get("rid", "")
    if not actions.REQUEST_ID_RE.fullmatch(rid) or not tokens.check_step_up(
        form.get("step", ""), name, f"firstmate:{rid}"
    ):
        rid = actions.new_request_id()
        step = tokens.step_up(name, f"firstmate:{rid}")
        title, detail, label = FIRSTMATE_ACTIONS[name]
        return Confirm(title, detail, name, {"rid": rid, "step": step}, label, warn=True)

    async def run() -> Outcome:
        act = wake.restart if name == "restart" else wake.wake
        result = await act(settings.owner_login, "desk")
        if not result.ok:
            raise ActionError(result.message, WAKE_STATUS.get(result.outcome, 502))
        return Outcome(name, "firstmate", result.message)

    return await runs.run(rid, run)


async def _dispatch(
    name: str,
    form: dict[str, str],
    desk: Desk,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    publish: Callable[[], None],
) -> Outcome | Confirm:
    """Run one action. Returns its outcome, or a ``Confirm`` for step-up actions.

    Nothing here waits longer than ``ACT_WAIT_S``: the work runs once per request id in
    ``runs``, and a run still going answers a pending outcome. The same id asked again gets
    the finished result, or the failure.
    """
    owner = settings.owner_login
    rid = form.get("rid", "")

    def submit(action: str, subject: str, start: Callable[[], Awaitable[Outcome]]):
        if not actions.REQUEST_ID_RE.fullmatch(rid):
            raise ActionError("invalid request id")
        return runs.run(rid, start, actions.ACT_WAIT_S, (action, subject), publish)

    def note(body: str, action: str, subject: str):
        return submit(
            action, subject, lambda: actions.send_note(settings, body, rid, action, subject)
        )

    if name == "answer":
        task = actions.check_id(form.get("task", ""), "task id")
        release = form.get("release") == "1"
        answer_body = actions.answer_body(form.get("text", ""))

        async def answer() -> Outcome:
            _need(desk, "hold", task)
            return await actions.answer_hold(settings, task, answer_body, release)

        return await submit("answer", f"{task}{' release' if release else ''}", answer)

    if name == "decision":
        task = actions.check_id(form.get("task", ""), "task id")
        key = actions.check_id(form.get("key", ""), "decision key")
        _need(desk, "decision", f"{task}/{key}")
        text = actions.check_text(form.get("text", ""), "answer")
        body = actions.decision_note_body(task, key, text, owner)
        return await note(body, "decision", f"{task}/{key}")

    if name == "delegate":
        text = actions.check_text(form.get("text", ""), "goal")
        focus = form.get("project", "")
        if not focus:  # no project focus: a plain message to the first mate
            return await note(text, "delegate", "-")
        await _worker_gate(settings)
        project = desk.projects.get(focus)
        if project is None:
            raise ActionError("Unknown project. Refresh the desk.", 404)
        to = "second mate" if project.mate else "first mate"
        body = actions.delegate_body(project.name, to, text, owner)
        return await note(body, "delegate", project.name)

    if name == "ticket":
        return await _ticket(form, desk, settings, note)

    if name == "merge":
        task = actions.check_id(form.get("task", ""), "task id")
        need = _need(desk, "merge", task)
        if not tokens.check_step_up(form.get("step", ""), "merge", task):
            return Confirm(
                "Give merge word",
                f"This records your word to merge {task}"
                f"{' (' + need.url + ')' if need.url else ''} as a note to the first mate. "
                "The website never merges; the first mate does.",
                "merge",
                {
                    "task": task,
                    "rid": rid or actions.new_request_id(),
                    "step": tokens.step_up("merge", task),
                },
                "Tap again to give the merge word",
            )
        projects = await asyncio.to_thread(fmconfig.read_projects, settings.fm_home)
        body = actions.merge_word_body(
            task,
            need.url or "",
            owner,
            fmconfig.posture_label(projects, need.project),
            "no (the website cannot see checks; firstmate decides)",
        )
        return await note(body, "merge", task)

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
            hidden = {"task": task, "verb": verb, "rid": rid, "step": step}
            if verb == "relaunch":
                hidden["note"] = (
                    form.get("note") or "Relaunched from the Hive website by the owner."
                )
            return Confirm(
                f"{verb.capitalize()} {task}",
                f"This will {verb} the worker {task}."
                + (" Its agent is replaced in the same worktree." if verb == "relaunch" else ""),
                "control",
                hidden,
                f"Tap again to {verb}",
            )
        note_text = (
            actions.check_text(form.get("note", ""), "relaunch note", 1000)
            if verb == "relaunch"
            else None
        )
        return await submit(
            "control", f"{task} {verb}", lambda: actions.control(settings, task, verb, note_text)
        )

    raise ActionError("unknown action", 404)


async def _worker_gate(settings: GatewaySettings) -> None:
    """A request that can start a worker is refused, not guessed, when firstmate's worker
    settings are missing or unreadable. The captain is told to fix the config."""
    cfg = await asyncio.to_thread(fmconfig.load, settings.fm_home)
    why = cfg.workers_ready
    if why:
        actions.audit("worker-config", "-", "refused", reason=why[:120])
        raise ActionError(
            f"Not sent: firstmate's worker settings are unusable ({why}). "
            "Ask the captain to fix them; Hive will not pick a harness or model itself.",
            409,
        )


async def _ticket(
    form: dict[str, str],
    desk: Desk,
    settings: GatewaySettings,
    note: Callable[[str, str, str], Awaitable[Outcome]],
) -> Outcome:
    mode = form.get("mode", "")
    project = desk.projects.get(form.get("project", ""))
    if project is None:
        raise ActionError("Unknown project.", 404)
    if mode == "create":
        await _worker_gate(settings)
        title = actions.check_line(form.get("title", ""), "title")
        details = form.get("text", "").replace("\r\n", "\n").strip()
        text = actions.check_text(f"{title}\n\n{details}" if details else title, "ticket")
        body = actions.ticket_request_body(
            "create", "(new)", project.name, "new", text, settings.owner_login
        )
        return await note(body, "ticket-create", project.name)
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
        return await note(body, "ticket-edit", ticket)
    raise ActionError("unknown ticket action")
