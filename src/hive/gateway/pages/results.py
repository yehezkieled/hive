# ruff: noqa: E501  (inline HTML)
"""The small pages an action answers with when the page script is not driving: the confirm
step and the outcome."""

from __future__ import annotations

from html import escape as esc

from hive.gateway.actions import Outcome
from hive.gateway.pages.shell import Ctx, _page


def render_confirm(title: str, detail: str, action: str, ctx: Ctx, hidden: dict[str, str]) -> str:
    """Step-up page: nothing runs until this form is posted back with ``step``."""
    inner = (
        f"<p>{esc(detail)}</p><div class=step__act>"
        "<button class=danger name=confirm value=1>Confirm</button>"
        f"<a class=lnk href='{esc(ctx.nxt)}'>Cancel</a></div>"
    )
    form = ctx.form(action, inner, **hidden)
    return _page(
        f"{title} · Hive desk",
        f"<div class=ph><h1>{esc(title)}</h1></div><section class='step step--ask'>{form}</section>",
    )


def render_result(title: str, message: str, nxt: str, ok: bool) -> str:
    cls = "banner ok" if ok else "banner"
    return _page(
        f"{title} · Hive desk",
        f"<div class=ph><h1>{esc(title)}</h1></div><section class=step>"
        f"<div class='{cls}' role=status>{esc(message)}</div>"
        f"<a class=lnk href='{esc(nxt)}'>Back</a></section>",
    )


def render_outcome(out: Outcome, nxt: str) -> str:
    return render_result(out.action.replace("-", " ").capitalize(), out.summary, nxt, True)
