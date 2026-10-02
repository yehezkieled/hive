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
import os
import re
import time
from dataclasses import dataclass

from hive.gateway.settings import GatewaySettings

PINNED_SCHEMA_MAJOR = 1
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
    return parse_snapshot(out.decode("utf-8", "replace"))


class SnapshotProvider:
    """Short-TTL cache so a burst of page loads runs the script once."""

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._at = 0.0
        self._value: Snapshot | None = None

    async def get(self, fresh: bool = False) -> Snapshot:
        async with self._lock:
            now = time.monotonic()
            if fresh or self._value is None or now - self._at > self._settings.snapshot_ttl_s:
                self._value = await run_snapshot(self._settings)
                self._at = time.monotonic()
            return self._value
