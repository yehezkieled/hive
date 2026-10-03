"""Claude Code headless mode — one ``claude -p`` subprocess per turn.

The default mode of the Claude harness (ADR 0029). Conversation continuity comes
from ``--resume <session_id>``; the PTY fallback (``claude_adapter.ClaudeAdapter``)
picks the same conversation back up with ``--continue``.

Failures are read from Claude Code's own ``stream-json`` result: the assistant
message carries an ``error`` code (``authentication_failed``, ``rate_limit``,
``billing_error``, ...) and the final ``result`` event ``is_error`` + text. Those
— not guesses — decide whether the router falls back to the PTY.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig, build_system_prompts
from hive.runtime.base import Runtime
from hive.runtime.claude_adapter import build_claude_extra_args
from hive.runtime.harness import (
    HarnessError,
    HarnessErrorKind,
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

HARNESS = "claude"

# `error` codes on the synthetic assistant message Claude Code emits on an API
# failure (Claude Agent SDK `AssistantMessageError`). Deterministic, so they win
# over text matching.
_ERROR_CODE_KIND = {
    "authentication_failed": HarnessErrorKind.AUTH,
    "billing_error": HarnessErrorKind.QUOTA,
    "rate_limit": HarnessErrorKind.QUOTA,
}

# Env that would make `claude -p` bill a per-token API key instead of the
# logged-in subscription. Headless must never silently spend API money
# (ADR 0001/0007 — API-billed is the expensive path, only by deliberate choice).
_API_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def probe_claude() -> HarnessStatus:
    """Installed? Signed in? Via ``claude auth status`` (JSON, exit 1 when out)."""
    binary = config.CLAUDE_BINARY
    if shutil.which(binary) is None:
        return HarnessStatus(HARNESS, False, None, f"{binary} not found")
    try:
        proc = subprocess.run(
            [binary, "auth", "status"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as e:
        return HarnessStatus(HARNESS, True, None, f"`claude auth status` failed: {e}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "loggedIn" in data:
        if data["loggedIn"]:
            return HarnessStatus(HARNESS, True, True, str(data.get("authMethod") or ""))
        return HarnessStatus(HARNESS, True, False, "Claude Code is logged out")
    # Unrecognised output: trust the exit code (0 = signed in) over silence.
    return HarnessStatus(HARNESS, True, proc.returncode == 0, tail(proc.stderr or proc.stdout, 120))


def _headless_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _API_BILLING_ENV:
        env.pop(key, None)
    return env


class ClaudeHeadlessAdapter(Runtime):
    """Runtime for Claude Code via ``claude -p`` (stream-json), one process per turn."""

    def __init__(
        self,
        cfg: AdapterConfig,
        cwd: Path | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        self._config = cfg
        self._cwd = cwd
        self._session_id = resume_session_id
        self._started = False

    # -- Runtime ---------------------------------------------------------
    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def is_alive(self) -> bool:
        return self._started

    def adopt_session(self, session_id: str | None) -> None:
        """Follow the conversation to a session another mode of this harness advanced."""
        if session_id:
            self._session_id = session_id

    def _argv(self) -> list[str]:
        cfg = self._config
        argv = [
            config.CLAUDE_BINARY,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            cfg.model,
        ]
        from hive.models.entity import DANGEROUS_MODES

        if cfg.permission_mode in DANGEROUS_MODES:
            argv.append("--dangerously-skip-permissions")
        elif cfg.permission_mode and cfg.permission_mode != "default":
            argv.extend(["--permission-mode", cfg.permission_mode])
        for block in build_system_prompts(cfg):
            argv.extend(["--append-system-prompt", block])
        argv.extend(build_claude_extra_args(cfg))
        if self._session_id:
            argv.extend(["--resume", self._session_id])
        return argv

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        try:
            return await self._run(prompt)
        except ResumeLostError:
            # The remembered session is gone (cleared, or minted by another
            # harness): start a fresh conversation rather than failing the turn.
            logger.warning("claude: session %s not found; starting fresh", self._session_id)
            self._session_id = None
            try:
                return await self._run(prompt)
            except ResumeLostError as e:  # cannot happen without --resume; stay defensive
                raise HarnessError(HarnessErrorKind.OTHER, HARNESS, RunMode.HEADLESS, str(e)) from e

    async def _run(self, prompt: str) -> tuple[str, dict]:
        proc = await run_process(
            self._argv(),
            harness=HARNESS,
            stdin_text=prompt,
            cwd=self._cwd,
            env=_headless_env(),
            timeout=config.HEADLESS_TIMEOUT_S,
        )
        events = parse_jsonl(proc.stdout)
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result is None:
            # stdout is the turn transcript (model prose, tool I/O): classify only
            # the harness's own stderr.
            detail = tail(proc.stderr) or f"exit {proc.returncode}, no result"
            kind = unless_work_done(classify_failure_text(proc.stderr), _did_work(events))
            raise HarnessError(kind, HARNESS, RunMode.HEADLESS, detail)
        if result.get("is_error") or result.get("subtype", "success") != "success":
            raise self._failure(events, result, proc.stderr)
        raw = result.get("usage") or {}
        final_usage = next(
            ((e.get("message") or {}).get("usage") for e in reversed(events)
             if e.get("type") == "assistant" and (e.get("message") or {}).get("usage")),
            None,
        )
        session_id = result.get("session_id") or self._session_id
        if session_id:
            self._session_id = session_id
        usage = {
            "context_tokens": None if final_usage is None else sum(
                int(final_usage.get(key) or 0)
                for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            ),
            "input_tokens": raw.get("input_tokens", 0),
            "output_tokens": raw.get("output_tokens", 0),
            "cache_creation_input_tokens": raw.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": raw.get("cache_read_input_tokens", 0),
            "session_id": session_id,
            "model": self._config.model,
            # Plan-billed: no marginal dollar cost (the CLI's own total_cost_usd is
            # an API-price estimate, not what a subscription is charged).
            "cost_usd": None,
        }
        return str(result.get("result") or ""), usage

    @staticmethod
    def _failure(events: list[dict], result: dict, stderr: str) -> Exception:
        text = str(result.get("result") or "")
        if "No conversation found" in text or "No conversation found" in stderr:
            return ResumeLostError(text or stderr)
        code = next(
            (
                e["error"]
                for e in reversed(events)
                if e.get("type") == "assistant" and isinstance(e.get("error"), str)
            ),
            None,
        )
        kind = _ERROR_CODE_KIND.get(code or "") or classify_failure_text(f"{text}\n{stderr}")
        kind = unless_work_done(kind, _did_work(events))
        detail = tail(text or stderr) or (code or "unknown error")
        return HarnessError(kind, HARNESS, RunMode.HEADLESS, detail)


def _did_work(events: list[dict]) -> bool:
    """True once the turn issued a tool call (``tool_use`` in an assistant message)."""
    for e in events:
        if e.get("type") != "assistant":
            continue
        content = (e.get("message") or {}).get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content
        ):
            return True
    return False


class ResumeLostError(Exception):
    """``--resume`` named a session Claude Code does not have."""
