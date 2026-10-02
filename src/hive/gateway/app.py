"""ASGI app: auth middleware, the desk pages, and the owner's write actions."""

from __future__ import annotations

import re
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from hive.gateway import actions, pages
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.auth import forbidden, is_authorised, is_same_origin_write, method_not_allowed
from hive.gateway.chat import load_chat
from hive.gateway.desk import Desk, build_desk
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import Snapshot, SnapshotProvider

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src '{pages.SCRIPT_CSP_HASH}'; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'"
    ),
}
MAX_BODY = 64 * 1024
NEXT_RE = re.compile(r"^/(chat|p/[A-Za-z0-9%._~-]{1,200})?$")


def _safe_next(value: str) -> str:
    return value if NEXT_RE.fullmatch(value) else "/"


def _quote_project(name: str) -> str:
    from urllib.parse import quote

    return "/p/" + quote(name, safe="")


def create_app(
    settings: GatewaySettings | None = None,
    provider: SnapshotProvider | None = None,
    tokens: Tokens | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    provider = provider or SnapshotProvider(settings)
    tokens = tokens or Tokens()
    runs = RunOnce()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def owner_only(request: Request, call_next) -> Response:
        if not is_authorised(request, settings):
            return forbidden()
        if request.method == "POST":
            if not request.url.path.startswith("/act/"):
                return method_not_allowed()
            if not is_same_origin_write(request):
                return forbidden()
        elif request.method not in ("GET", "HEAD"):
            return method_not_allowed()
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def ctx_for(snap: Snapshot, nxt: str) -> pages.Ctx:
        return pages.Ctx(
            tokens.csrf(),
            writable=snap.ok,
            nxt=nxt,
            board_url=settings.board_url,
            tz=settings.default_tz,
        )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        return HTMLResponse(pages.render_home(snap, desk, ctx_for(snap, "/")))

    @app.get("/p/{name}", response_class=HTMLResponse)
    async def project(name: str) -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        proj = desk.projects.get(name) if desk else None
        status = 200 if (proj or desk is None) else 404
        ctx = ctx_for(snap, _quote_project(name))
        return HTMLResponse(pages.render_project(name, snap, proj, ctx), status_code=status)

    @app.get("/chat", response_class=HTMLResponse)
    async def chat() -> HTMLResponse:
        snap = await provider.get()
        view = await load_chat(settings)
        return HTMLResponse(pages.render_chat(view, ctx_for(snap, "/chat")))

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
            return pages_error(name, str(exc), nxt, exc.status)
        if isinstance(result, HTMLResponse):  # a confirm page
            return result
        return HTMLResponse(pages.render_outcome(result, nxt))

    return app


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
        answer_body = actions.answer_body(form.get("text", ""), owner)

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
