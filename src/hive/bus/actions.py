"""Parse structured actions from entity responses.

Entities can include a <hive_actions> block in their text response
containing a JSON array of actions. The orchestrator extracts these,
validates permissions, and routes messages accordingly.

Supported action types:
- ``message``: send text to another entity. Fields: ``to``, ``text``.
  Peer routing applies (see ``hive.bus.permissions.can_message``).
- ``request_decision``: escalate a directional decision to the direct
  parent (worker → own lead, lead → own maestro). Fields: ``to``,
  ``text``. Strict parent-only routing.
- ``request_mode_change``: ask for an elevated permission mode. Fields:
  ``requested_mode`` (yolo|yotree), ``reason`` (optional).
- ``report_failure``: a task-bound entity tells the orchestrator the
  current task is failing. Fields: ``reason``; optional ``task_id``
  override (defaults to the entity's current task_id).
- ``spawn_team``: maestro creates a new team in its own org. Fields:
  ``team_name``; optional ``model`` (default sonnet).
- ``spawn_worker``: maestro or lead spawns a worker under a team.
  Fields: optional ``lead`` (full lead name like ``dev.backend``);
  optional ``worker_name``, ``task_id``. When ``lead`` is omitted, the
  manager infers it from the actor: a lead spawns under itself; a
  maestro is rejected (must specify which team).
- ``kill_entity``: maestro or lead kills an entity in its scope.
  Fields: ``target``.
- ``request_payment``: vault entity requests a structured payment.
  Fields: ``amount_cents`` (positive int), ``currency`` (e.g. "USD"),
  ``recipient``, ``idempotency_key`` (unique per request), ``reason``.
  Only ``vault`` role may emit this; manager rejects + audits otherwise.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ACTIONS_PATTERN = re.compile(
    r"<hive_actions>\s*(.*?)\s*</hive_actions>",
    re.DOTALL,
)

_MESSAGE_REQUIRED = {"to", "text"}
_MODE_REQUEST_REQUIRED = {"requested_mode"}
_FAILURE_REQUIRED = {"reason"}
_SPAWN_TEAM_REQUIRED = {"team_name"}
_SPAWN_WORKER_REQUIRED: set[str] = set()
_KILL_ENTITY_REQUIRED = {"target"}
_REQUEST_DECISION_REQUIRED = {"to", "text"}
_REQUEST_PAYMENT_REQUIRED = {
    "amount_cents",
    "currency",
    "recipient",
    "idempotency_key",
    "reason",
}


@dataclass
class Action:
    """A structured action extracted from an entity response.

    Only the fields relevant to ``type`` are populated. ``to``/``text``
    are set for ``message`` actions; ``requested_mode``/``reason`` for
    ``request_mode_change``; ``reason``/``task_id`` for ``report_failure``;
    ``team_name``/``model`` for ``spawn_team``; ``lead``/``worker_name``/
    ``task_id`` for ``spawn_worker``; ``target`` for ``kill_entity``.
    """

    type: str
    to: str | None = None
    text: str | None = None
    requested_mode: str | None = None
    reason: str | None = None
    task_id: int | None = None
    team_name: str | None = None
    model: str | None = None
    lead: str | None = None
    worker_name: str | None = None
    target: str | None = None
    # Phase 3 (autonomous personality generation): parents may include a
    # human-readable label and free-text personality blurb when spawning
    # a team or worker. Only used to write the auto-generated personality
    # file — both fields must be present together for the file to be
    # written (pair-or-nothing).
    display_name: str | None = None
    personality: str | None = None
    # request_payment fields (Sprint 25)
    amount_cents: int | None = None
    currency: str | None = None
    recipient: str | None = None
    idempotency_key: str | None = None


def parse_actions(response: str) -> tuple[str, list[Action]]:
    """Extract <hive_actions> block from response text.

    Returns (clean_text, actions) where clean_text has the
    <hive_actions>...</hive_actions> block stripped out.
    If no block is found, returns (response, []).
    If JSON inside the block is malformed, logs a warning
    and returns (cleaned_text, []).
    """
    match = _ACTIONS_PATTERN.search(response)
    if not match:
        return response, []

    # Strip the entire block from the response
    clean_text = response[: match.start()] + response[match.end() :]
    clean_text = clean_text.strip()

    raw_json = match.group(1)
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON in <hive_actions> block: %s", raw_json[:200])
        return clean_text, []

    if not isinstance(data, list):
        logger.warning("<hive_actions> block is not a JSON array")
        return clean_text, []

    actions: list[Action] = []
    for item in data:
        if not isinstance(item, dict) or "type" not in item:
            logger.warning("Action missing type field: %s", item)
            continue
        atype = item["type"]

        if atype == "message":
            missing = _MESSAGE_REQUIRED - item.keys()
            if missing:
                logger.warning("message action missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, to=item["to"], text=item["text"]))
            continue

        if atype == "request_mode_change":
            missing = _MODE_REQUEST_REQUIRED - item.keys()
            if missing:
                logger.warning("request_mode_change missing fields %s: %s", missing, item)
                continue
            actions.append(
                Action(
                    type=atype,
                    requested_mode=item["requested_mode"],
                    reason=item.get("reason"),
                )
            )
            continue

        if atype == "report_failure":
            missing = _FAILURE_REQUIRED - item.keys()
            if missing:
                logger.warning("report_failure missing fields %s: %s", missing, item)
                continue
            raw_task_id = item.get("task_id")
            try:
                task_id_val = int(raw_task_id) if raw_task_id is not None else None
            except (TypeError, ValueError):
                logger.warning("report_failure has non-integer task_id: %r", raw_task_id)
                task_id_val = None
            actions.append(
                Action(
                    type=atype,
                    reason=item["reason"],
                    task_id=task_id_val,
                )
            )
            continue

        if atype == "spawn_team":
            missing = _SPAWN_TEAM_REQUIRED - item.keys()
            if missing:
                logger.warning("spawn_team missing fields %s: %s", missing, item)
                continue
            actions.append(
                Action(
                    type=atype,
                    team_name=item["team_name"],
                    model=item.get("model"),
                    display_name=item.get("display_name"),
                    personality=item.get("personality"),
                )
            )
            continue

        if atype == "spawn_worker":
            raw_task_id = item.get("task_id")
            try:
                task_id_val = int(raw_task_id) if raw_task_id is not None else None
            except (TypeError, ValueError):
                logger.warning("spawn_worker has non-integer task_id: %r", raw_task_id)
                task_id_val = None
            actions.append(
                Action(
                    type=atype,
                    lead=item.get("lead"),
                    worker_name=item.get("worker_name"),
                    task_id=task_id_val,
                    display_name=item.get("display_name"),
                    personality=item.get("personality"),
                )
            )
            continue

        if atype == "kill_entity":
            missing = _KILL_ENTITY_REQUIRED - item.keys()
            if missing:
                logger.warning("kill_entity missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, target=item["target"]))
            continue

        if atype == "request_decision":
            missing = _REQUEST_DECISION_REQUIRED - item.keys()
            if missing:
                logger.warning("request_decision missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, to=item["to"], text=item["text"]))
            continue

        if atype == "request_payment":
            missing = _REQUEST_PAYMENT_REQUIRED - item.keys()
            if missing:
                logger.warning("request_payment missing fields %s: %s", missing, item)
                continue
            try:
                amount = int(item["amount_cents"])
            except (TypeError, ValueError):
                logger.warning(
                    "request_payment has non-integer amount_cents: %r",
                    item.get("amount_cents"),
                )
                continue
            if amount <= 0:
                logger.warning("request_payment has non-positive amount_cents: %r", amount)
                continue
            currency = item["currency"]
            if not isinstance(currency, str) or len(currency) != 3:
                logger.warning("request_payment has invalid currency: %r", currency)
                continue
            actions.append(
                Action(
                    type=atype,
                    amount_cents=amount,
                    currency=currency.upper(),
                    recipient=str(item["recipient"]),
                    idempotency_key=str(item["idempotency_key"]),
                    reason=str(item["reason"]),
                )
            )
            continue

        logger.warning("Unknown action type %r, skipping", atype)

    return clean_text, actions
