"""ASGI app: builds the desk's services, the auth middleware, and registers the routes.

The pages and the live stream are route groups in ``routes.py``, the static files in
``assets.py``; ``POST /act/{name}`` is here and runs the handlers in ``handlers.py``. The three
remote-access pages each have their routes and /act handler in a module of their own:
``runroutes.py``, ``signinroutes.py`` and ``terminalroutes.py``.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from hive.gateway import actions, machine, pages, signins, terminal
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.assets import add_asset_routes
from hive.gateway.auth import (
    forbidden,
    is_authorised,
    is_same_origin_write,
    method_not_allowed,
)
from hive.gateway.describe import Describer
from hive.gateway.desk import build_desk
from hive.gateway.handlers import (
    FIRSTMATE_ACTIONS,
    REVIEW_ACTIONS,
    _chat,
    _firstmate,
    _review_action,
    _snapshot_action,
    _watchdog,
)
from hive.gateway.live import LiveHub
from hive.gateway.push import Alert, PushService
from hive.gateway.quota import QuotaProvider
from hive.gateway.repos import RepoNames
from hive.gateway.responses import (
    Confirm,
    confirm_response,
    outcome_response,
    pages_error,
    refused,
)
from hive.gateway.routes import MAX_BODY, Services, add_live_routes, add_page_routes
from hive.gateway.runqueue import RunQueue
from hive.gateway.runroutes import _run, add_run_routes
from hive.gateway.settings import GatewaySettings
from hive.gateway.signinroutes import SIGNIN_ACTIONS, _signin, add_signin_routes
from hive.gateway.snapshot import SnapshotProvider
from hive.gateway.terminalroutes import _terminal_open, add_terminal_routes, terminal_csp
from hive.gateway.wake import WakeService
from hive.gateway.watchdog import Watchdog, WatchdogStore

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
# Every name `POST /act/{name}` answers to. A test times each one; add new actions here.
ACT_NAMES = (
    "chat",
    "answer",
    "decision",
    "delegate",
    "ticket",
    "merge",
    "control",
    "run",
    "terminal",
    *REVIEW_ACTIONS,
)
PUSH_POSTS = ("/push/subscribe", "/push/unsubscribe")
NEXT_RE = re.compile(
    r"^/(chat|config|machine|run|signins|terminal|p/[A-Za-z0-9%._~-]{1,200}|\?focus=[A-Za-z0-9%._~-]{1,200})?$"
)


def _safe_next(value: str) -> str:
    return value if NEXT_RE.fullmatch(value) else "/"


def create_app(
    settings: GatewaySettings | None = None,
    provider: SnapshotProvider | None = None,
    tokens: Tokens | None = None,
    describer: Describer | None = None,
    wake: WakeService | None = None,
    watchdog: Watchdog | None = None,
    monitor: machine.MachineMonitor | None = None,
    runq: RunQueue | None = None,
    signin: signins.SignInService | None = None,
    term: terminal.TerminalService | None = None,
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

    async def work_underway() -> bool | None:
        """Loops running or tickets in flight, from the cached snapshot; None if unreadable."""
        snap = await provider.get()
        if snap.data is None:
            return None
        desk = build_desk(snap.data)
        return any(p.running or p.count("in_flight") for p in desk.projects.values())

    async def announce(event: dict) -> None:
        """A watchdog event: refresh open desks, and push the ones that need the captain."""
        hub.publish("desk")
        if event.get("level") in ("warn", "alert") and event.get("text"):
            await push.notify(Alert("watchdog", "Firstmate recovery", str(event["text"]), "/"))

    watchdog = watchdog or Watchdog(wake, WatchdogStore(settings.data_dir), work_underway, announce)
    # The Machine page's read-only probes; each finished round refreshes open Machine pages.
    monitor = monitor or machine.MachineMonitor(
        settings, wake.status, watchdog.store.view, lambda: hub.publish("machine")
    )
    # The Run page's fixed fix buttons; each start and end refreshes open Run pages.
    runq = runq or RunQueue(settings, on_change=lambda: hub.publish("run"))
    # The Sign-ins page. Nothing about a flow is ever published to the event stream.
    signin = signin or signins.SignInService(settings)
    # The Terminal page's bridge to firstmate's own herdr pane; None when it is switched off.
    term = (term or terminal.TerminalService(settings)) if settings.terminal else None
    term_csp = terminal_csp(settings.origin)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.live:
            hub.start()
            watchdog.start()
            monitor.peek()  # first readings land before the first Machine page view
        try:
            yield
        finally:
            await signin.stop()  # a login command must not outlive the desk
            if term is not None:
                await term.stop()
            await monitor.stop()
            await watchdog.stop()
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
        path = request.url.path
        if path == "/terminal":
            response.headers["Content-Security-Policy"] = term_csp
        if path.startswith("/fonts/") and response.status_code == 200:
            response.headers["Cache-Control"] = "private, max-age=604800"
        if path.startswith("/vendor/") and response.status_code == 200:
            response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        return response

    services = Services(
        settings=settings,
        provider=provider,
        quota=quota,
        describer=describer,
        repo_names=repo_names,
        tokens=tokens,
        wake=wake,
        watchdog=watchdog,
        monitor=monitor,
        push=push,
        hub=hub,
        terminal=term is not None,
    )
    add_page_routes(app, services)
    add_live_routes(app, services)
    add_asset_routes(app)

    # The three remote-access pages, each with its /act handler in a module of its own.
    add_run_routes(app, services, runq)
    add_signin_routes(app, services, signin)
    add_terminal_routes(app, services, term)

    # ---- every write action ------------------------------------------------------------------

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
        if name in SIGNIN_ACTIONS:  # the PC's own sign-ins; nothing of firstmate's
            return await _signin(name, form, signin, settings, tokens, wants_json)
        if name == "terminal":  # herdr, not the snapshot
            return await _terminal_open(form, term, tokens, settings.owner_login, wants_json)
        if name in REVIEW_ACTIONS:  # Lavish state, not the snapshot
            return await _review_action(
                name, form, settings, tokens, runs, nxt, wants_json, hub.publish
            )
        if name == "run":  # the Run page; must work while firstmate's snapshot is unreadable
            return await _run(form, runq, settings, tokens, runs, nxt, wants_json)
        try:
            if name == "watchdog" or name in FIRSTMATE_ACTIONS:
                # The recovery switch, wake and restart need no snapshot: they must work while
                # firstmate's is unreadable.
                if name == "watchdog":
                    result = await _watchdog(form, watchdog, settings, tokens, runs)
                else:
                    result = await _firstmate(name, form, wake, settings, tokens, runs)
                if isinstance(result, Confirm):
                    return confirm_response(result, nxt, tokens, wants_json)
                if wants_json:
                    return JSONResponse({"ok": True, "message": result.summary})
                return HTMLResponse(pages.render_outcome(result, nxt))
            result = await _snapshot_action(
                name, form, provider, settings, tokens, runs, nxt, lambda: hub.publish("desk")
            )
        except ActionError as exc:
            actions.audit(name, "-", "refused", status=exc.status)
            return refused(name, str(exc), nxt, exc.status, wants_json)
        if isinstance(result, Outcome) and not result.pending:
            hub.publish("desk")
        if isinstance(result, Confirm):
            return confirm_response(result, nxt, tokens, wants_json)
        return outcome_response(result, nxt, wants_json)

    return app
