"""How an action answers: JSON when the page script asked for it (``Accept:
application/json``), else a small page. Shared by the ``/act`` handlers."""

from __future__ import annotations

from dataclasses import dataclass

from starlette.responses import HTMLResponse, JSONResponse, Response

from hive.gateway import pages
from hive.gateway.actions import Outcome, Tokens


def pages_error(title: str, message: str, nxt: str, status: int) -> HTMLResponse:
    return HTMLResponse(pages.render_result(title, message, nxt, False), status_code=status)


def refused(title: str, message: str, nxt: str, status: int, wants_json: bool) -> Response:
    """An action that did not run, and why."""
    if wants_json:
        return JSONResponse({"ok": False, "message": message}, status_code=status)
    return pages_error(title, message, nxt, status)


def outcome_response(result: Outcome, nxt: str, wants_json: bool) -> Response:
    """An action that ran, or is still being recorded (``pending``, answered 202)."""
    if wants_json:
        return JSONResponse(
            {"ok": True, "message": result.summary, "pending": result.pending},
            status_code=202 if result.pending else 200,
        )
    return HTMLResponse(pages.render_outcome(result, nxt))


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
