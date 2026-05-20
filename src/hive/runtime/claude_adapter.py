"""Claude Code adapter: subprocess (step 1) and PTY (step 2) modes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hive.models.entity import DANGEROUS_MODES
from hive.process.claude_session import ClaudeSession
from hive.runtime.base import Runtime
from hive.runtime.pty_session import PtySession

logger = logging.getLogger(__name__)


@dataclass
class ClaudeAdapterConfig:
    """All Claude-specific settings needed to build a claude -p invocation."""

    model: str = "sonnet"
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"
    loop_mode: str = "ralph"
    role: str = "worker"
    name: str = ""
    mcp_config_path: Path | None = None


SessionFactory = Callable[[list[str], Path | None], ClaudeSession]


def _default_session_factory(args: list[str], cwd: Path | None) -> ClaudeSession:
    return ClaudeSession(args=args, cwd=cwd)


class ClaudeAdapter(Runtime):
    """Implements Runtime for Claude Code using claude -p subprocess (step 1).

    Step 2 will swap the internals to PtySession without changing this class's
    public interface or the tests that use session_factory injection.
    """

    def __init__(
        self,
        config: ClaudeAdapterConfig,
        cwd: Path | None = None,
        session_factory: SessionFactory | None = None,
        initial_session_id: str | None = None,
        use_pty: bool = False,
    ) -> None:
        self._config = config
        self._cwd = cwd
        self._session_factory: SessionFactory = session_factory or _default_session_factory
        self._session_id: str | None = initial_session_id
        self._use_pty = use_pty
        self._pty: PtySession | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    def _build_args(self) -> list[str]:
        cfg = self._config
        args = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            cfg.model,
        ]

        if cfg.system_prompt:
            args.extend(["--system-prompt", cfg.system_prompt])

        if cfg.allowed_tools:
            args.extend(["--allowedTools", *cfg.allowed_tools])

        if cfg.disallowed_tools:
            args.extend(["--disallowedTools", *cfg.disallowed_tools])

        if cfg.permission_mode in DANGEROUS_MODES:
            args.append("--dangerously-skip-permissions")
        elif cfg.permission_mode != "default":
            args.extend(["--permission-mode", cfg.permission_mode])

        from hive.process.loops import LOOP_PROMPTS, load_role_jd

        identity_lines = [
            f"You are {cfg.name}. Your role is {cfg.role}.",
            "If a hive_action is denied or fails, report the failure honestly. "
            "Do not narrate fictional success.",
        ]
        args.extend(["--append-system-prompt", "\n".join(identity_lines)])

        loop_text = LOOP_PROMPTS.get(cfg.loop_mode)
        if loop_text:
            args.extend(["--append-system-prompt", loop_text])

        if cfg.role in ("maestro", "lead", "worker"):
            args.extend(["--append-system-prompt", load_role_jd(cfg.role)])

        if cfg.mcp_config_path is not None:
            # --strict-mcp-config: load only this file, not the user's global
            # MCP servers (those add minutes of cold-start latency per spawn).
            args.extend(["--mcp-config", str(cfg.mcp_config_path)])
            args.append("--strict-mcp-config")

        return args

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
        if self._use_pty:
            cfg = self._config
            self._pty = PtySession(
                model=cfg.model,
                cwd=self._cwd,
                append_system_prompts=self._build_pty_system_prompts(),
                extra_args=self._build_pty_extra_args(),
                permission_mode=cfg.permission_mode,
            )
            await self._pty.start()

    async def stop(self) -> None:
        if self._pty is not None:
            await self._pty.stop()
            self._pty = None

    def is_alive(self) -> bool:
        if self._use_pty:
            return self._pty is not None and self._pty.is_alive()
        return True  # subprocess mode: always ready (new process per turn)

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        async with self._lock:
            if self._use_pty:
                return await self._send_via_pty(prompt)
            return await self._send_via_subprocess(prompt)

    async def _send_via_pty(self, prompt: str) -> tuple[str, dict]:
        assert self._pty is not None, "PtySession not started — call start() first"
        # PtySession.send now returns (text, usage) — usage sourced from the
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

    async def _send_via_subprocess(self, prompt: str) -> tuple[str, dict]:
        args = self._build_args()
        if self._session_id:
            args.extend(["--resume", self._session_id])

        session = self._session_factory(args, self._cwd)
        await session.start()
        try:
            text = await session.send_prompt(prompt)
        finally:
            await session.kill()

        if session.session_id:
            self._session_id = session.session_id

        raw = session.last_usage or {}
        usage: dict = {
            "input_tokens": raw.get("input_tokens", 0),
            "output_tokens": raw.get("output_tokens", 0),
            "cache_creation_input_tokens": raw.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": raw.get("cache_read_input_tokens", 0),
            "session_id": self._session_id,
            "cost_usd": raw.get("cost_usd"),
        }
        return text, usage

    @property
    def session_id(self) -> str | None:
        return self._session_id
