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

# Negative lookahead `(?!<hive_actions>)` makes the lazy group refuse to
# span across another opening tag — orphan openings (e.g. when the model
# closes with `</invoke>` and retries) are skipped, and only well-formed
# pairs match.
_ACTIONS_PATTERN = re.compile(
    r"<hive_actions>\s*((?:(?!<hive_actions>).)*?)\s*</hive_actions>",
    re.DOTALL,
)

# Human-readable aliases for the action tag names, used inside error
# strings and feedback messages. Using these instead of the literal
# `<hive_actions>` / `</hive_actions>` tags prevents a self-sustaining
# loop: when the orchestrator routes parse-error feedback back to an
# entity, the entity's terminal screen-echoes the feedback, and on the
# next turn parse_actions re-scans the screen. If the feedback contained
# the literal tag strings, the parser would find them inside its own
# help text, try to parse the prose between them as JSON, fail, and
# generate the same feedback — firing every ~2h in prod. The spaces
# break the literal-substring `.find` and the `<hive_actions>` regex
# both, while staying obviously the tag name to a human reader.
_OPEN_TAG_ALIAS = "< hive_actions >"
_CLOSE_TAG_ALIAS = "< /hive_actions >"


def neutralize_action_tags(text: str) -> str:
    """Replace literal ``<hive_actions>`` / ``</hive_actions>`` substrings
    with non-parseable visual aliases.

    Used by ``parse_actions`` (on its own error strings) and by the
    process manager (on the wrapper text it composes around them) to
    guarantee that feedback routed back to an entity cannot re-trigger
    the parser when the entity's terminal screen-echoes it.
    """
    return text.replace("</hive_actions>", _CLOSE_TAG_ALIAS).replace(
        "<hive_actions>", _OPEN_TAG_ALIAS
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


def parse_actions(response: str) -> tuple[str, list[Action], list[str]]:
    """Extract <hive_actions> blocks from response text.

    Returns ``(clean_text, actions, errors)``:

    - ``clean_text`` has every <hive_actions>-related span stripped —
      from the first opening tag through the last closing tag, even
      when orphan openings sit between them. Robust to a malformed
      first attempt (closing with ``</invoke>``) and a correctly
      closed retry: only the well-formed retry is parsed.
    - ``actions`` is the list of valid Action records.
    - ``errors`` is a list of human-readable parse-failure strings
      (malformed JSON, missing required fields, unknown action types).
      Empty when every block parses cleanly. The caller is expected
      to route these back to the sender as a feedback message so they
      can retry — silent drops let leads believe their spawn worked
      when it didn't.

    If no opening tag is present, returns ``(response, [], [])``.
    """
    errors: list[str] = []
    first_open = response.find("<hive_actions>")
    if first_open == -1:
        return response, [], errors

    closing_tag = "</hive_actions>"
    last_close = response.rfind(closing_tag)
    if last_close == -1:
        # Orphan opening with no close anywhere — strip from the
        # opening to the end so harness chatter doesn't leak.
        errors.append(
            f"{_OPEN_TAG_ALIAS} block has no closing {_CLOSE_TAG_ALIAS} "
            "tag — the entire block was dropped. Make sure every "
            f"opening tag is followed by a matching {_CLOSE_TAG_ALIAS} "
            "close (not </invoke> or any other tool-call closing tag). "
            "(Tag names shown with spaces to avoid re-triggering the "
            "parser when this message is echoed back.)"
        )
        return response[:first_open].strip(), [], errors
    last_close_end = last_close + len(closing_tag)
    clean_text = (response[:first_open] + response[last_close_end:]).strip()

    data: list[object] = []
    for match in _ACTIONS_PATTERN.finditer(response):
        raw_json = match.group(1)
        try:
            block_data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            snippet = raw_json[:200]
            # Neutralise the snippet too — entities sometimes paste prior
            # feedback or doc text verbatim into their malformed JSON,
            # which would re-introduce parseable tag substrings.
            safe_snippet = neutralize_action_tags(repr(snippet))
            errors.append(
                f"Malformed JSON in {_OPEN_TAG_ALIAS} block: {exc.msg} "
                f"(line {exc.lineno}, col {exc.colno}). "
                f"Snippet: {safe_snippet}. Tip: escape newlines as \\n "
                f'and \\" for quotes inside multi-line string fields '
                f"like `personality`."
            )
            logger.warning("Malformed JSON in <hive_actions> block: %s", snippet)
            continue
        if not isinstance(block_data, list):
            errors.append(
                f"{_OPEN_TAG_ALIAS} block must be a JSON array of action "
                f"objects, got {type(block_data).__name__}."
            )
            logger.warning("<hive_actions> block is not a JSON array")
            continue
        data.extend(block_data)

    actions: list[Action] = []
    for item in data:
        if not isinstance(item, dict) or "type" not in item:
            errors.append(f"Action missing `type` field: {item!r}")
            logger.warning("Action missing type field: %s", item)
            continue
        atype = item["type"]

        if atype == "message":
            missing = _MESSAGE_REQUIRED - item.keys()
            if missing:
                errors.append(
                    f"`message` action missing required fields {sorted(missing)}: {item!r}"
                )
                logger.warning("message action missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, to=item["to"], text=item["text"]))
            continue

        if atype == "request_mode_change":
            missing = _MODE_REQUEST_REQUIRED - item.keys()
            if missing:
                errors.append(
                    f"`request_mode_change` missing required fields {sorted(missing)}: {item!r}"
                )
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
                errors.append(
                    f"`report_failure` missing required fields {sorted(missing)}: {item!r}"
                )
                logger.warning("report_failure missing fields %s: %s", missing, item)
                continue
            raw_task_id = item.get("task_id")
            try:
                task_id_val = int(raw_task_id) if raw_task_id is not None else None
            except (TypeError, ValueError):
                errors.append(f"`report_failure` has non-integer task_id: {raw_task_id!r}")
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
                errors.append(f"`spawn_team` missing required fields {sorted(missing)}: {item!r}")
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
                errors.append(f"`spawn_worker` has non-integer task_id: {raw_task_id!r}")
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
                errors.append(f"`kill_entity` missing required fields {sorted(missing)}: {item!r}")
                logger.warning("kill_entity missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, target=item["target"]))
            continue

        if atype == "request_decision":
            missing = _REQUEST_DECISION_REQUIRED - item.keys()
            if missing:
                errors.append(
                    f"`request_decision` missing required fields {sorted(missing)}: {item!r}"
                )
                logger.warning("request_decision missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, to=item["to"], text=item["text"]))
            continue

        if atype == "request_payment":
            missing = _REQUEST_PAYMENT_REQUIRED - item.keys()
            if missing:
                errors.append(
                    f"`request_payment` missing required fields {sorted(missing)}: {item!r}"
                )
                logger.warning("request_payment missing fields %s: %s", missing, item)
                continue
            try:
                amount = int(item["amount_cents"])
            except (TypeError, ValueError):
                errors.append(
                    f"`request_payment` has non-integer amount_cents: {item.get('amount_cents')!r}"
                )
                logger.warning(
                    "request_payment has non-integer amount_cents: %r",
                    item.get("amount_cents"),
                )
                continue
            if amount <= 0:
                errors.append(f"`request_payment` has non-positive amount_cents: {amount!r}")
                logger.warning("request_payment has non-positive amount_cents: %r", amount)
                continue
            currency = item["currency"]
            if not isinstance(currency, str) or len(currency) != 3:
                errors.append(
                    f"`request_payment` has invalid currency (must be 3-letter code): {currency!r}"
                )
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

        errors.append(f"Unknown action type {atype!r}, skipped.")
        logger.warning("Unknown action type %r, skipping", atype)

    return clean_text, actions, errors
