"""ASGI app: builds the desk's services, the auth middleware, and registers the routes.

The pages and the live stream are route groups in ``routes.py``, the static files in
``assets.py``; ``POST /act/{name}`` is here and runs the handlers in ``handlers.py``. The Run
queue, Sign-ins and Terminal routes and handlers are in this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from hive.gateway import actions, machine, pages, runqueue, signins, terminal
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.assets import FONTS_DIR as FONTS_DIR  # moved; tests import it from here
from hive.gateway.assets import add_asset_routes
from hive.gateway.auth import (
    forbidden,
    is_authorised,
    is_owner_websocket,
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
from hive.gateway.handlers import _step_subject as _step_subject  # moved; tests import it here
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
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider
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
# The vendored terminal emulator, served from the desk itself (never a CDN), pinned by name.
VENDOR_DIR = pages.VENDOR_DIR
VENDOR_FILES = {pages.XTERM_JS: "text/javascript"}
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
# The Sign-ins page's actions: start (behind the step-up), ask how it is going, hand over a
# pasted code, stop. Timed against a slow login command in tests/gateway/test_signins.py.
SIGNIN_ACTIONS = ("signin", "signin-flow", "signin-code", "signin-cancel")
PUSH_POSTS = ("/push/subscribe", "/push/unsubscribe")
NEXT_RE = re.compile(
    r"^/(chat|config|machine|run|signins|terminal|p/[A-Za-z0-9%._~-]{1,200}|\?focus=[A-Za-z0-9%._~-]{1,200})?$"
)


def _safe_next(value: str) -> str:
    return value if NEXT_RE.fullmatch(value) else "/"


def terminal_csp(origin: str) -> str:
    """The Terminal page's policy: its own pinned script and the vendored emulator (same
    origin), and the websocket back to this desk only."""
    host = urlsplit(origin).netloc
    socket = f" wss://{host}" if re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?", host) else ""
    return (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src '{pages.SCRIPT_CSP_HASH}' '{pages.TERMINAL_SCRIPT_CSP_HASH}' 'self'; "
        f"connect-src 'self'{socket}; "
        "manifest-src 'self'; img-src 'self'; worker-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'"
    )


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

    # ---- Run queue, Sign-ins and Terminal ----------------------------------------------------
    # The three remote-access pages are registered here, not in a route group; their /act
    # handlers are further down this file.
    ctx_for = services.ctx_for

    @app.get("/run", response_class=HTMLResponse)
    async def run_page() -> HTMLResponse:
        """The fix buttons and the last runs. Rendering it runs nothing; a run the desk did
        not see finish is settled from the logs."""
        snap = await provider.get()
        view = await runq.view()
        return HTMLResponse(pages.render_run(view, await ctx_for(snap, "/run")))

    @app.get("/signins", response_class=HTMLResponse)
    async def signins_page() -> HTMLResponse:
        """Each sign-in's state, and Renew. What it renders is safe for any owner page: a
        running flow's link and code only ever answer the page that started it (``/act``)."""
        snap = await provider.get()
        panel = await signin.panel()
        return HTMLResponse(pages.render_signins(panel, await ctx_for(snap, "/signins")))

    @app.get("/terminal", response_class=HTMLResponse)
    async def terminal_page() -> HTMLResponse:
        """The "confirm it is you" screen. It holds no terminal data: that only ever arrives
        over the websocket, after a finished step-up."""
        snap = await provider.get()
        ctx = await ctx_for(snap, "/terminal")
        return HTMLResponse(pages.render_terminal(ctx, term is not None))

    @app.websocket("/terminal/ws")
    async def terminal_socket(ws: WebSocket) -> None:
        # The HTTP middleware never sees a websocket: the owner check happens here, before
        # the upgrade is accepted. Refused, the browser gets a bare 403.
        if term is None or not is_owner_websocket(ws, settings):
            await ws.close(code=1008)
            return
        await ws.accept()
        await _terminal_session(ws, term, settings.owner_login)

    @app.get("/vendor/{name}")
    async def vendor(name: str) -> Response:
        media = VENDOR_FILES.get(name)
        if media is None or term is None or not (VENDOR_DIR / name).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((VENDOR_DIR / name).read_bytes(), media_type=media)

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


TERMINAL_OPEN_DETAIL = (
    "This shows firstmate’s own session, live, as it is on the PC. It opens read-only; "
    "typing needs a second confirm."
)


