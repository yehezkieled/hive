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
from pathlib import Path

logger = logging.getLogger(__name__)


class TranscriptReader:
    """Reads response text + usage from Claude Code session .jsonl transcripts."""

    def __init__(self, project_dir: Path) -> None:
        """project_dir = ~/.claude/projects/<cwd-slug>/ for this entity."""
        self._project_dir = project_dir

    def identify_session(
        self,
        before_sizes: dict[Path, int],
        *,
        timeout: float = 10.0,
    ) -> Path:
        """Find this session's .jsonl by comparing against the pre-spawn snapshot.

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
    ) -> tuple[str, dict]:
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
        milliseconds (no in-progress writes).

        Raises TimeoutError after `timeout` seconds.
        """
        deadline = time.monotonic() + timeout
        quiescence_seconds = quiescence_ms / 1000.0
        poll_interval = min(0.05, quiescence_seconds / 2.0)

        # Snapshot of how many assistant entries existed at call start.
        # In production this is normally 0 (we await right after sending a
        # prompt), but tests may pre-seed the file.
        initial_count = self._count_assistant_entries(session_path)

        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"No completed assistant turn in {session_path} within {timeout}s"
                )

            current_count = self._count_assistant_entries(session_path)

            # Strict acceptance: only return when a NEW assistant entry has appeared
            # since call start. Production-safe — on the 2nd turn of any session
            # (and the 1st turn of any --continue session) the file already holds
            # prior assistant entries, and a lax "any entry exists" gate would
            # return stale data.
            if current_count > initial_count:
                # Check quiescence: mtime must not have changed for quiescence_seconds.
                try:
                    mtime = session_path.stat().st_mtime
                except OSError:
                    await asyncio.sleep(poll_interval)
                    continue
                if (time.time() - mtime) >= quiescence_seconds:
                    return self._extract_last_turn(session_path)

            await asyncio.sleep(poll_interval)

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
