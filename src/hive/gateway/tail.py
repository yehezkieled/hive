"""Live tail: a read-only view of a worker's recent output through ``fm-peek.sh``."""

from __future__ import annotations

import re

from hive.gateway.actions import ActionError, check_id, run_script
from hive.gateway.settings import GatewaySettings

MAX_CHARS = 64 * 1024
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def clean_output(text: str) -> str:
    """Drop terminal escapes and control bytes; keep newlines and tabs; bound the size."""
    text = _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c if c.isprintable() or c in "\n\t" else " " for c in text)
    return text[-MAX_CHARS:]


async def peek(settings: GatewaySettings, task: str) -> str:
    check_id(task, "task id")
    code, out = await run_script(
        settings, "fm-peek.sh", task, str(settings.tail_lines), timeout_s=settings.tail_timeout_s
    )
    if code != 0:
        first = clean_output(out).strip().splitlines()
        raise ActionError(f"fm-peek: {first[0][:200] if first else 'failed'}", 502)
    return clean_output(out)