async def _terminal_open(
    form: dict[str, str],
    term: terminal.TerminalService | None,
    tokens: Tokens,
    who: str,
    wants_json: bool,
) -> Response:
    """Open the Terminal page: the first tap checks firstmate's session is there and arms the
    step-up; the second, with the step token, hands the page its pass (see ``terminal.py``).
    A step token opens once. Nothing here reads or types into the session."""

    def refuse(message: str, status: int) -> Response:
        actions.audit("terminal", "-", "refused", status=status)
        if wants_json:
            return JSONResponse({"ok": False, "message": message}, status_code=status)
        return pages_error("Terminal", message, "/terminal", status)

    if term is None:
        return refuse("The Terminal page is switched off on this desk.", 404)
    if not wants_json:
        return refuse("The terminal needs the page script. Open it from the Terminal page.", 400)
    # Each open's step is bound to its own request id, so it opens once and only that open.
    step, rid = form.get("step", ""), form.get("rid", "")
    if (
        step
        and actions.REQUEST_ID_RE.fullmatch(rid)
        and tokens.check_step_up(step, "terminal", f"open:{rid}")
        and term.spend_step(step)
    ):
        try:
            grant = await term.open(who)
        except ActionError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        return JSONResponse({"ok": True, "pass": grant.token, "ends": int(grant.ends)})
    found = await term.open_look()
    if found.pane is None:
        return refuse(terminal.WORDS[found.why], 503 if found.why.startswith("herdr") else 409)
    rid = actions.new_request_id()
    return JSONResponse(
        {
            "ok": True,
            "confirm": True,
            "rid": rid,
            "step": tokens.step_up("terminal", f"open:{rid}"),
            "label": "Tap again to open",
            "detail": TERMINAL_OPEN_DETAIL,
        }
    )


async def _terminal_session(ws: WebSocket, term: terminal.TerminalService, who: str) -> None:
    """One attached tab: its pass first, then messages both ways until either side ends.

    Nothing that passes through here (what the pane shows, what is typed) is logged."""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), terminal.ATTACH_WAIT_S)
        hello = json.loads(raw) if len(raw) <= terminal.MAX_MESSAGE else None
    except (TimeoutError, ValueError, KeyError, RuntimeError, WebSocketDisconnect):
        hello = None
    if not isinstance(hello, dict) or hello.get("t") != "attach":
        with contextlib.suppress(Exception):
            await ws.close(code=terminal.CLOSE_STEP_UP)
        return
    try:
        viewer = await term.attach(hello.get("pass"), who)
    except terminal.AttachError as exc:
        with contextlib.suppress(Exception):
            await ws.send_text(
                json.dumps({"t": "end", "reason": exc.reason, "text": terminal.WORDS[exc.reason]})
            )
            await ws.close(code=exc.code)
        return

    async def outbound() -> None:
        while (msg := await viewer.next()) is not None:
            await ws.send_text(json.dumps(msg, separators=(",", ":"), ensure_ascii=False))

    sender = asyncio.create_task(outbound())
    reason = "closed"
    try:
        while True:
            incoming = asyncio.ensure_future(ws.receive())
            done, _ = await asyncio.wait({incoming, sender}, return_when=asyncio.FIRST_COMPLETED)
            if incoming not in done:
                incoming.cancel()
                reason = viewer.ended[1] if viewer.ended else "closed"
                break
            event = incoming.result()
            if event["type"] == "websocket.disconnect":
                break
            if event.get("text") is not None:
                await term.handle(viewer, event["text"])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await term.detach(viewer, reason)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        code = viewer.ended[0] if viewer.ended else 1000
        with contextlib.suppress(Exception):
            # The last message (``end``) goes out before the close.
            while (msg := await asyncio.wait_for(viewer.next(), 0.1)) is not None:
                await ws.send_text(json.dumps(msg, separators=(",", ":"), ensure_ascii=False))
        with contextlib.suppress(Exception):
            await ws.close(code=code)


RUN_PENDING = "Running. The result shows in the list of runs when it finishes."


