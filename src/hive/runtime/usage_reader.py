"""Reads per-turn token usage from Claude Code session .jsonl transcript files."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ZERO: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}


def read_last_usage(session_dir: Path) -> dict[str, int]:
    """Return token usage from the last assistant turn in a session .jsonl file.

    Looks for any *.jsonl file in session_dir, reads the last entry that has
    role='assistant' and a usage sub-object. Returns zeros on any failure.
    """
    try:
        jsonl_files = sorted(session_dir.glob("*.jsonl"))
        if not jsonl_files:
            return _ZERO.copy()

        lines = jsonl_files[-1].read_text(encoding="utf-8").splitlines()
    except OSError:
        return _ZERO.copy()

    last_usage: dict[str, int] | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("role") == "assistant" and isinstance(entry.get("usage"), dict):
            raw = entry["usage"]
            last_usage = {
                "input_tokens": int(raw.get("input_tokens", 0)),
                "output_tokens": int(raw.get("output_tokens", 0)),
            }

    return last_usage if last_usage is not None else _ZERO.copy()
