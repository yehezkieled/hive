"""Shared plumbing for headless (one-subprocess-per-turn) adapters."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from hive.runtime.harness import HarnessError, HarnessErrorKind, RunMode


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


async def run_process(
    argv: list[str],
    *,
    harness: str,
    stdin_text: str,
    cwd: Path | None,
    env: dict[str, str] | None,
    timeout: float,
) -> ProcResult:
    """Run one headless turn. The prompt goes over stdin, never argv: a prompt can
    be hundreds of KB (inbox + retrieved context) and may start with ``-`` or ``/``.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except (FileNotFoundError, PermissionError) as e:
        raise HarnessError(
            HarnessErrorKind.UNAVAILABLE, harness, RunMode.HEADLESS, f"cannot start {argv[0]}: {e}"
        ) from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout=timeout)
    except TimeoutError as e:
        raise HarnessError(
            HarnessErrorKind.OTHER, harness, RunMode.HEADLESS, f"no result after {timeout:.0f}s"
        ) from e
    finally:
        # Timeout or cancellation (entity killed mid-turn): never leak the child.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    return ProcResult(
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


def parse_jsonl(text: str) -> list[dict]:
    """Decode a JSONL stream, skipping blank/garbled lines (stdout may carry noise)."""
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


# Failure-text classification. These patterns are matched against the harness's
# own error output (never against model prose). Each is anchored to a message
# the harness is known to emit — see tests/runtime/test_headless_errors.py for
# the captured samples; extend both together when a harness words one anew.
_AUTH_RE = re.compile(
    r"not logged in|please run /login|/login|no api key found|no models available|"
    r"authentication[_ ]failed|invalid (x-)?api[- ]key|oauth token (has )?(expired|revoked)|"
    r"credentials[_ ]not[_ ]configured|\b401\b|unauthori[sz]ed",
    re.I,
)
_QUOTA_RE = re.compile(
    r"hit your (usage )?limit|usage limit|limit reached|out of (extra )?usage|rate[_ -]?limit|"
    r"\b429\b|quota|credit balance|insufficient[_ ]credit|billing[_ ]error|"
    r"monthly (usage|credit)",
    re.I,
)
_REFUSED_RE = re.compile(
    r"(headless|non-?interactive|print mode|-p/--print|sdk usage)[^\n]{0,80}"
    r"(not (allowed|available|supported|permitted|included)|disabled|requires?|refus)|"
    r"(not (allowed|available|supported|permitted|included)|disabled|requires?|refus)[^\n]{0,80}"
    r"(headless|non-?interactive|print mode|-p/--print|sdk usage)",
    re.I,
)


def classify_failure_text(text: str) -> HarnessErrorKind:
    """Map harness error text to a kind; ``OTHER`` when nothing recognisable matches.

    Order matters: a refusal that mentions "login" is still a refusal, and
    "login" inside a quota message must not read as AUTH.
    """
    if _REFUSED_RE.search(text):
        return HarnessErrorKind.REFUSED
    if _QUOTA_RE.search(text) and not re.search(r"not logged in|no api key", text, re.I):
        return HarnessErrorKind.QUOTA
    if _AUTH_RE.search(text):
        return HarnessErrorKind.AUTH
    return HarnessErrorKind.OTHER


def tail(text: str, n: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= n else "…" + text[-n:]
