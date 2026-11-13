"""The desk's read routes, in groups: the pages, and the live stream with its Web Push
endpoints. ``create_app`` builds the services once and hands them to each group. The Run,
Sign-ins and Terminal routes are not here: they are still in ``app.py``."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from hive.gateway import actions, fmconfig, machine, pages
from hive.gateway.actions import ActionError, Tokens
from hive.gateway.auth import forbidden
from hive.gateway.chat import load_chat
from hive.gateway.describe import Describer, Item
from hive.gateway.desk import Desk, build_desk
from hive.gateway.live import LiveHub
from hive.gateway.push import PushService, valid_subscription
from hive.gateway.quota import Quota, QuotaProvider
from hive.gateway.repos import RepoNames
from hive.gateway.responses import pages_error
from hive.gateway.reviews import read_reviews
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import Snapshot, SnapshotProvider, project_notes
from hive.gateway.tail import peek
from hive.gateway.wake import WakeService
from hive.gateway.watchdog import Watchdog

MAX_BODY = 64 * 1024


def _quote_project(name: str) -> str:
    return "/p/" + quote(name, safe="")


@dataclass(frozen=True)
class Services:
    """What the route groups share, built once by ``create_app``."""

    settings: GatewaySettings
    provider: SnapshotProvider
    quota: QuotaProvider
    describer: Describer
    repo_names: RepoNames
    tokens: Tokens
    wake: WakeService
    watchdog: Watchdog
    monitor: machine.MachineMonitor
    push: PushService
    hub: LiveHub
    terminal: bool  # the Terminal page is switched on

    def make_ctx(
        self,
        snap: Snapshot,
        nxt: str,
        q: Quota | None,
        postures: fmconfig.Projects,
        desk: Desk | None = None,
        repos: dict[str, str] | None = None,
    ) -> pages.Ctx:
        return pages.Ctx(
            self.tokens.csrf(),
            writable=snap.ok,
            nxt=nxt,
            board_url=self.settings.board_url,
            tz=self.settings.default_tz,
            quota=q,
            descriptions=_cached_descriptions(self.describer, desk),
            repos=repos,
            can_close_reviews=self.settings.lavish_axi is not None,
            project_notes=project_notes(snap),
            postures=postures,
            firstmate=self.wake.status.peek(),
            wake_enabled=self.wake.available,
            watchdog=self.watchdog.store.view(),
            terminal=self.terminal,
        )

    async def ctx_for(self, snap: Snapshot, nxt: str, names: list[str] | None = None) -> pages.Ctx:
        repos = await self.repo_names.get(names or [])
        postures = await asyncio.to_thread(fmconfig.read_projects, self.settings.fm_home)
        return self.make_ctx(snap, nxt, await self.quota.get(), postures, repos=repos)


def add_page_routes(app: FastAPI, s: Services) -> None:
    """The Desk, a project, Config, Machine, Chat and a crew's live output."""

    @app.get("/", response_class=HTMLResponse)
    async def home(focus: str = "") -> HTMLResponse:
        snap, q = await asyncio.gather(s.provider.get(), s.quota.get())
        desk = build_desk(snap.data) if snap.data is not None else None
        selected = focus if desk is not None and focus in desk.projects else None
        nxt = "/?focus=" + quote(selected, safe="") if selected else "/"
        names = set(desk.projects) if desk else set()
        postures = await asyncio.to_thread(fmconfig.read_projects, s.settings.fm_home)
        ctx = s.make_ctx(snap, nxt, q, postures, desk, await s.repo_names.get(names))
        reviews = await asyncio.to_thread(read_reviews, s.settings.lavish_state, names)
        return HTMLResponse(pages.render_home(snap, desk, ctx, selected, reviews))

    @app.get("/describe")
    async def describe(p: str = "", id: str = "") -> Response:
        """The agent's plain description of one lane item: ready, still pending, or unavailable.

        The item is looked up in the current snapshot, never taken from the request, so only a
        real backlog item can start a turn. Behind the same owner gate as every page.
        """
        snap = await s.provider.get()
        item = _lane_item(build_desk(snap.data), p, id) if snap.data is not None else None
        if item is None:
            return JSONResponse({"state": "unavailable"}, status_code=404)
        state, text = await s.describer.request(item)
        return JSONResponse({"state": state, "text": text} if text else {"state": state})

    @app.get("/p/{name}", response_class=HTMLResponse)
    async def project(name: str) -> HTMLResponse:
        snap = await s.provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        proj = desk.projects.get(name) if desk else None
        status = 200 if (proj or desk is None) else 404
        ctx = await s.ctx_for(snap, _quote_project(name), [name])
        return HTMLResponse(pages.render_project(name, snap, proj, ctx), status_code=status)

    @app.get("/config", response_class=HTMLResponse)
    async def config() -> HTMLResponse:
        snap = await s.provider.get()
        cfg = await asyncio.to_thread(fmconfig.load, s.settings.fm_home)
        return HTMLResponse(pages.render_config(cfg, await s.ctx_for(snap, "/config")))

    @app.get("/machine", response_class=HTMLResponse)
    async def machine_page() -> HTMLResponse:
        """The chain that has to hold, read-only. Renders the cached readings at once; a
        stale cache starts a probe round behind this response, never in front of it."""
        snap = await s.provider.get()
        view = machine.build_view(s.monitor.peek(), time.time(), s.monitor.started_at)
        return HTMLResponse(pages.render_machine(view, await s.ctx_for(snap, "/machine")))

    @app.get("/chat", response_class=HTMLResponse)
    async def chat() -> HTMLResponse:
        snap = await s.provider.get()
        view = await load_chat(s.settings)
        return HTMLResponse(pages.render_chat(view, await s.ctx_for(snap, "/chat")))

    @app.get("/w/{task}", response_class=HTMLResponse)
    async def watch(task: str) -> HTMLResponse:
        snap = await s.provider.get()
        crew = _find_crew(snap, task)
        if crew is None:
            return pages_error("Not found", "That worker is not listed.", "/", 404)
        return HTMLResponse(pages.render_watch(crew, await s.ctx_for(snap, "/")))

    @app.get("/w/{task}/out")
    async def watch_out(task: str) -> Response:
        snap = await s.provider.get()
        if _find_crew(snap, task) is None:
            return JSONResponse({"ok": False, "error": "not listed"}, status_code=404)
        try:
            text = await peek(s.settings, task)
        except ActionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)
        return JSONResponse({"ok": True, "text": text, "at": int(time.time())})


