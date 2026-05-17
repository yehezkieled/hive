"""Persistent PTY session for Claude Code — plan-billed interactive harness."""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import re
from pathlib import Path
from threading import Thread

from ptyprocess import PtyProcess

_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

logger = logging.getLogger(__name__)

# Terminal dimensions: match cortexos (200 cols × 50 rows)
_PTY_COLS = 200
_PTY_ROWS = 50

# Bracketed paste delimiters
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
_CHUNK_SIZE = 4096

# Trust prompt text Claude Code shows on first launch (verified 2026-05-16)
_TRUST_PROMPT = "trust this folder"

# OSC title bar signals emitted by Claude Code:
#   Idle:    \x1b]0;✳ <dir>\x07  — ✳ U+2733 (\xe2\x9c\xb3)
_IDLE_TITLE = b"\x1b]0;\xe2\x9c\xb3"
_IDLE_TITLE_STR = _IDLE_TITLE.decode("utf-8")

# Seconds of quiet (no new bytes) after the ❯ input prompt appears before
# _handle_trust_prompt declares startup complete. 1.5s is empirically needed
# to outlast the welcome banner's full render; override in tests via patch.
_STARTUP_QUIET_S = 1.5

# Strips ANSI/VT100 escape sequences (OSC first, then CSI, then simple Fe).
# OSC must come before the generic [@-Z\\-_] alternative because ] (0x5D) falls
# in the \\-_ range; if the generic branch wins, only \x1b] is consumed and the
# rest of the OSC payload is left in the output.
_ANSI_ESCAPE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])"
)


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
    if permission_mode in DANGEROUS_MODES or permission_mode == "bypassPermissions":
        # bypassPermissions bypasses tool-permission prompts but NOT the first-run
        # trust dialog; --dangerously-skip-permissions skips both.
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
        self._buf: bytearray = bytearray()
        self._closed: bool = False
        self._reader_task: asyncio.Task | None = None
        self._advisor_original: str | None = None  # set only if we removed it
        self._inject_offset: int = 0  # buf offset just before \r (Enter) is sent
        self._atexit_registered: bool = False

    async def start(self) -> None:
        """Spawn Claude Code in a PTY and handle the initial trust prompt."""
        self._suppress_advisor()
        if not self._atexit_registered:
            atexit.register(self._restore_advisor)
            self._atexit_registered = True
        try:
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
            self._buf = bytearray()
            self._closed = False
            self._reader_task = asyncio.create_task(self._reader())
            await self._handle_trust_prompt()
        except Exception:
            self._restore_advisor()
            raise

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
        self._restore_advisor()

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

    def _suppress_advisor(self) -> None:
        """Remove advisorModel from ~/.claude/settings.json before spawning.

        The Advisor Tool invokes Opus before every response, adding >90s latency
        per turn. We snapshot the original value and restore it exactly on stop().
        This is a global mutation — safe for single-session use.
        """
        if not _SETTINGS_PATH.exists():
            return
        try:
            settings = json.loads(_SETTINGS_PATH.read_text())
            if "advisorModel" in settings:
                self._advisor_original = settings.pop("advisorModel")
                _SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
                logger.debug("PtySession: advisorModel suppressed for session")
        except (OSError, json.JSONDecodeError):
            logger.warning("PtySession: could not suppress advisorModel", exc_info=True)

    def _restore_advisor(self) -> None:
        """Restore the original advisorModel value to ~/.claude/settings.json."""
        if self._advisor_original is None:
            return
        try:
            settings = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
            settings["advisorModel"] = self._advisor_original
            _SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
            self._advisor_original = None
            logger.debug("PtySession: advisorModel restored")
        except (OSError, json.JSONDecodeError):
            logger.warning("PtySession: could not restore advisorModel", exc_info=True)

    async def _reader(self) -> None:
        """Dispatch blocking PTY reads to a daemon thread; copy bytes into _buf.

        A daemon thread means Python can exit even if proc.read() is still
        blocking — e.g. when the event loop shuts down after a TimeoutError
        before stop() has had a chance to terminate the subprocess.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def _sync_read() -> None:
            while True:
                try:
                    chunk = self._proc.read(1024)
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except (EOFError, OSError):
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                    return

        thread = Thread(target=_sync_read, daemon=True, name="pty-reader")
        thread.start()

        while True:
            chunk = await queue.get()
            if chunk is None:
                self._closed = True
                return
            self._buf.extend(chunk)

    async def _handle_trust_prompt(self) -> None:
        """Auto-accept trust dialogue and drain until idle (❯) — 60s window.

        --dangerously-skip-permissions skips the trust prompt entirely; this path
        just waits for ❯ in the welcome banner. When the trust prompt does appear
        (non-default permission modes), we send \\r to accept, then wait for ❯ in
        bytes that arrive AFTER acceptance — so the trust-dialog's own ❯ can't
        trigger a premature idle signal.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 60.0
        trust_accepted = False
        # Snapshot buf length at the moment trust is accepted; only count ❯ from
        # bytes that arrive after this point so the trust dialog's ❯ is excluded.
        trust_accepted_buf_len = 0
        glyph_found = False
        prev_buf_len = 0
        quiet_since: float | None = None

        while loop.time() < deadline:
            text = self._buf.decode("utf-8", errors="replace")
            if not trust_accepted and _TRUST_PROMPT in text and self._proc:
                self._proc.write(b"\r")
                trust_accepted = True
                trust_accepted_buf_len = len(self._buf)
                glyph_found = False
                quiet_since = None
                prev_buf_len = 0
            post_trust = self._buf[trust_accepted_buf_len:].decode("utf-8", errors="replace")
            if "❯" in post_trust:
                glyph_found = True
            if glyph_found:
                cur_len = len(self._buf)
                if cur_len == prev_buf_len:
                    if quiet_since is None:
                        quiet_since = loop.time()
                    elif loop.time() - quiet_since >= _STARTUP_QUIET_S:
                        # ❯ present + terminal quiet for _STARTUP_QUIET_S → truly idle.
                        # 300ms was too short: Claude Code's welcome banner can pause
                        # mid-render (emitting ❯) then continue for > 300ms more.
                        # Do NOT trim _buf here: Claude Code redraws the input
                        # field using cursor-position moves (no new ❯ bytes),
                        # so the startup ❯ bytes must stay in _buf for
                        # _inject to snapshot them as the inject_offset.
                        return
                else:
                    quiet_since = None
                prev_buf_len = cur_len
            if self._closed:
                self._buf.clear()
                return
            await asyncio.sleep(0.05)

        logger.warning("PtySession: trust/startup wait timed out after 60s")

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
        # Wait for the paste-triggered screen repaint before snapshotting.
        # Claude Code repaints the terminal (including a ✳ idle title) when it
        # receives pasted text. By sampling _buf AFTER that repaint settles,
        # _read_loop sees only bytes produced after Enter triggers actual processing.
        await asyncio.sleep(0.5)
        self._inject_offset = len(self._buf)
        self._proc.write(b"\r")

    async def _read_until_idle(self, timeout: float = 180.0) -> str:
        """Read PTY output until Claude Code's idle prompt glyph appears."""
        try:
            return await asyncio.wait_for(self._read_loop(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Claude did not become idle within {timeout}s")

    async def _read_loop(self) -> str:
        """Poll post-inject slice of _buf until idle-title appears and is stable.

        _inject_offset is snapshotted AFTER the paste-triggered repaint and
        BEFORE \\r, so the slice contains only post-Enter output. ✳ + 1s quiet
        is the completion signal.
        """
        loop = asyncio.get_event_loop()
        idle_since: float | None = None
        prev_len = 0
        offset = self._inject_offset

        while True:
            slice_bytes = bytes(self._buf[offset:])
            cur_len = len(slice_bytes)

            has_idle = _IDLE_TITLE in slice_bytes

            if has_idle:
                if cur_len == prev_len:
                    if idle_since is None:
                        idle_since = loop.time()
                    elif loop.time() - idle_since >= 1.0:
                        self._buf.clear()
                        text = slice_bytes.decode("utf-8", errors="replace")
                        clean = _ANSI_ESCAPE.sub("", text)
                        lines = clean.splitlines()
                        content_lines = [ln for ln in lines if _IDLE_TITLE_STR not in ln]
                        return "\n".join(content_lines).strip()
                else:
                    idle_since = None

            prev_len = cur_len

            if self._closed:
                self._buf.clear()
                text = slice_bytes.decode("utf-8", errors="replace")
                return _ANSI_ESCAPE.sub("", text).strip()

            await asyncio.sleep(0.1)
