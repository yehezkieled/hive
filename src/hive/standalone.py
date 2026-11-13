"""What Hive's standalone services share: the Telegram bot and the gone-dark alarm.

Each runs with no database and no Hive runtime, holds one secret that must never reach a log
line, and looks at Tailscale read-only. Standard library only, so neither service pulls in the
other's dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

EX_CONFIG = 78  # sysexits: the config is wrong; the unit does not restart on it
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
TAILSCALE_TIMEOUT_S = 8.0


class RedactingFormatter(logging.Formatter):
    """Replaces each secret in a formatted record, tracebacks included."""

    label = "[secret]"  # what a secret reads as; each service names its own

    def __init__(self, fmt: str, secrets: tuple[str, ...]) -> None:
        super().__init__(fmt, datefmt="%H:%M:%S")
        self._secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, self.label)
        return text


def setup_logging(formatter: logging.Formatter) -> None:
    """Log to stderr through ``formatter`` alone, with the HTTP client's own logging quiet:
    httpx logs every request URL at INFO, and the URL is, or carries, the secret."""
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def tailscale_state(binary: Path | None, timeout_s: float = TAILSCALE_TIMEOUT_S) -> str:
    """One plain phrase for the PC's Tailscale: read only, never changes anything."""
    if binary is None:
        return "unknown (Tailscale's command was not found)"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "status",
            "--json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return "unknown (could not run Tailscale)"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return "unknown (Tailscale did not answer)"
    if proc.returncode != 0 and not out.strip():
        return "not connected: Tailscale is not running"
    try:
        data = json.loads(out)
        backend = str(data.get("BackendState", ""))
        online = bool((data.get("Self") or {}).get("Online"))
    except (ValueError, AttributeError, TypeError):
        return "unknown (Tailscale's answer was unreadable)"
    if backend == "Running" and online:
        return "connected"
    if backend == "Running":
        return "running but offline (no route to Tailscale's servers)"
    if backend == "NeedsLogin":
        return "not connected: it needs a sign-in"
    return f"not connected (state: {backend.lower() or 'unknown'})"