async def _run(
    form: dict[str, str],
    runq: RunQueue,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    wants_json: bool,
) -> Response:
    """One Run page action: a confirm step, a second one for the actions that take things
    down, then one run. Each step-up is bound to exactly this action, number and request id,
    so a link, a GET or another page cannot start one, and a confirmed tap runs only what
    it confirmed."""
    key = "-"  # the journal names only a listed action, never text from the request
    try:
        action, n = runqueue.parse(form)
        key = action.key
        rid = form.get("rid", "")
        subject = f"{action.key}:{'-' if n is None else n}:{rid}"
        step = form.get("step", "")
        good_rid = actions.REQUEST_ID_RE.fullmatch(rid) is not None
        hidden = {"do": action.key, **({"n": str(n)} if n is not None else {})}
        if good_rid and action.down and tokens.check_step_up(step, "run-down", subject):
            pass  # both confirms passed
        elif good_rid and tokens.check_step_up(step, "run", subject):
            if action.down:  # the extra confirm: what goes down and how it comes back
                return confirm_response(
                    Confirm(
                        f"{action.label}: are you sure?",
                        action.down,
                        "run",
                        {**hidden, "rid": rid, "step": tokens.step_up("run-down", subject)},
                        f"Yes, {action.label[0].lower()}{action.label[1:]}",
                        warn=True,
                    ),
                    nxt,
                    tokens,
                    wants_json,
                )
        else:
            why, status = await runq.refusal(action)
            if why:  # not offered now: say so at the first tap, not after the confirm
                raise ActionError(why, status)
            rid = actions.new_request_id()
            subject = f"{action.key}:{'-' if n is None else n}:{rid}"
            when = action.when(n)
            detail = (
                f"{action.plain}{' It runs ' + when + '.' if when else ''} "
                f"Exactly: {action.command(n)}. Time-out {int(action.timeout_s)} s."
            )
            return confirm_response(
                Confirm(
                    action.label,
                    detail,
                    "run",
                    {**hidden, "rid": rid, "step": tokens.step_up("run", subject)},
                    "Tap again: next step" if action.down else "Tap again to run",
                    warn=True,
                ),
                nxt,
                tokens,
                wants_json,
            )

        async def start() -> Outcome:
            done = await runq.run(action, n, rid, settings.owner_login)
            if done.result != runqueue.OK:
                raise ActionError(done.message, runqueue.RESULT_STATUS.get(done.result, 502))
            return Outcome("run", action.key, done.message)

        result = await runs.run(
            rid, start, actions.ACT_WAIT_S, ("run", action.key), summary=RUN_PENDING
        )
    except ActionError as exc:
        actions.audit("run", key, "not-run-or-failed", status=exc.status)
        if wants_json:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        return pages_error("Run", str(exc), nxt, exc.status)
    reload_s = action.reload_s if not result.pending else 0
    if wants_json:
        body: dict[str, object] = {
            "ok": True,
            "message": result.summary,
            "pending": result.pending,
        }
        if reload_s:
            body["reload"] = reload_s
        return JSONResponse(body, status_code=202 if result.pending else 200)
    return HTMLResponse(pages.render_outcome(result, nxt))


async def _signin(
    name: str,
    form: dict[str, str],
    service: signins.SignInService,
    settings: GatewaySettings,
    tokens: Tokens,
    wants_json: bool,
) -> Response:
    """One Sign-ins action. The owner gate and the CSRF check already ran.

    Starting needs the step-up; the other three act on the running flow and need the token
    only the starting page was given (stopping it is open to any owner page). The answer
    carries the flow as this caller may see it: JSON for the page script, else a page.
    """
    nxt, token = "/signins", form.get("flow", "")
    ctx = pages.Ctx(tokens.csrf(), True, nxt, "", settings.default_tz)
    try:
        if name == "signin":
            provider = service.providers.get(form.get("provider", ""))
            if provider is None:
                raise ActionError("Unknown sign-in.", 404)
            rid = form.get("rid", "")
            if not actions.REQUEST_ID_RE.fullmatch(rid) or not tokens.check_step_up(
                form.get("step", ""), "signin", f"{provider.key}:{rid}"
            ):
                rid = actions.new_request_id()
                step = tokens.step_up("signin", f"{provider.key}:{rid}")
                confirm = Confirm(
                    f"Renew the {provider.label} sign-in",
                    service.confirm_text(provider),
                    "signin",
                    {"provider": provider.key, "rid": rid, "step": step},
                    "Tap again to renew",
                    warn=True,
                )
                return confirm_response(confirm, nxt, tokens, wants_json)
            view = await service.start(provider.key, rid, settings.owner_login)
            message = f"The {provider.label} sign-in has started."
        elif name == "signin-code":
            view = await service.send_code(token, form.get("code", ""))
            message = "The code was handed to the login command."
        elif name == "signin-cancel":
            view = await service.cancel(token, settings.owner_login)
            message = "The sign-in was stopped." if not view.active else "Stopping the sign-in."
        else:
            view = service.view(token)
            message = "A sign-in is running." if view.active else "No sign-in is running."
    except ActionError as exc:
        actions.audit(name, "-", "refused", status=exc.status)
        if wants_json:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        return pages_error("Sign-ins", str(exc), nxt, exc.status)
    if not wants_json:
        return HTMLResponse(pages.render_signin_flow(view, ctx))
    body: dict[str, object] = {
        "ok": True,
        "message": message,
        "active": view.active,
        "html": pages.signin_flow(view, ctx),
    }
    if name == "signin":
        body["flow"] = view.token
    return JSONResponse(body)
