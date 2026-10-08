"""Run firstmate's fleet snapshot and pin the schema the desk relies on.

The gateway never parses the backlog itself. It runs one fixed read-only script,
``fm-fleet-snapshot.sh --json``, under ``FM_HOME``, and accepts only the pinned major
schema. Added fields are tolerated; an unknown schema, a failed run or invalid JSON
becomes a ``Snapshot`` with ``data=None`` and a reason, so pages show a read-only
fallback instead of breaking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from hive.gateway.settings import GatewaySettings

PINNED_SCHEMA_MAJOR = 1
log = logging.getLogger(__name__)
_SCHEMA_RE = re.compile(r"^fm-fleet-snapshot\.v(\d+)$")
_MAX_OUTPUT = 16 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    data: dict | None
    schema: str | None
    generated: str | None
    reason: str | None = None  # why data is None

    @property
    def ok(self) -> bool:
        return self.data is not None


def parse_snapshot(raw: str) -> Snapshot:
    """Validate raw script output against the pinned schema."""
    try:
        data = json.loads(raw)
    except ValueError:
        return Snapshot(None, None, None, "snapshot output is not valid JSON")
    if not isinstance(data, dict):
        return Snapshot(None, None, None, "snapshot output is not a JSON object")
    schema = data.get("schema")
    generated = data.get("generated") if isinstance(data.get("generated"), str) else None
    match = _SCHEMA_RE.match(schema) if isinstance(schema, str) else None
    if match is None or int(match.group(1)) != PINNED_SCHEMA_MAJOR:
        return Snapshot(
            None,
            schema if isinstance(schema, str) else None,
            generated,
            "firstmate is newer than this desk (unrecognised snapshot schema)",
        )
    return Snapshot(data, schema, generated)


async def run_snapshot(settings: GatewaySettings) -> Snapshot:
    script = settings.snapshot_script
    if not script.is_file():
        return Snapshot(None, None, None, "firstmate snapshot script not found")
    env = {**os.environ, "FM_HOME": str(settings.fm_home)}
    try:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            "--json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=str(settings.fm_home),
        )
    except OSError:
        return Snapshot(None, None, None, "could not run the firstmate snapshot")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), settings.snapshot_timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Snapshot(None, None, None, "firstmate snapshot timed out")
    if proc.returncode != 0 or len(out) > _MAX_OUTPUT:
        return Snapshot(None, None, None, "firstmate snapshot failed")
    snap = parse_snapshot(out.decode("utf-8", "replace"))
    if snap.data is not None:
        snap.data["registered_projects"] = await asyncio.to_thread(
            registered_projects, settings.fm_home, snap.data
        )
        snap.data["project_descriptions"] = await asyncio.to_thread(
            project_descriptions, settings.fm_home, snap.data
        )
    return snap


_REGISTRY_LINE = re.compile(r"^- (.+?)(?: \[| - )")


def registered_projects(fm_home: Path, data: dict) -> list[str]:
    """Project names from the main registry and every local second mate's, so a project with
    no backlog rows still has a card. Unreadable registries add nothing."""
    homes = [fm_home]
    mates = data.get("secondmate_current")
    for rec in mates.get("records", []) if isinstance(mates, dict) else []:
        home = rec.get("home") if isinstance(rec, dict) else None
        if isinstance(home, str) and home.startswith("/") and not rec.get("host"):
            homes.append(Path(home))
    names: list[str] = []
    for home in homes:
        try:
            lines = (home / "data" / "projects.md").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            m = _REGISTRY_LINE.match(line)
            if m and m.group(1) not in names:
                names.append(m.group(1))
    return names


_NOTE_MAX = 240
_ENTRY_NOTE = re.compile(r"^- .+?(?:\]| - )\s*(?:-\s*)?(.*)$")
_ADDED = re.compile(r"\s*\((?:added|registered)[^)]*\)\s*$", re.I)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _NOTE_MAX:
        return text
    cut = text.rfind(" ", 0, _NOTE_MAX)
    return text[: cut if cut > 0 else _NOTE_MAX].rstrip(" ,;:") + "…"


def _registry_notes(home: Path) -> dict[str, str]:
    """``name -> description`` from one home's ``data/projects.md`` (``- name [flags] - text``)."""
    try:
        lines = (home / "data" / "projects.md").read_text().splitlines()
    except (OSError, UnicodeError):
        return {}
    notes: dict[str, str] = {}
    for line in lines:
        name = _REGISTRY_LINE.match(line)
        body = _ENTRY_NOTE.match(line)
        if name and body:
            text = _ADDED.sub("", body.group(1)).strip()
            if text and name.group(1) not in notes:
                notes[name.group(1)] = _clip(text)
    return notes


