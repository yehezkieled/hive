"""Parse structured actions from entity responses.

Entities can include a <hive_actions> block in their text response
containing a JSON array of actions. The orchestrator extracts these,
validates permissions, and routes messages accordingly.

Supported action types:
- ``message``: send text to another entity. Fields: ``to``, ``text``.
- ``request_mode_change``: ask for an elevated permission mode. Fields:
  ``requested_mode`` (yolo|yotree), ``reason`` (optional).
- ``report_failure``: a task-bound entity tells the orchestrator the
  current task is failing. Fields: ``reason``; optional ``task_id``
  override (defaults to the entity's current task_id).
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


@dataclass
class Action:
    """A structured action extracted from an entity response.

    Only the fields relevant to ``type`` are populated. ``to``/``text``
    are set for ``message`` actions; ``requested_mode``/``reason`` for
    ``request_mode_change``; ``reason``/``task_id`` for ``report_failure``.
    """

    type: str
    to: str | None = None
    text: str | None = None
    requested_mode: str | None = None
    reason: str | None = None
    task_id: int | None = None


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

        logger.warning("Unknown action type %r, skipping", atype)

    return clean_text, actions
