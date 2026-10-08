"""Open Lavish review sessions, read strictly read-only from ``~/.lavish-axi/state.json``.

The desk lists them so the owner can open one from a phone. Links are always built from the
session key on the tailnet board URL, never copied from the recorded local address.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_MAX_STATE = 8 * 1024 * 1024
_MAX_HEAD = 8192
# The nudge: a page untouched this long, or any page beyond the newest few, looks done.
STALE_AFTER_S = 2 * 24 * 3600
KEEP_OPEN = 8


@dataclass(frozen=True)
class Review:
    key: str
    title: str
    project: str
    reply: bool  # the agent answered last: a reply is waiting for the owner
    updated: str
    stale: bool = False  # looks done: old, or beyond the newest ``KEEP_OPEN`` open pages


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
    """The nearest path component naming a known project, else empty."""
    for part in reversed(file.parts[:-1]):
        base = re.sub(r"-[0-9a-f]{6}$", "", part)
        if part in projects or base in projects:
            return part if part in projects else base
    return ""


def state_signature(state: Path | None) -> tuple[int, int] | None:
    """(mtime, size) of the Lavish state file: one ``stat``, so the live watcher can poll it."""
    if state is None:
        return None
    try:
        st = state.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def digest(reviews: list[Review]) -> str:
    """What the Review pages list shows; changes when a page opens, closes or gets a reply."""
    shown = [(r.key, r.title, r.reply, r.stale) for r in reviews]
    return hashlib.sha256(repr(shown).encode()).hexdigest()


def _age_s(updated: str, now: float) -> float | None:
    try:
        return now - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _mark_stale(reviews: list[Review], now: float) -> list[Review]:
    """Flag pages that look done: older than two days, plus the oldest ones beyond eight open.

    A page the agent has answered (a reply waits for the owner) is never flagged.
    """
    over = max(0, len(reviews) - KEEP_OPEN)
    flagged = {
        r.key for r in reviews if not r.reply and (_age_s(r.updated, now) or 0) > STALE_AFTER_S
    }
    for r in sorted(reviews, key=lambda r: r.updated):
        if len(flagged) >= over:
            break
        if not r.reply:
            flagged.add(r.key)
    return [
        Review(r.key, r.title, r.project, r.reply, r.updated, r.key in flagged) for r in reviews
    ]


def _sessions(state: Path | None) -> list[dict]:
    if state is None:
        return []
    try:
        if state.stat().st_size > _MAX_STATE:
            return []
        sessions = json.loads(state.read_text()).get("sessions")
    except (OSError, ValueError, AttributeError):
        return []
    return (
        [s for s in sessions.values() if isinstance(s, dict)] if isinstance(sessions, dict) else []
    )


def session_file(state: Path | None, key: str) -> Path | None:
    """The artifact file of an open session, looked up by key (never taken from a request)."""
    for s in _sessions(state):
        if s.get("status") == "open" and s.get("key") == key and isinstance(s.get("file"), str):
            return Path(s["file"])
    return None


def read_reviews(state: Path | None, projects: set[str], now: float | None = None) -> list[Review]:
    """Open sessions, reply-waiting first, then most recently updated. Unreadable state is empty."""
    out: list[Review] = []
    for s in _sessions(state):
        if not isinstance(s, dict) or s.get("status") != "open":
            continue
        key, file = s.get("key"), s.get("file")
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(file, str):
            continue
        chat = s.get("chat")
        chat = [c for c in chat if isinstance(c, dict)] if isinstance(chat, list) else []
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
    out = _mark_stale(out, time.time() if now is None else now)
    out.sort(key=lambda r: r.updated, reverse=True)
    out.sort(key=lambda r: not r.reply)
    return out
