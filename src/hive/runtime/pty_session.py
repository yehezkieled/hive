"""Persistent PTY session for Claude Code — plan-billed interactive harness."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from threading import Thread

from ptyprocess import PtyProcess

from hive.config import CLAUDE_BINARY
from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.gates import GateDetector
from hive.runtime.transcript_reader import Gated, TranscriptReader

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


# Session pinning (ADR 0011): how long to poll ~/.claude/sessions/<pid>.json
# for the sessionId after spawn, and at what cadence. The file can lag the
# spawn by a moment; past the timeout the reader falls back to the directory
# heuristic (loudly).
_PIN_POLL_TIMEOUT_S = 10.0
_PIN_POLL_INTERVAL_S = 0.1


def _claude_sessions_dir() -> Path:
    """Default location of Claude Code's per-process session-state files.

    ``~/.claude/sessions/<pid>.json`` carries ``{pid, sessionId, cwd, ...}``
    for every live ``claude`` process (verified on the fleet's pinned binary,
    ADR 0011). Undocumented interface — accepted with a fallback and the
    version pin from Ticket 009.
    """
    return Path.home() / ".claude" / "sessions"


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


_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def _looks_like_version(text: str) -> bool:
    """True if text is a bare semver-ish string like ``2.1.162``."""
    return bool(_VERSION_RE.fullmatch(text))


def _resolve_claude_version(binary: str) -> tuple[str, str]:
    """Return ``(resolved_path, version)`` for the spawned ``claude`` binary.

    Cheap path first: the native installer symlinks ``~/.local/bin/claude`` at a
    ``versions/<X>`` file, so ``realpath`` + ``basename`` yields the version with
    no subprocess. Falls back to ``claude --version`` when the resolved basename
    isn't a recognizable version (e.g. an npm wrapper, or a bare PATH lookup).
    """
    resolved = os.path.realpath(binary)
    version = os.path.basename(resolved)
    if not _looks_like_version(version):
        version = _claude_version_subprocess(binary)
    return resolved, version


def _claude_version_subprocess(binary: str) -> str:
    """Return the version from ``<binary> --version``, or ``"unknown"`` on failure.

    A version probe must never block or crash a spawn: a short timeout bounds it
    and any error (binary missing, slow, garbled output) degrades to ``"unknown"``.
    """
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    match = _VERSION_RE.search(result.stdout)
    return match.group(0) if match else "unknown"


def _build_spawn_args(
    model: str,
    cwd: Path | None,
    append_system_prompts: list[str],
    extra_args: list[str],
    permission_mode: str = "bypassPermissions",
) -> list[str]:
    from hive.models.entity import DANGEROUS_MODES

    args = [CLAUDE_BINARY, "--model", model]
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
        gate_coordinator: GateCoordinator | None = None,
        entity_name: str | None = None,
        gate_approver: str = "user",
        on_gate_state: Callable[[str, str], None] | None = None,
        sessions_dir: Path | None = None,
    ) -> None:
        self._model = model
        # Where Claude Code keeps its per-process session-state files
        # (ADR 0011). Injectable so tests can fake the pid → sessionId pin;
        # production uses the real ~/.claude/sessions.
        self._sessions_dir = sessions_dir if sessions_dir is not None else _claude_sessions_dir()
        self._cwd = str(cwd) if cwd else None
        self._append_system_prompts = append_system_prompts or []
        self._extra_args = extra_args or []
        self._permission_mode = permission_mode
        # Interactive-gate bridge (Ticket 003). When a coordinator is wired,
        # the transcript reader watches for unanswered gates and send() holds
        # the Turn open, resolves the gate, injects the keypress, and resumes.
        # Without it, send() keeps its original two-outcome (text, usage)
        # contract.
        self._gate_coordinator = gate_coordinator
        self._entity_name = entity_name
        self._gate_approver = gate_approver
        self._on_gate_state = on_gate_state
        self._proc: PtyProcess | None = None
        self._buf: bytearray = bytearray()
        self._closed: bool = False
        self._reader_task: asyncio.Task | None = None
        # Transcript-as-source-of-truth: snapshot project_dir's *.jsonl BEFORE
        # spawn, identify this session's file on first send(), then read every
        # turn's response + usage from there. Replaces screen-scraping.
        self._project_dir: Path | None = None
        self._before_sizes: dict[Path, int] = {}
        self._transcript_reader: TranscriptReader | None = None
        self._session_path: Path | None = None

    async def start(self) -> None:
        """Spawn Claude Code in a PTY and handle the initial trust prompt."""
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
        # Wire gate detection only when a coordinator can act on it; this
        # keeps the reader's two-outcome contract for sessions that don't
        # bridge gates.
        gate_detector = GateDetector() if self._gate_coordinator is not None else None
        self._transcript_reader = TranscriptReader(self._project_dir, gate_detector=gate_detector)
        # The pin is per-PROCESS (ADR 0011): clear it on every (re)spawn so
        # the next send() re-resolves against the NEW pid's state file.
        self._session_path = None  # resolved lazily on first send()

        args = _build_spawn_args(
            self._model,
            cwd,
            self._append_system_prompts,
            self._extra_args,
            self._permission_mode,
        )
        logger.info("PtySession: spawning %s", " ".join(args[:5]))
        # Log the resolved binary + version every spawn so version drift
        # between dev and the fleet is visible in the journal (Ticket 009).
        bin_path, version = _resolve_claude_version(CLAUDE_BINARY)
        logger.info(
            "PtySession: %s on claude %s (%s)",
            self._entity_name or "entity",
            version,
            bin_path,
        )
        self._proc = PtyProcess.spawn(
            args,
            cwd=self._cwd,
            dimensions=(_PTY_ROWS, _PTY_COLS),
        )
        self._buf = bytearray()
        self._closed = False
        self._reader_task = asyncio.create_task(self._reader())
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

    @property
    def session_dir(self) -> Path | None:
        """The session's working dir (holds ``workflows/`` and ``subagents/``),
        or ``None`` until the session ``.jsonl`` is pinned on first send.

        Claude Code writes a Workflow run's records under
        ``<session>/workflows/`` and ``<session>/subagents/workflows/`` —
        siblings of the session transcript. Ticket 017's read-only progress
        watcher reads them here, never touching the PTY or its lock.
        """
        if self._session_path is None:
            return None
        return self._session_path.with_suffix("")

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

        # Resolve this session's .jsonl lazily — Claude Code only creates the
        # file once it has user input to log. Preferred path (ADR 0011): pin
        # to the exact sessionId from ~/.claude/sessions/<pid>.json. Fallback:
        # the new-or-growing heuristic over the project dir, which needs
        # _inject to have happened (the file appears / grows on input). The
        # pin is per-process — start() clears _session_path, so a respawn
        # (new pid) re-pins.
        if self._session_path is None:
            pid = getattr(self._proc, "pid", None)
            session_id = await self._read_session_id(pid) if isinstance(pid, int) else None
            self._session_path = await asyncio.to_thread(
                self._transcript_reader.resolve_session,
                session_id,
                self._before_sizes,
                timeout=10.0,
            )

        # Await loop: a normal turn returns (text, usage) on the first pass.
        # If the Turn parks on an interactive gate, the reader returns Gated;
        # we hold the Turn open, resolve the gate, inject the decision, and
        # re-await the SAME Turn. Re-awaiting is how the 180s reader timeout is
        # "suspended" while gated — a fresh 180s window starts after the
        # keypress, not while the user is deciding.
        while True:
            outcome = await self._transcript_reader.await_next_assistant_turn(
                self._session_path, timeout=180.0
            )
            if not isinstance(outcome, Gated):
                return outcome
            await self._handle_gate(outcome)

    async def _read_session_id(self, pid: int) -> str | None:
        """Poll for the sessionId Claude Code records for our child pid (ADR 0011).

        ``<sessions_dir>/<pid>.json`` can lag the spawn by a moment, so poll
        briefly (``_PIN_POLL_TIMEOUT_S`` at ``_PIN_POLL_INTERVAL_S``). A
        missing, malformed, or sessionId-less file counts as "not yet usable"
        and polling continues — a partial write may complete a moment later.
        Returns None past the timeout; the caller then falls back to the
        directory heuristic (which logs its own loud warning at the bind).
        """
        state_path = self._sessions_dir / f"{pid}.json"
        deadline = time.monotonic() + _PIN_POLL_TIMEOUT_S
        while True:
            session_id = self._parse_session_id(state_path)
            if session_id is not None:
                return session_id
            if time.monotonic() >= deadline:
                logger.warning(
                    "PtySession: no usable session-state file at %s within %.1fs — "
                    "session pin unavailable (ADR 0011)",
                    state_path,
                    _PIN_POLL_TIMEOUT_S,
                )
                return None
            await asyncio.sleep(_PIN_POLL_INTERVAL_S)

    @staticmethod
    def _parse_session_id(state_path: Path) -> str | None:
        """The sessionId in a session-state file, or None if unreadable/absent."""
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        session_id = data.get("sessionId")
        if isinstance(session_id, str) and session_id:
            return session_id
        return None

    async def _handle_gate(self, gated: Gated) -> None:
        """Park on the gate, inject the user's decision, resume the Turn.

        Requires a coordinator; the reader only emits Gated when one is wired.
        """
        assert self._gate_coordinator is not None
        entity_name = self._entity_name or "unknown"
        self._set_gate_state(entity_name, "gated")
        keys = await self._gate_coordinator.resolve(
            entity_name, gated.gate, approver=self._gate_approver
        )
        for key in keys:
            await self._inject_keys(key)
        self._set_gate_state(entity_name, "running")

    def _set_gate_state(self, entity_name: str, state: str) -> None:
        """Notify the state hook of a gate transition, if one is registered."""
        if self._on_gate_state is not None:
            self._on_gate_state(entity_name, state)

    async def _inject_keys(self, key: str) -> None:
        """Write a raw control key (e.g. Enter, arrow-down) into the PTY.

        Unlike _inject, this sends the bytes verbatim with no bracketed-paste
        wrapping — these are TUI navigation keystrokes, not pasted text.
        """
        if self._proc is None:
            return
        self._proc.write(key.encode("utf-8"))
        # Small settle so successive keys (e.g. Down, Down, Enter) aren't
        # coalesced or eaten by a mid-keystroke repaint.
        await asyncio.sleep(0.05)

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
