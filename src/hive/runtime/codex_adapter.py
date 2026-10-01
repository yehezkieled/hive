"""Codex adapter — drives the Codex CLI one ``codex exec`` process per turn.

Unlike the Claude Code adapter's persistent PTY, Codex is exec-per-turn (ADR
0001): each turn spawns ``codex exec --json`` (``codex exec resume <thread>``
once a thread exists), reads the JSONL event stream to its ``turn.completed``
or ``turn.failed`` event, and returns ``(text, usage)``. Continuity is the
Codex *thread id*, handed back as ``usage["session_id"]`` so Hive persists it
exactly like a Claude session id and a restart resumes the same conversation.

Codex has no append-system-prompt flag, so the entity's system prompts are
prefixed to the first prompt of a fresh thread; a resumed thread already holds
them in its history.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from hive.config import CODEX_BINARY, CODEX_TURN_TIMEOUT_S
from hive.models.entity import DANGEROUS_MODES
from hive.runtime.base import Runtime
from hive.runtime.system_prompt import build_system_prompts
from hive.runtime.workflow_progress import WorkflowProgress

logger = logging.getLogger(__name__)


@dataclass
class CodexAdapterConfig:
    """All Codex-specific settings needed to build a ``codex exec`` invocation."""

    model: str = "gpt-5.5"
    system_prompt: str = ""
    permission_mode: str = "default"
    role: str = "lead"
    name: str = ""
    is_pa: bool = False
    # Codex thread id to resume (the entity's persisted session_id), or None.
    session_id: str | None = None


class CodexError(RuntimeError):
    """The Codex CLI reported a failed turn (usage limit, auth, model error)."""


class CodexAdapter(Runtime):
    """Implements Runtime for the Codex CLI, one subprocess per turn."""

    def __init__(self, config: CodexAdapterConfig, cwd: Path | None = None) -> None:
        self._config = config
        self._cwd = cwd
        self._started = False
        self._thread_id: str | None = config.session_id
        self._proc: asyncio.subprocess.Process | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # -- argv ---------------------------------------------------------------

    def _build_argv(self) -> list[str]:
        cfg = self._config
        argv = [CODEX_BINARY, "exec"]
        if self._thread_id:
            argv.extend(["resume", self._thread_id])
        argv.extend(["--json", "--skip-git-repo-check", "--model", cfg.model])
        if cfg.permission_mode in DANGEROUS_MODES:
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            # `exec` never prompts; a non-bypass mode stays inside the sandbox.
            argv.extend(["-c", 'sandbox_mode="workspace-write"'])
        argv.append("-")  # prompt on stdin
        return argv

    def _first_turn_prompt(self, prompt: str) -> str:
        cfg = self._config
        blocks = build_system_prompts(
            name=cfg.name, role=cfg.role, system_prompt=cfg.system_prompt, is_pa=cfg.is_pa
        )
        return "\n\n".join([*blocks, prompt])

    # -- Runtime ------------------------------------------------------------

    async def start(self) -> None:
        if shutil.which(CODEX_BINARY) is None:
            raise FileNotFoundError(f"codex binary {CODEX_BINARY!r} not found on PATH")
        self._started = True

    async def stop(self) -> None:
        self._started = False
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        self._proc = None

    def is_alive(self) -> bool:
        return self._started

    def is_busy(self) -> bool:
        """True while a turn is in flight (``send_turn`` holds the lock)."""
        return self._lock.locked()

    # Codex has no Workflow tool, so the Workflow-run probes are inert; they
    # exist so the manager's liveness/bounce paths treat this like any adapter.
    def poll_workflow_progress(self) -> list[WorkflowProgress]:
        return []

    def workflow_active(self, window: float) -> bool:
        return False

    def describe_jam(self) -> dict | None:
        return None

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        async with self._lock:
            if not self._started:
                raise RuntimeError("CodexAdapter not started — call start() first")
            if not self._thread_id:
                prompt = self._first_turn_prompt(prompt)
            proc = await asyncio.create_subprocess_exec(
                *self._build_argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd) if self._cwd else None,
            )
            self._proc = proc
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(prompt.encode()), timeout=CODEX_TURN_TIMEOUT_S
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            finally:
                self._proc = None
            text, usage, thread_id, error = parse_events(stdout.decode(errors="replace"))
            if thread_id:
                self._thread_id = thread_id
            if error is not None:
                raise CodexError(error)
            if proc.returncode != 0 and not text:
                tail = stderr.decode(errors="replace").strip()[-500:]
                raise CodexError(f"codex exited {proc.returncode}: {tail}")
            usage["session_id"] = self._thread_id
            return text, usage


def parse_events(stdout: str) -> tuple[str, dict, str | None, str | None]:
    """Reduce a ``codex exec --json`` JSONL stream to a turn result.

    Returns ``(final_text, usage, thread_id, error)``. ``final_text`` is the
    last ``agent_message`` item; ``error`` is set by a ``turn.failed`` (or bare
    ``error``) event. Unparseable lines are skipped — the stream is advisory
    chatter around the events we read.
    """
    text = ""
    thread_id: str | None = None
    error: str | None = None
    usage: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost_usd": None,
    }
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                text = item["text"]
        elif kind == "turn.completed":
            raw = event.get("usage") or {}
            usage["input_tokens"] = raw.get("input_tokens", 0)
            usage["output_tokens"] = raw.get("output_tokens", 0)
            usage["cache_read_input_tokens"] = raw.get("cached_input_tokens", 0)
        elif kind == "turn.failed":
            error = (event.get("error") or {}).get("message") or "codex turn failed"
        elif kind == "error" and error is None:
            error = event.get("message") or "codex error"
    return text, usage, thread_id, error
