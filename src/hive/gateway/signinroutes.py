"""The Sign-ins page over HTTP: the page itself, and what its four ``/act`` names do.

The readings and the one login flow that may run belong to ``signins.py``; the page's HTML
to ``pages/signins.py``. This module is the part between them. The owner gate and the CSRF
check have already run when anything here is called.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.responses import HTMLResponse, JSONResponse, Response

from hive.gateway import actions, pages, signins
from hive.gateway.actions import ActionError, Tokens
from hive.gateway.responses import Confirm, confirm_response, refused
from hive.gateway.routes import Services
from hive.gateway.settings import GatewaySettings

# The Sign-ins page's actions: start (behind the step-up), ask how it is going, hand over a
# pasted code, stop. Timed against a slow login command in tests/gateway/test_signins.py.
SIGNIN_ACTIONS = ("signin", "signin-flow", "signin-code", "signin-cancel")


def add_signin_routes(app: FastAPI, s: Services, signin: signins.SignInService) -> None:
    @app.get("/signins", response_class=HTMLResponse)
    async def signins_page() -> HTMLResponse:
        """Each sign-in's state, and Renew. What it renders is safe for any owner page: a
        running flow's link and code only ever answer the page that started it (``/act``)."""
        snap = await s.provider.get()
        panel = await signin.panel()
        return HTMLResponse(pages.render_signins(panel, await s.ctx_for(snap, "/signins")))


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
        return refused("Sign-ins", str(exc), nxt, exc.status, wants_json)
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
