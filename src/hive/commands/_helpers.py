"""Small parsing helpers shared by more than one command group.

Live here (not in dispatch.py) so the group collaborators can import them
without a cycle back through the facade.
"""

from __future__ import annotations


def _strip_quotes(text: str) -> str:
    """Strip a single matching pair of leading/trailing quotes (if any)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _parse_task_id(raw: str) -> int | None:
    """Extract the first integer token from a /task done|cancel args string."""
    raw = (raw or "").strip()
    if not raw:
        return None
    first = raw.split()[0]
    try:
        return int(first)
    except ValueError:
        return None
