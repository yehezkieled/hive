"""Open Lavish review sessions, read strictly read-only from ``~/.lavish-axi/state.json``.

The desk lists them so the owner can open one from a phone. Links are always built from the
session key on the tailnet board URL, never copied from the recorded local address.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_MAX_STATE = 8 * 1024 * 1024
_MAX_HEAD = 8192


@dataclass(frozen=True)
class Review:
    key: str
    title: str
    project: str
    reply: bool  # the agent answered last: a reply is waiting for the owner
    updated: str


def _title(file: Path) -> str:
    try:
        with file.open("rb") as fh:
            head = fh.read(_MAX_HEAD).decode("utf-8", "replace")
    except OSError:
        head = ""
    m = _TITLE.search(head)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    return text or file.stem.replace("-", " ").replace("_", " ")


def _project(file: Path, projects: set[str]) -> str:
    """The nearest path component naming a known project, else the owning home's name."""
    for part in reversed(file.parts[:-1]):
        base = re.sub(r"-[0-9a-f]{6}$", "", part)
        if part in projects or base in projects:
            return part if part in projects else base
    return ""


def read_reviews(state: Path | None, projects: set[str]) -> list[Review]:
    """Open sessions, reply-waiting first, then most recently updated. Unreadable state is empty."""
    if state is None:
        return []
    try:
        if state.stat().st_size > _MAX_STATE:
            return []
        sessions = json.loads(state.read_text()).get("sessions")
    except (OSError, ValueError, AttributeError):
        return []
    out: list[Review] = []
    for s in sessions.values() if isinstance(sessions, dict) else []:
        if not isinstance(s, dict) or s.get("status") != "open":
            continue
        key, file = s.get("key"), s.get("file")
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(file, str):
            continue
        chat = [c for c in s.get("chat") or [] if isinstance(c, dict)]
        updated = s.get("updated_at") if isinstance(s.get("updated_at"), str) else ""
        path = Path(file)
        out.append(
            Review(
                key,
                _title(path),
                _project(path, projects),
                bool(chat) and chat[-1].get("role") == "agent",
                updated,
            )
        )
    out.sort(key=lambda r: r.updated, reverse=True)
    out.sort(key=lambda r: not r.reply)
    return out
