"""The desk's own small files under ``settings.data_dir``: owner-only (mode 0600), never
half-written."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one step, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def append_private(path: Path, text: str) -> None:
    """Add ``text`` to the end of ``path``, creating it readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(text)


def claim_slot(path: Path, now: float, limit: int, window_s: float) -> float:
    """Count one attempt at ``now`` in ``path``, across processes and restarts: 0 when fewer
    than ``limit`` were counted in the last ``window_s`` seconds (and this one now is), else
    the seconds to wait. The file is owner-only and locked while it is read and rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            seen = [float(t) for t in json.loads(fh.read() or "[]")]
        except (ValueError, TypeError):
            seen = []
        recent = sorted(t for t in seen if now - window_s < t <= now)
        if len(recent) >= limit:
            return max(1.0, recent[0] + window_s - now)
        recent.append(now)
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(recent))
        return 0.0
