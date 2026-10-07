"""GitHub repo names for the desk's projects, read from each clone's ``origin`` remote.

Projects are keyed (routing, links, the registry) by the clone's registry name, which can lag a
GitHub rename. The desk shows the repo name instead, read strictly read-only from
``<fm_home>/projects/<name>/.git/config`` (no git subprocess) and cached, so a request never
waits on it past the first sight of a project; a stale name is refreshed off the request path.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable
from pathlib import Path

_TTL_S = 60.0
_MAX_CONFIG = 256 * 1024
_ORIGIN = re.compile(r'^\[remote "origin"\]\s*$', re.M)
_URL = re.compile(r"^\s*url\s*=\s*(\S+)\s*$", re.M)
_SECTION = re.compile(r"^\[", re.M)
_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")


def repo_name_from_url(url: str) -> str:
    """``https://github.com/o/r.git`` / ``git@github.com:o/r.git`` / ``/path/r`` -> ``r``."""
    tail = re.split(r"[/:]", url.strip().rstrip("/"))[-1]
    tail = tail.removesuffix(".git")
    return tail if _NAME.fullmatch(tail) else ""


def read_origin_repo(clone: Path) -> str:
    """The origin remote's repo name, or empty when there is no readable remote."""
    cfg = clone / ".git" / "config"
    try:
        if cfg.stat().st_size > _MAX_CONFIG:
            return ""
        text = cfg.read_text(errors="replace")
    except OSError:
        return ""
    m = _ORIGIN.search(text)
    if not m:
        return ""
    body = text[m.end() :]
    nxt = _SECTION.search(body)
    u = _URL.search(body[: nxt.start()] if nxt else body)
    return repo_name_from_url(u.group(1)) if u else ""


class RepoNames:
    """Registry name -> GitHub repo name, cached; unknown/no-remote names are absent."""

    def __init__(self, projects_dir: Path) -> None:
        self._dir = projects_dir
        self._seen: dict[str, tuple[float, str]] = {}
        self._task: asyncio.Task[None] | None = None

    def _read(self, name: str) -> str:
        if not _NAME.fullmatch(name):
            return ""
        return read_origin_repo(self._dir / name)

    async def get(self, names: Iterable[str]) -> dict[str, str]:
        """Names that differ from their repo name are the only ones that matter, but return all
        known. A first sight reads in a worker thread; a stale entry is served and refreshed in
        the background."""
        want = [n for n in dict.fromkeys(names) if n]
        now = time.monotonic()
        fresh = [n for n in want if n not in self._seen]
        if fresh:
            found = await asyncio.to_thread(lambda: {n: self._read(n) for n in fresh})
            for n, repo in found.items():
                self._seen[n] = (now, repo)
        stale = [n for n in want if now - self._seen[n][0] > _TTL_S]
        if stale and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._refresh(stale))
        return {n: self._seen[n][1] for n in want if self._seen[n][1]}

    async def _refresh(self, names: list[str]) -> None:
        try:
            found = await asyncio.to_thread(lambda: {n: self._read(n) for n in names})
        except Exception:
            return
        now = time.monotonic()
        for n, repo in found.items():
            self._seen[n] = (now, repo)
