"""Pi adapter — headless ``pi -p --mode json``, one subprocess per turn.

Pi (https://pi.dev) is Hive's preferred harness (ADR 0029): it signs in to many
providers independently of Claude Code's login, which Claude Code drops roughly
monthly. Only the headless mode exists for Pi — there is no Pi PTY driver, so
when Pi fails the router moves to the next *harness*, not the next mode.

Protocol facts (Pi docs ``json.md`` / ``message-types.md``, verified against the
installed 0.87 CLI for the failure paths): ``--mode json`` writes a ``session``
header then JSONL session events on stdout; the authoritative assistant reply is
the last ``message_end`` with ``role == "assistant"``; a failed model call has
``stopReason == "error"`` + ``errorMessage``; a startup failure (no credentials)
prints to stderr and exits 1 with only the header on stdout.

Capability gaps, accepted (ADR 0029): Pi has no MCP servers, no Claude-style
``--allowedTools`` names, and no PreToolUse hook — so the ownership guard
(ADR 0017) does not fence a Pi entity. Its fence is its working directory only.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig, build_system_prompts
from hive.runtime.base import Runtime
from hive.runtime.harness import (
    HarnessError,
    HarnessStatus,
    RunMode,
)
from hive.runtime.headless import (
    classify_failure_text,
    parse_jsonl,
    run_process,
    tail,
    unless_work_done,
)

logger = logging.getLogger(__name__)

HARNESS = "pi"


def probe_pi() -> HarnessStatus:
    """Installed? Signed in to at least one provider?

    With ``HIVE_PI_PROVIDER`` set, asks ``pi auth check --provider X --json``
    (precise). Otherwise ``pi --list-models``: Pi lists only models whose provider
    has credentials, and prints "No models available" when it has none.
    """
    binary = config.PI_BINARY
    if shutil.which(binary) is None:
        return HarnessStatus(HARNESS, False, None, f"{binary} not found")
    provider = config.PI_PROVIDER
    argv = (
        [binary, "auth", "check", "--provider", provider, "--json"]
        if provider
        else [binary, "--list-models"]
    )
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return HarnessStatus(HARNESS, True, None, f"{' '.join(argv[1:3])} failed: {e}")
    out = f"{proc.stdout}\n{proc.stderr}"
    if provider:
        ready = '"status":"ready"' in out.replace(" ", "")
        return HarnessStatus(
            HARNESS, True, ready, "" if ready else f"provider {provider!r} not ready"
        )
    if "no models available" in out.lower():
        return HarnessStatus(HARNESS, True, False, "no provider credentials configured")
    return HarnessStatus(HARNESS, True, proc.returncode == 0 and bool(out.strip()))


class PiAdapter(Runtime):
    """Runtime for Pi via ``pi -p --mode json``."""

    def __init__(
        self,
        cfg: AdapterConfig,
        cwd: Path | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        self._config = cfg
        self._cwd = cwd
        # ``--session-id`` creates the session when missing and resumes it when
        # present, so one stable id per conversation covers first turn, later
        # turns, and a restart (the Entity persists it) — even an id minted by
        # another harness is merely a fresh Pi session.
        self._session_id = resume_session_id or str(uuid.uuid4())
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def is_alive(self) -> bool:
        return self._started

    def adopt_session(self, session_id: str | None) -> None:
        if session_id:
            self._session_id = session_id

    def _argv(self) -> list[str]:
        argv = [config.PI_BINARY, "-p", "--mode", "json", "--session-id", self._session_id]
        if config.PI_PROVIDER:
            argv.extend(["--provider", config.PI_PROVIDER])
        model = config.pi_model_for(self._config.role)
        if model:
            argv.extend(["--model", model])
        for block in build_system_prompts(self._config):
            argv.extend(["--append-system-prompt", block])
        return argv

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        proc = await run_process(
            self._argv(),
            harness=HARNESS,
            stdin_text=prompt,
            cwd=self._cwd,
            env=None,
            timeout=config.HEADLESS_TIMEOUT_S,
        )
        events = parse_jsonl(proc.stdout)
        assistants = [
            e["message"]
            for e in events
            if e.get("type") == "message_end"
            and isinstance(e.get("message"), dict)
            and e["message"].get("role") == "assistant"
        ]
        # A model error inside the run (provider 401/429, retries exhausted).
        failed = next((m for m in reversed(assistants) if m.get("stopReason") == "error"), None)
        if failed is None and proc.returncode == 0 and assistants:
            return self._success(events, assistants)
        retry_end = next(
            (
                e
                for e in reversed(events)
                if e.get("type") == "auto_retry_end" and e.get("success") is False
            ),
            None,
        )
        if failed is not None:
            detail = str(failed.get("errorMessage") or "model call failed")
        elif retry_end is not None:
            detail = str(retry_end.get("finalError") or "retries exhausted")
        else:
            detail = tail(proc.stderr) or f"exit {proc.returncode}, no assistant reply"
        kind = unless_work_done(classify_failure_text(detail), _did_work(events, assistants))
        raise HarnessError(kind, HARNESS, RunMode.HEADLESS, detail)

    def _success(self, events: list[dict], assistants: list[dict]) -> tuple[str, dict]:
        last = assistants[-1]
        content = last.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "".join(
                b.get("text", "")
                for b in (content or [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
        header = next((e for e in events if e.get("type") == "session"), {})
        session_id = header.get("id") or self._session_id
        self._session_id = session_id

        def _u(m: dict, key: str) -> int:
            return int((m.get("usage") or {}).get(key) or 0)

        cost = sum(
            float(((m.get("usage") or {}).get("cost") or {}).get("total") or 0) for m in assistants
        )
        usage = {
            "context_tokens": sum(_u(last, key) for key in ("input", "cacheRead", "cacheWrite")),
            "input_tokens": sum(_u(m, "input") for m in assistants),
            "output_tokens": sum(_u(m, "output") for m in assistants),
            "cache_creation_input_tokens": sum(_u(m, "cacheWrite") for m in assistants),
            "cache_read_input_tokens": sum(_u(m, "cacheRead") for m in assistants),
            "session_id": session_id,
            # Pi reports per-call cost from its provider catalog; 0 on a flat plan.
            "cost_usd": cost or None,
        }
        return text, usage


def _did_work(events: list[dict], assistants: list[dict]) -> bool:
    """True once the turn issued a tool call (a ``toolCall`` block or a tool run)."""
    if any(e.get("type") == "tool_execution_start" for e in events):
        return True
    return any(
        isinstance(b, dict) and b.get("type") == "toolCall"
        for m in assistants
        if isinstance(m.get("content"), list)
        for b in m["content"]
    )
