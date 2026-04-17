"""Parse structured actions from entity responses.

Entities can include a <hive_actions> block in their text response
containing a JSON array of actions. The orchestrator extracts these,
validates permissions, and routes messages accordingly.
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

REQUIRED_FIELDS = {"type", "to", "text"}


@dataclass
class Action:
    """A structured action extracted from an entity response."""

    type: str
    to: str
    text: str


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
        if not isinstance(item, dict):
            continue
        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            logger.warning("Action missing required fields %s: %s", missing, item)
            continue
        if item["type"] != "message":
            logger.warning("Unknown action type %r, skipping", item["type"])
            continue
        actions.append(Action(type=item["type"], to=item["to"], text=item["text"]))

    return clean_text, actions
