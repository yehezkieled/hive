# ruff: noqa: E501  (inline HTML)
"""The Chat page: the thread of notes to the first mate and its replies."""

from __future__ import annotations

from html import escape as esc

from hive.gateway.actions import new_request_id
from hive.gateway.chat import ChatView
from hive.gateway.pages.shell import Ctx, _page, _sc

# a sent note's kind: (chip colour, chip word); plain chat has no chip
_KIND_LABEL = {
    "chat": ("", ""),
    "ticket": ("q", "Ticket request"),
    "merge": ("ok", "Merge word"),
    "decision": ("you", "Answer"),
    "delegate": ("run", "Delegated"),
}


_STATE_LABEL = {
    "pending": "Sent",
    "seen": "Seen",
    "replied": "Answered",
}


def _thread(view: ChatView, ctx: Ctx) -> str:
    if not view.available:
        return "<p class=mute>Messages are unavailable (firstmate inbox script did not answer).</p>"
    msgs = []
    for r in sorted(view.receipts, key=lambda r: r.at):  # oldest first, newest at the bottom
        rid = f" data-rid='{esc(r.request_id)}'" if r.request_id else ""
        if _KIND_LABEL[r.kind][1]:  # a request, not a plain note: a receipt card led by its kind
            bubble = f"<div class='bubble rc'>{_sc(*_KIND_LABEL[r.kind])}<span>{esc(r.body)}</span></div>"
        else:
            bubble = f"<div class=bubble>{esc(r.body)}</div>"
        msgs.append(
            f"<div class='msg me'{rid}>{bubble}"
            f"<span class=meta>{ctx.time(r.at)} · {esc(_STATE_LABEL[r.state])}</span></div>"
        )
        if r.reply is not None:
            msgs.append(
                "<div class='msg fm'><span class=msg__who><i></i>First mate</span>"
                f"<div class=bubble>{esc(r.reply)}</div>"
                f"<span class=meta>{ctx.time(r.reply_at)}</span></div>"
            )
    out = "".join(msgs) or "<p class='mute empty'>No messages yet. Say hello below.</p>"
    if view.omitted:
        out = "<p class=meta>Older entries omitted: " + esc("; ".join(view.omitted)) + "</p>" + out
    return out


def render_chat(view: ChatView, ctx: Ctx) -> str:
    if view.can_receive is False:
        status = (
            "<div class=banner role=status>The first mate session is not receiving right now. "
            "Notes are saved and will be read when it is back.</div>"
        )
    else:
        status = ""
    form = ctx.form(
        "chat",
        "<label for=chat-text>Message the first mate</label><textarea id=chat-text name=text "
        "rows=1 required maxlength=4000 enterkeyhint=send placeholder='Ask, delegate, or steer…'>"
        "</textarea><button class='btn btn--p'>Send</button>",
        "chatf",
        rid=new_request_id(),
    )
    return _page(
        "Chat · Hive desk",
        f"<div class=ph><h1>Chat with the first mate</h1></div>{status}<section class='pn chatpn'>"
        f"<div class='pn__b thread' id=thread aria-live=polite>{_thread(view, ctx)}</div>"
        "<button id=jump class='btn btn--s btn--sm' type=button hidden>New messages \u2193</button>"
        f"<div class=dock>{form}</div></section>",
        "chat",
        " data-poll=4000",
        ctx,
    )
