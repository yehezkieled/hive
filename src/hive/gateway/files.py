"""The desk's own small files under ``settings.data_dir``: owner-only (mode 0600), never
half-written."""

from __future__ import annotations

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
