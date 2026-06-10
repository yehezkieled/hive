"""Claude Code adapter — drives one persistent PTY session per entity.

Ticket 007 removed the headless ``claude -p`` subprocess path; the PTY session
is now the only runtime. The adapter builds the append-system-prompts and extra
CLI args for the PTY, forwards the interactive-gate bridge, and turns each
``PtySession.send`` into a uniform ``(text, usage)`` turn result.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hive.runtime.base import Runtime
from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.pty_session import PtySession

logger = logging.getLogger(__name__)


@dataclass
class ClaudeAdapterConfig:
    """All Claude-specific settings needed to build a claude PTY invocation."""

    model: str = "sonnet"
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"
    loop_mode: str = "ralph"
    role: str = "worker"
    name: str = ""
    mcp_config_path: Path | None = None


class ClaudeAdapter(Runtime):
    """Implements Runtime for Claude Code via a persistent PTY session."""

    def __init__(
        self,
        config: ClaudeAdapterConfig,
        cwd: Path | None = None,
        gate_coordinator: GateCoordinator | None = None,
        entity_name: str | None = None,
        gate_approver: str = "user",
        on_gate_state: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._cwd = cwd
        self._pty: PtySession | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        # Interactive-gate bridge (Ticket 003): forwarded to PtySession so the
        # transcript reader detects gates and send() parks-and-injects.
        self._gate_coordinator = gate_coordinator
        self._entity_name = entity_name
        self._gate_approver = gate_approver
        self._on_gate_state = on_gate_state

    def _build_pty_system_prompts(self) -> list[str]:
        cfg = self._config
        prompts: list[str] = []
        if cfg.system_prompt:
            prompts.append(cfg.system_prompt)
        identity_lines = [
            f"You are {cfg.name}. Your role is {cfg.role}.",
            "If a hive_action is denied or fails, report the failure honestly. "
            "Do not narrate fictional success.",
        ]
        prompts.append("\n".join(identity_lines))
        from hive.process.loops import LOOP_PROMPTS, load_role_jd

        loop_text = LOOP_PROMPTS.get(cfg.loop_mode)
        if loop_text:
            prompts.append(loop_text)
        if cfg.role in ("maestro", "lead", "worker"):
            prompts.append(load_role_jd(cfg.role))
        return prompts

    def _build_pty_extra_args(self) -> list[str]:
        cfg = self._config
        args: list[str] = []
        if cfg.allowed_tools:
            args.extend(["--allowedTools", *cfg.allowed_tools])
        if cfg.disallowed_tools:
            args.extend(["--disallowedTools", *cfg.disallowed_tools])
        if cfg.mcp_config_path is not None:
            # --strict-mcp-config: load only this file, not the user's global
            # MCP servers (those add minutes of cold-start latency per spawn).
            args.extend(["--mcp-config", str(cfg.mcp_config_path)])
            args.append("--strict-mcp-config")
        return args

    async def start(self) -> None:
        cfg = self._config
        self._pty = PtySession(
            model=cfg.model,
            cwd=self._cwd,
            append_system_prompts=self._build_pty_system_prompts(),
            extra_args=self._build_pty_extra_args(),
            permission_mode=cfg.permission_mode,
            gate_coordinator=self._gate_coordinator,
            entity_name=self._entity_name,
            gate_approver=self._gate_approver,
            on_gate_state=self._on_gate_state,
        )
        await self._pty.start()

    async def stop(self) -> None:
        if self._pty is not None:
            await self._pty.stop()
            self._pty = None

    def is_alive(self) -> bool:
        return self._pty is not None and self._pty.is_alive()

    def is_busy(self) -> bool:
        """True while a turn is in flight (``send_turn`` holds the lock).

        The idle reaper checks this so an entity mid-turn — e.g. a lead
        blocked on a Workflow sync-wait (ADR 0010) — is never killed on a
        stale ``last_activity_at``, which only updates at turn start.
        """
        return self._lock.locked()

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        async with self._lock:
            assert self._pty is not None, "PtySession not started — call start() first"
            # PtySession.send returns (text, usage) — usage sourced from the
            # session .jsonl transcript, not the scraped screen. Pass the token
            # counts through and add cost_usd=None (plan-billed: no marginal
            # dollar cost).
            text, raw_usage = await self._pty.send(prompt)
            usage: dict = {
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
                "cache_creation_input_tokens": raw_usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": raw_usage.get("cache_read_input_tokens", 0),
                "session_id": raw_usage.get("session_id"),
                "cost_usd": None,
            }
            return text, usage
