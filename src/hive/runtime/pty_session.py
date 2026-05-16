"""Persistent PTY session for Claude Code — plan-billed interactive harness."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ptyprocess import PtyProcess

logger = logging.getLogger(__name__)

# Terminal dimensions: match cortexos (200 cols × 50 rows)
_PTY_COLS = 200
_PTY_ROWS = 50

# Bracketed paste delimiters
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
_CHUNK_SIZE = 4096

# Pattern that signals Claude Code is idle and waiting for input.
# cortexos: "⚔ ❯ " is the full prompt; ❯ never appears in assistant output.
# Real idle line: "❯ Try ..." — non-breaking space follows, so match ❯ anywhere.
_TURN_COMPLETE = re.compile(r"❯")

# Trust prompt text Claude Code shows on first launch
_TRUST_PROMPT = "Do you trust"


def _claude_projects_dir(cwd: Path) -> Path:
    """Return the ~/.claude/projects/<cwd-slug>/ path for a given working directory."""
    slug = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def _has_prior_session(cwd: Path) -> bool:
    """True if Claude Code has an existing session .jsonl for this working directory."""
    projects_dir = _claude_projects_dir(cwd)
    return projects_dir.is_dir() and any(projects_dir.glob("*.jsonl"))


def _build_spawn_args(
    model: str,
    cwd: Path | None,
    append_system_prompts: list[str],
    extra_args: list[str],
    permission_mode: str = "bypassPermissions",
) -> list[str]:
    from hive.models.entity import DANGEROUS_MODES

    args = ["claude", "--model", model]
    if permission_mode in DANGEROUS_MODES:
        args.append("--dangerously-skip-permissions")
    elif permission_mode not in ("default", ""):
        args.extend(["--permission-mode", permission_mode])
    if cwd and _has_prior_session(cwd):
        args.append("--continue")
    for prompt in append_system_prompts:
        args.extend(["--append-system-prompt", prompt])
    args.extend(extra_args)
    return args


class PtySession:
    """Spawns Claude Code in a persistent PTY, injects turns via bracketed paste.

    This is the billing-fix layer: interactive PTY sessions stay on the Claude
    Max plan; claude -p subprocess-per-turn would be API-billed after 2026-06-15.
    """

    def __init__(
        self,
        model: str = "sonnet",
        cwd: Path | None = None,
        append_system_prompts: list[str] | None = None,
        extra_args: list[str] | None = None,
        permission_mode: str = "bypassPermissions",
    ) -> None:
        self._model = model
        self._cwd = str(cwd) if cwd else None
        self._append_system_prompts = append_system_prompts or []
        self._extra_args = extra_args or []
        self._permission_mode = permission_mode
        self._proc: PtyProcess | None = None

    async def start(self) -> None:
        """Spawn Claude Code in a PTY and handle the initial trust prompt."""
        cwd = Path(self._cwd) if self._cwd else None
        args = _build_spawn_args(
            self._model, cwd, self._append_system_prompts, self._extra_args, self._permission_mode
        )
        logger.info("PtySession: spawning %s", " ".join(args[:5]))
        self._proc = PtyProcess.spawn(
            args,
            cwd=self._cwd,
            dimensions=(_PTY_ROWS, _PTY_COLS),
        )
        await self._handle_trust_prompt()

    async def stop(self) -> None:
        """Send /exit and wait for the process to close."""
        if self._proc is None or not self._proc.isalive():
            return
        try:
            self._proc.write(b"/exit\r\n")
            await asyncio.sleep(0.5)
        except OSError:
            pass
        if self._proc.isalive():
            self._proc.terminate(force=True)

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    async def send(self, prompt: str) -> str:
        """Inject prompt via bracketed paste and return the cleaned response."""
        if self._proc is None:
            raise RuntimeError("PtySession not started — call start() first.")
        if not self.is_alive():
            await self._recover()
        await self._inject(prompt)
        return await self._read_until_idle()

    async def _recover(self) -> None:
        """Attempt to respawn after a crash, with exponential back-off."""
        delays = [2.0, 4.0, 8.0]
        for delay in delays:
            logger.warning("PtySession: proc dead, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
            try:
                await self.start()
                if self.is_alive():
                    logger.info("PtySession: recovered after crash")
                    return
            except Exception:
                logger.exception("PtySession: recovery attempt failed")
        raise RuntimeError("PtySession: failed to recover after 3 attempts")

    async def _handle_trust_prompt(self) -> None:
        """Auto-accept Claude Code's initial trust dialogue within 5 seconds."""
        try:
            output = await asyncio.wait_for(self._read_chunk_containing(_TRUST_PROMPT), timeout=5.0)
            if output and self._proc:
                self._proc.write(b"\r")
                # Drain the welcome banner so the first real send() sees a clean buffer
                try:
                    await asyncio.wait_for(self._read_chunk_containing("❯"), timeout=15.0)
                except TimeoutError:
                    pass
        except TimeoutError:
            pass  # No trust prompt appeared — already trusted or newer Claude version

    async def _inject(self, text: str) -> None:
        """Send text into the PTY using bracketed paste (handles large payloads)."""
        if self._proc is None:
            return
        payload = text.encode("utf-8")
        self._proc.write(_PASTE_START)
        for i in range(0, max(len(payload), 1), _CHUNK_SIZE):
            self._proc.write(payload[i : i + _CHUNK_SIZE])
            if len(payload) > _CHUNK_SIZE:
                await asyncio.sleep(0.05)
        self._proc.write(_PASTE_END)
        await asyncio.sleep(0.3)
        self._proc.write(b"\r")

    async def _read_chunk_containing(self, needle: str) -> str:
        """Read PTY output until needle is seen or EOF."""
        buf = ""
        loop = asyncio.get_event_loop()
        while True:
            try:
                chunk = await loop.run_in_executor(None, self._proc.read, 1024)
                buf += chunk.decode("utf-8", errors="replace")
                if needle in buf:
                    return buf
            except (EOFError, OSError):
                return buf

    async def _read_until_idle(self, timeout: float = 120.0) -> str:
        """Read PTY output until Claude Code's idle prompt glyph appears."""
        try:
            return await asyncio.wait_for(self._read_loop(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Claude did not become idle within {timeout}s")

    async def _read_loop(self) -> str:
        buf = ""
        loop = asyncio.get_event_loop()
        while True:
            try:
                chunk = await loop.run_in_executor(None, self._proc.read, 1024)
                buf += chunk.decode("utf-8", errors="replace")
                if _TURN_COMPLETE.search(buf):
                    lines = buf.splitlines()
                    content_lines = [ln for ln in lines if not _TURN_COMPLETE.search(ln)]
                    return "\n".join(content_lines)
            except (EOFError, OSError):
                return buf
