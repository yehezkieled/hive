"""Reads response text + usage from Claude Code session .jsonl transcripts.

Replaces screen-scraping as the source of truth for PTY-mode turns. The reader
points at ~/.claude/projects/<cwd-slug>/ (the project_dir), identifies which
*.jsonl file belongs to a freshly-spawned session, and then polls that file
for completed assistant turns.

Real .jsonl shape (verified against prod files):
  - Top-level key is "type" (NOT "role").
  - Each entry has a top-level "sessionId".
  - Assistant entries have message.content as a LIST of blocks; the text
    payload lives in blocks with type == "text".
  - Usage lives at message.usage (NOT top-level).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from hive.runtime.gates import Gate, GateDetector, resolved_tool_use_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gated:
    """Third outcome of ``await_next_assistant_turn``: the Turn parked on a gate.

    Returned instead of ``(text, usage)`` when the transcript shows an
    unanswered interactive gate. PtySession holds the Turn open and resolves
    the gate rather than treating this as a completed turn or a timeout.
    """

    gate: Gate


class TranscriptReader:
    """Reads response text + usage from Claude Code session .jsonl transcripts."""

    def __init__(
        self,
        project_dir: Path,
        gate_detector: GateDetector | None = None,
    ) -> None:
        """project_dir = ~/.claude/projects/<cwd-slug>/ for this entity.

        gate_detector: optional. When supplied, ``await_next_assistant_turn``
        also watches for an unanswered interactive gate and returns ``Gated``
        instead of hanging to the timeout. Off by default so callers that only
        want completed turns keep the original two-outcome contract.
        """
        self._project_dir = project_dir
        self._gate_detector = gate_detector

    def resolve_session(
        self,
        session_id: str | None,
        before_sizes: dict[Path, int],
        *,
        timeout: float = 10.0,
    ) -> Path:
        """Resolve this session's transcript path — pinned by session id (ADR 0011).

        When the caller knows the harness process's sessionId (from Claude
        Code's per-process state file ``~/.claude/sessions/<pid>.json``), the
        transcript is ``<project_dir>/<session_id>.jsonl`` exactly — no
        directory scanning, immune to sibling sessions growing files in a
        shared project dir (failure F3, ticket 023). The file may not exist
        yet (Claude Code creates it lazily on first input); that's fine —
        ``await_next_assistant_turn`` polls until it appears.

        With no session id, falls back to the new-or-growing heuristic
        (``identify_session``), which can mis-bind in a shared dir.
        """
        if session_id:
            return self._project_dir / f"{session_id}.jsonl"
        logger.warning(
            "session pin unavailable, falling back to directory heuristic in %s "
            "— in a shared project dir this bind can silently mis-attribute "
            "turns (F3, ticket 023)",
            self._project_dir,
        )
        return self.identify_session(before_sizes, timeout=timeout)

    def identify_session(
        self,
        before_sizes: dict[Path, int],
        *,
        timeout: float = 10.0,
    ) -> Path:
        """FALLBACK ONLY — find the .jsonl by comparing against the pre-spawn snapshot.

        Prefer ``resolve_session`` with a pinned session id (ADR 0011): in a
        project dir shared by several sessions this heuristic can bind to a
        sibling's growing transcript, silently (failure F3, ticket 023).

        before_sizes: {path: size_in_bytes} for every *.jsonl present in
            project_dir at snapshot time (just before spawning the harness).

        Polls project_dir every ~100ms until either:
          - a *.jsonl file appears that wasn't in before_sizes (fresh session), or
          - a file from before_sizes has grown vs its snapshot size (--continue).

        Returns the matching path. Raises TimeoutError if neither happens within
        `timeout` seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            for path in self._project_dir.glob("*.jsonl"):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                prior = before_sizes.get(path)
                if prior is None:
                    # Brand-new file → fresh session.
                    return path
                if size > prior:
                    # Existing file grew → --continue resumed it.
                    return path
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"No new or growing *.jsonl in {self._project_dir} within {timeout}s"
                )
            time.sleep(0.1)

    async def await_next_assistant_turn(
        self,
        session_path: Path,
        *,
        timeout: float = 180.0,
        quiescence_ms: int = 500,
    ) -> tuple[str, dict] | Gated:
        """Poll session_path until a completed assistant turn is written.

        Returns (response_text, usage):
          response_text: text of the LAST {"type":"text"} block in the LAST
                         {"type":"assistant"} entry currently in the file.
          usage: {
              "input_tokens": int,
              "output_tokens": int,
              "cache_creation_input_tokens": int,
              "cache_read_input_tokens": int,
              "session_id": str | None,   # from top-level sessionId
          }

        A turn is considered complete when an assistant entry exists in the
        file AND the file's mtime has been stable for `quiescence_ms`
        milliseconds (no in-progress writes) AND the last assistant entry has
        no unresolved ``tool_use`` (a pending tool means the entry is an
        intermediate step, not the Turn's answer — ADR 0010).

        Third outcome: if a gate_detector was supplied and the (quiescent)
        transcript shows an unanswered interactive gate, returns ``Gated(gate)``
        instead — the PTY is frozen on a TUI menu and no further assistant
        entry will arrive. Without a detector this never happens.

        `timeout` is a NO-PROGRESS deadline, not a wall clock: it restarts
        whenever the transcript's mtime advances or a tool call is pending
        (an in-flight tool counts as progress, however long it runs). Raises
        TimeoutError after `timeout` seconds with neither.
        """
        deadline = time.monotonic() + timeout
        quiescence_seconds = quiescence_ms / 1000.0
        poll_interval = min(0.05, quiescence_seconds / 2.0)

        # Snapshot of how many assistant entries existed at call start.
        # In production this is normally 0 (we await right after sending a
        # prompt), but tests may pre-seed the file.
        initial_count = self._count_assistant_entries(session_path)
        last_mtime = self._stat_mtime(session_path)

        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"No completed assistant turn in {session_path} within {timeout}s"
                )

            mtime = self._stat_mtime(session_path)
            if mtime is not None and mtime != last_mtime:
                # Transcript moved — progress. Restart the no-progress window.
                last_mtime = mtime
                deadline = now + timeout

            current_count = self._count_assistant_entries(session_path)

            # Strict acceptance: only return when a NEW assistant entry has appeared
            # since call start. Production-safe — on the 2nd turn of any session
            # (and the 1st turn of any --continue session) the file already holds
            # prior assistant entries, and a lax "any entry exists" gate would
            # return stale data.
            if current_count > initial_count and mtime is not None:
                # Check quiescence: mtime must not have changed for quiescence_seconds.
                if (time.time() - mtime) >= quiescence_seconds:
                    # A frozen gate also writes an assistant entry then goes
                    # quiet (the PTY sits on a menu). Check for an unanswered
                    # gate FIRST so it isn't mistaken for a completed turn —
                    # a gate IS a pending tool_use (ExitPlanMode /
                    # AskUserQuestion), so if the pending-tool guard below ran
                    # first, gates would never surface.
                    entries = self._read_entries(session_path)
                    gate = self._detect_gate(entries)
                    if gate is not None:
                        return Gated(gate)
                    # Pending-tool guard (ADR 0010): a long tool call (e.g. a
                    # Workflow sync-wait) sits quiet with an unresolved
                    # tool_use in the last assistant entry. That entry is
                    # intermediate, not the Turn's answer — keep polling. The
                    # in-flight tool also counts as progress, so the
                    # no-progress deadline resets.
                    if self._has_pending_tool_use(entries):
                        deadline = now + timeout
                        await asyncio.sleep(poll_interval)
                        continue
                    return self._extract_last_turn(session_path)

            await asyncio.sleep(poll_interval)

    @staticmethod
    def _stat_mtime(session_path: Path) -> float | None:
        """The file's mtime, or None if it can't be statted right now."""
        try:
            return session_path.stat().st_mtime
        except OSError:
            return None

    def _detect_gate(self, entries: list[dict]) -> Gate | None:
        """Run the gate detector over the parsed transcript, if one is set."""
        if self._gate_detector is None:
            return None
        return self._gate_detector.detect(entries)

    @staticmethod
    def _has_pending_tool_use(entries: list[dict]) -> bool:
        """True when the LAST assistant entry holds an unresolved ``tool_use``.

        Unresolved = a ``tool_use`` block whose id has no matching
        ``tool_result`` anywhere in the file (the GateDetector pairing). The
        tool is still in flight, so the entry is intermediate — not the Turn's
        final answer.
        """
        last_assistant: dict | None = None
        for entry in entries:
            if entry.get("type") == "assistant":
                last_assistant = entry
        if last_assistant is None:
            return False

        content = (last_assistant.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            return False
        resolved = resolved_tool_use_ids(entries)
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if block.get("id") not in resolved:
                    return True
        return False

    @staticmethod
    def _read_entries(session_path: Path) -> list[dict]:
        """Parse every JSON line in the transcript into a list of dicts."""
        try:
            text = session_path.read_text(encoding="utf-8")
        except OSError:
            return []
        entries: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _count_assistant_entries(session_path: Path) -> int:
        try:
            text = session_path.read_text(encoding="utf-8")
        except OSError:
            return 0
        count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "assistant":
                count += 1
        return count

    @staticmethod
    def _extract_last_turn(session_path: Path) -> tuple[str, dict]:
        """Extract (text, usage) from the LAST assistant entry in the file.

        text = the last {"type":"text"} block in that entry.
        usage = the 5-field dict (4 token counts + session_id).
        """
        text = session_path.read_text(encoding="utf-8")
        last_assistant: dict | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "assistant":
                last_assistant = entry

        if last_assistant is None:
            raise RuntimeError(f"No assistant entry found in {session_path} after acceptance")

        message = last_assistant.get("message") or {}
        content = message.get("content") or []

        # Pick the LAST text block.
        response_text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                response_text = block.get("text", "")

        raw_usage = message.get("usage") or {}
        usage = {
            "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
            "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                raw_usage.get("cache_creation_input_tokens", 0) or 0
            ),
            "cache_read_input_tokens": int(raw_usage.get("cache_read_input_tokens", 0) or 0),
            "session_id": last_assistant.get("sessionId"),
        }
        return response_text, usage
