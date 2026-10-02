"""Chat view model: first-mate readiness and note receipts from ``fm-inbox.sh``."""

from __future__ import annotations

from dataclasses import dataclass

from hive.gateway.actions import read_json_script
from hive.gateway.settings import GatewaySettings

RECEIPTS_SCHEMA = "fm-inbox-receipts.v1"
TICKET_MARK = "HIVE-WEB TICKET REQUEST"
MERGE_MARK = "HIVE-WEB MERGE WORD"
DECISION_MARK = "HIVE-WEB DECISION ANSWER"


@dataclass
class Receipt:
    id: str
    at: str
    kind: str  # chat | ticket | merge | decision
    body: str
    state: str  # pending | seen | replied
    reply: str | None
    reply_at: str | None


@dataclass
class ChatView:
    available: bool
    can_receive: bool | None
    receipts: list[Receipt]
    omitted: list[str]


def _s(value: object) -> str:
    return value if isinstance(value, str) else ""


def _kind(body: str) -> str:
    for mark, kind in (
        (TICKET_MARK, "ticket"),
        (MERGE_MARK, "merge"),
        (DECISION_MARK, "decision"),
    ):
        if body.startswith(mark):
            return kind
    return "chat"


def parse_receipts(data: dict) -> tuple[list[Receipt], list[str]]:
    if not _s(data.get("schema")).startswith("fm-inbox-receipts.v1"):
        return [], []
    out: list[Receipt] = []
    for key in ("pending", "handled"):
        items = data.get(key)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            reply = item.get("reply") if isinstance(item.get("reply"), dict) else None
            body = _s(item.get("body"))
            if reply:
                state = "replied"
            elif item.get("acknowledged") is True:
                state = "seen"
            else:
                state = "pending"
            out.append(
                Receipt(
                    _s(item.get("id")),
                    _s(item.get("at")),
                    _kind(body),
                    body,
                    state,
                    _s(reply.get("body")) if reply else None,
                    _s(reply.get("at")) if reply else None,
                )
            )
    out.sort(key=lambda r: r.at, reverse=True)
    omitted = data.get("omitted")
    notes = [
        _s(o) if isinstance(o, str) else _s(o.get("note")) if isinstance(o, dict) else ""
        for o in (omitted if isinstance(omitted, list) else [])
    ]
    return out, [n for n in notes if n]


async def load_chat(settings: GatewaySettings) -> ChatView:
    receipts = await read_json_script(settings, "fm-inbox.sh", "receipts")
    ready = await read_json_script(settings, "fm-inbox.sh", "ready")
    can = ready.get("can_receive") if isinstance(ready, dict) else None
    if receipts is None:
        return ChatView(False, can if isinstance(can, bool) else None, [], [])
    items, omitted = parse_receipts(receipts)
    return ChatView(True, can if isinstance(can, bool) else None, items, omitted)