def add_live_routes(app: FastAPI, s: Services) -> None:
    """The event stream open pages listen on, and the Web Push sign-up."""

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream():
            q = s.hub.subscribe()
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
                s.hub.unsubscribe(q)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/push/key")
    async def push_key() -> Response:
        try:
            key = await asyncio.to_thread(s.push.public_key)
        except Exception:
            return JSONResponse({"enabled": False})
        return JSONResponse({"enabled": True, "key": key, "csrf": s.tokens.csrf()})

    async def _push_body(request: Request) -> dict | None:
        raw = await request.body()
        if len(raw) > MAX_BODY or not s.tokens.check_csrf(request.headers.get("x-csrf", "")):
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
        if not await asyncio.to_thread(s.push.add, sub):
            return JSONResponse({"ok": False, "error": "too many devices"}, status_code=409)
        actions.audit("push-subscribe", "-", "added")
        await s.push.notify_one(
            sub, "Alerts are on", "You will be pinged when something needs you."
        )
        return JSONResponse({"ok": True})

    @app.post("/push/unsubscribe")
    async def push_unsubscribe(request: Request) -> Response:
        data = await _push_body(request)
        endpoint = data.get("endpoint") if data else None
        if not isinstance(endpoint, str):
            return forbidden()
        await asyncio.to_thread(s.push.remove, endpoint)
        actions.audit("push-subscribe", "-", "removed")
        return JSONResponse({"ok": True})


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
