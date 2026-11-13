"""The Terminal page over HTTP: the page, its websocket, the vendored emulator file, and what
``POST /act/terminal`` does.

Finding firstmate's pane, the passes, the one keyboard holder and every limit belong to
``terminal.py``; the page's HTML to ``pages/terminal.py``. This module is the part between
them. Nothing that passes through it (what the pane shows, what is typed) is logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from hive.gateway import actions, pages, terminal
from hive.gateway.actions import ActionError, Tokens
from hive.gateway.auth import is_owner_websocket
from hive.gateway.responses import refused
from hive.gateway.routes import Services

# The vendored terminal emulator, served from the desk itself (never a CDN), pinned by name.
VENDOR_DIR = pages.VENDOR_DIR
VENDOR_FILES = {pages.XTERM_JS: "text/javascript"}


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


def add_terminal_routes(app: FastAPI, s: Services, term: terminal.TerminalService | None) -> None:
    @app.get("/terminal", response_class=HTMLResponse)
    async def terminal_page() -> HTMLResponse:
        """The "confirm it is you" screen. It holds no terminal data: that only ever arrives
        over the websocket, after a finished step-up."""
        snap = await s.provider.get()
        ctx = await s.ctx_for(snap, "/terminal")
        return HTMLResponse(pages.render_terminal(ctx, term is not None))

    @app.websocket("/terminal/ws")
    async def terminal_socket(ws: WebSocket) -> None:
        # The HTTP middleware never sees a websocket: the owner check happens here, before
        # the upgrade is accepted. Refused, the browser gets a bare 403.
        if term is None or not is_owner_websocket(ws, s.settings):
            await ws.close(code=1008)
            return
        await ws.accept()
        await _terminal_session(ws, term, s.settings.owner_login)

    @app.get("/vendor/{name}")
    async def vendor(name: str) -> Response:
        media = VENDOR_FILES.get(name)
        if media is None or term is None or not (VENDOR_DIR / name).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((VENDOR_DIR / name).read_bytes(), media_type=media)


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
        return refused("Terminal", message, "/terminal", status, wants_json)

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