def _charter_summary(home: Path) -> str:
    """The first sentence of a second mate's ``data/charter.md`` charter, or empty."""
    try:
        text = (home / "data" / "charter.md").read_text()
    except (OSError, UnicodeError):
        return ""
    m = re.search(r"^# Charter\s*\n(.*?)(?=^# |\Z)", text, re.M | re.S)
    body = " ".join((m.group(1) if m else "").split())
    if not body:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    return _clip(sentence)


def _owned_names(home: Path, rec: dict, data: dict) -> list[str]:
    """The projects a second mate owns: its session task's list, then its own registry."""
    names: list[str] = []
    tasks = data.get("tasks")
    for task in tasks if isinstance(tasks, list) else []:
        if isinstance(task, dict) and task.get("id") == rec.get("id"):
            owned = task.get("secondmate_projects")
            names += [p for p in owned if isinstance(p, str)] if isinstance(owned, list) else []
    try:
        for line in (home / "data" / "projects.md").read_text().splitlines():
            m = _REGISTRY_LINE.match(line)
            if m:
                names.append(m.group(1))
    except (OSError, UnicodeError):
        pass
    return list(dict.fromkeys(names))


def project_descriptions(fm_home: Path, data: dict) -> dict[str, str]:
    """A one-line description per registered project: a second mate's charter summary for the
    projects it owns, else the first mate's ``data/projects.md`` entry. Missing or unparsable
    sources add nothing."""
    notes = _registry_notes(fm_home)
    mates = data.get("secondmate_current")
    for rec in mates.get("records", []) if isinstance(mates, dict) else []:
        home = rec.get("home") if isinstance(rec, dict) else None
        if not (isinstance(home, str) and home.startswith("/")) or rec.get("host"):
            continue
        charter = _charter_summary(Path(home))
        if charter:
            for name in _owned_names(Path(home), rec, data):
                notes[name] = charter
    return notes


def project_notes(snap: Snapshot) -> dict[str, str]:
    raw = snap.data.get("project_descriptions") if snap.data else None
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v}


_MAX_STALE_S = 120.0


class SnapshotProvider:
    """Stale-while-revalidate cache around the ~3 s snapshot script.

    A page request never waits on the script once a value exists: an expired value is served
    as is while one background refresh runs (single flight). Only the first call, an explicit
    ``fresh`` (after an action, and the live watcher) or a zero TTL (tests) wait for it. A
    caller that is not ``fresh`` takes a value another caller produced while it waited for
    the lock, so a cold burst runs the script once. A failed background refresh keeps the
    last good value for ``_MAX_STALE_S`` and is retried on the next request.
    """

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._at = 0.0
        self._good_at = 0.0
        self._value: Snapshot | None = None
        self._task: asyncio.Task[None] | None = None

    async def get(self, fresh: bool = False) -> Snapshot:
        ttl = self._settings.snapshot_ttl_s
        if fresh or self._value is None or ttl <= 0:
            return await self._refresh(keep_good=False, fresh=fresh)
        if time.monotonic() - self._at > ttl and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._refresh_quietly())
        return self._value

    async def _refresh_quietly(self) -> None:
        try:
            await self._refresh(keep_good=True, fresh=False)
        except Exception:
            log.exception("background snapshot refresh failed")

    async def _refresh(self, keep_good: bool, fresh: bool) -> Snapshot:
        started = time.monotonic()
        async with self._lock:
            if not fresh and self._value is not None and self._at >= started:
                return self._value
            snap = await run_snapshot(self._settings)
            now = time.monotonic()
            old = self._value
            if snap.data is not None:
                self._good_at = now
            elif keep_good and old is not None and old.data is not None:
                if now - self._good_at < _MAX_STALE_S:
                    return old  # keep serving the last good value; retried on the next request
            self._value, self._at = snap, now
            return snap
