"""Cleans raw PTY output from Claude Code into plain assistant text."""

from __future__ import annotations

import re

# Matches all ANSI/VT100 escape sequences: ESC [ ... final-byte
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_LINE = re.compile(rf"^\s*[{re.escape(_SPINNER_CHARS)}]\s")

# Lines that report tool invocations: start with a status glyph or tool name
_TOOL_NAMES = "Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|Task"
_PROGRESS_LINE = re.compile(rf"^\s*(?:[✓✗→·]|\s{{2}})(?:\s*(?:{_TOOL_NAMES})\()")


def _resolve_cr_rewrites(text: str) -> str:
    """Collapse \r-rewritten lines: keep only what the final \r lands on."""
    lines = text.split("\n")
    resolved = []
    for line in lines:
        parts = line.split("\r")
        resolved.append(parts[-1])
    return "\n".join(resolved)


def _drop_noise_lines(text: str) -> str:
    kept = [
        line
        for line in text.split("\n")
        if not _SPINNER_LINE.match(line) and not _PROGRESS_LINE.match(line)
    ]
    return "\n".join(kept)


def clean(raw: str) -> str:
    """Strip terminal noise from raw PTY output, leaving only assistant text."""
    result = _ANSI_ESCAPE.sub("", raw)
    result = _resolve_cr_rewrites(result)
    result = _drop_noise_lines(result)
    return result
