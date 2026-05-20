"""Persistent PTY session for Claude Code — plan-billed interactive harness."""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
from pathlib import Path
from threading import Thread

from ptyprocess import PtyProcess

from hive.runtime.transcript_reader import TranscriptReader

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

# Seconds of quiet (no new bytes) after the ❯ input prompt appears before
# _handle_trust_prompt declares startup complete. 1.5s is empirically needed
# to outlast the welcome banner's full render; override in tests via patch.
_STARTUP_QUIET_S = 1.5


def _claude_projects_dir(cwd: Path) -> Path:
    """Return the ~/.claude/projects/<cwd-slug>/ path for a given working directory.

    Claude Code's slug rule: replace BOTH ``/`` and ``.`` with ``-`` in the cwd
    path. The dot replacement matters whenever the cwd traverses a hidden dir
    (e.g. ``/home/x/repo/.claude/worktrees/foo`` → ``-home-x-repo--claude-...``,
    note the double-dash because ``/.`` becomes ``--``). Skipping the dot
    replacement (the original bug) silently mis-locates the transcript and
    breaks the transcript route for any cwd containing a ``.``.
    """
    slug = str(cwd).replace("/", "-").replace(".", "-")
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
    """Spawns Claude Code in a persistent PTY; drives turns; reads results from .jsonl.

    This is the billing-fix layer: interactive PTY sessions stay on the Claude
    Max plan (`claude -p` subprocess-per-turn would be API-billed after
    2026-06-15).

    Response text and per-turn usage come from the session .jsonl transcript
    (via TranscriptReader), not from screen-scraping the TUI. The PTY exists
    only to drive the turn — spawn interactive `claude`, let it process input,
    detect startup. The byte-reader thread populates _buf for trust-prompt and
    idle detection during startup; after startup the buffer is unused.
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
        self._atexit_registered: bool = False
        # Transcript-as-source-of-truth: snapshot project_dir's *.jsonl BEFORE
        # spawn, identify this session's file on first send(), then read every
        # turn's response + usage from there. Replaces screen-scraping.
        self._project_dir: Path | None = None
        self._before_sizes: dict[Path, int] = {}
        self._transcript_reader: TranscriptReader | None = None
        self._session_path: Path | None = None

    async def start(self) -> None:
        """Spawn Claude Code in a PTY and handle the initial trust prompt."""
        self._suppress_advisor()
        if not self._atexit_registered:
            atexit.register(self._restore_advisor)
            self._atexit_registered = True
        try:
            cwd = Path(self._cwd) if self._cwd else None
            # Snapshot the project-dir *.jsonl set BEFORE spawning so the
            # TranscriptReader can identify this session's file (a brand-new
            # one or a --continue'd one that grows past its snapshot size).
            effective_cwd = cwd if cwd is not None else Path(os.getcwd())
            self._project_dir = _claude_projects_dir(effective_cwd)
            self._before_sizes = {}
            if self._project_dir.is_dir():
                for p in self._project_dir.glob("*.jsonl"):
                    try:
                        self._before_sizes[p] = p.stat().st_size
                    except OSError:
                        continue
            self._transcript_reader = TranscriptReader(self._project_dir)
            self._session_path = None  # identified lazily on first send()

            args = _build_spawn_args(
                self._model,
                cwd,
                self._append_system_prompts,
                self._extra_args,
                self._permission_mode,
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

    async def send(self, prompt: str) -> tuple[str, dict]:
        """Inject prompt; return (response_text, usage) from the .jsonl transcript.

        Claude Code's interactive TUI can't emit structured output to stdout
        (``--output-format`` is ``--print``-only), but it writes every turn to
        the session .jsonl as clean structured JSON. The PTY drives the turn
        (inject + Claude processes it); the transcript is the source of truth
        for the response text and token usage.

        Returns ``(response_text, usage)`` where ``usage`` carries the 5 keys
        defined by ``TranscriptReader.await_next_assistant_turn``:
        ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``,
        ``cache_read_input_tokens``, ``session_id``.
        """
        if self._proc is None:
            raise RuntimeError("PtySession not started — call start() first.")
        if not self.is_alive():
            await self._recover()
        assert self._transcript_reader is not None, (
            "PtySession not properly started — _transcript_reader is None"
        )

        await self._inject(prompt)

        # Identify this session's .jsonl lazily — Claude Code only creates the
        # file once it has user input to log. After _inject the file either
        # appears (fresh session) or the --continue'd file has grown.
        if self._session_path is None:
            self._session_path = await asyncio.to_thread(
                self._transcript_reader.identify_session,
                self._before_sizes,
                timeout=10.0,
            )

        return await self._transcript_reader.await_next_assistant_turn(
            self._session_path, timeout=180.0
        )

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
        # Wait for the paste-triggered screen repaint to settle before sending
        # Enter — Claude Code repaints (incl. a ✳ idle title) on receiving the
        # paste, and Enter pressed mid-repaint can be eaten by the paste handler.
        await asyncio.sleep(0.5)
        self._proc.write(b"\r")
