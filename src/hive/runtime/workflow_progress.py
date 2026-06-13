"""Read-only view of a Claude Code Workflow run's on-disk record (Ticket 017).

A Lead drives leaf work by calling the Claude Code **Workflow** tool inside one
sync-wait Turn (ADR 0010). That run writes, under the Lead's pinned session
working dir:

    <session>/workflows/wf_<id>.json                      ← state snapshot
    <session>/subagents/workflows/wf_<id>/journal.jsonl   ← started/result events

This module turns those into a uniform :class:`WorkflowProgress`, with the
Claude-Code-specific file layout **quarantined here** (ADR 0001 / ADR 0014). It
is read-only and **fail-soft**: a missing, half-written, or unexpected file
yields an empty / partial result, never an exception into the Lead path.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Partials are dashboard-only and never carried in a notification, so a large
# agent result can't choke the SSE queue or exceed Telegram's message limit.
MAX_PARTIALS = 3
PARTIAL_CHARS = 280


@dataclass(frozen=True)
class WorkflowProgress:
    """Harness-neutral snapshot of one Workflow run. Consumed by the sweeper,
    dashboard, and notifications — none of which see Claude Code's file shapes."""

    run_id: str
    name: str
    phase: str | None
    agent_count: int
    done_count: int
    status: str
    partials: list[str] | None = None


def parse_run_dir(session_dir: Path | None) -> list[WorkflowProgress]:
    """Every Workflow run under ``session_dir``, fail-soft per file.

    A corrupt or half-written ``wf_*.json`` skips *that* file and continues
    with the rest; a missing dir or no matches yields ``[]``. Never raises.
    """
    if session_dir is None:
        return []
    runs_dir = session_dir / "workflows"
    if not runs_dir.is_dir():
        return []
    runs: list[WorkflowProgress] = []
    for state_file in sorted(runs_dir.glob("wf_*.json")):
        run = _parse_state_file(state_file, session_dir)
        if run is not None:
            runs.append(run)
    return runs


def run_active(session_dir: Path | None, window: float, now: float | None = None) -> bool:
    """True iff a run's state/journal file mtime advanced within ``window`` s.

    The no-hang guarantee (ADR 0014): existence is *not* enough — a stale
    orphan (the Lead's Turn died, files frozen at ``status:"running"``) has an
    old mtime, so this returns False and the transcript reader stops resetting
    its deadline instead of looping forever.
    """
    if session_dir is None or window <= 0:
        return False
    now = time.time() if now is None else now
    for path in _run_files(session_dir):
        try:
            if (now - path.stat().st_mtime) <= window:
                return True
        except OSError:
            continue
    return False


# ── internals (the only CC-layout-aware code) ────────────────────────────────


def _run_files(session_dir: Path) -> list[Path]:
    files: list[Path] = []
    runs_dir = session_dir / "workflows"
    if runs_dir.is_dir():
        files.extend(runs_dir.glob("wf_*.json"))
    subagents = session_dir / "subagents" / "workflows"
    if subagents.is_dir():
        files.extend(subagents.glob("wf_*/journal.jsonl"))
    return files


def _parse_state_file(state_file: Path, session_dir: Path) -> WorkflowProgress | None:
    try:
        data = json.loads(state_file.read_text())
    except (OSError, ValueError):
        logger.debug("workflow state unreadable, skipping: %s", state_file, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None

    run_id = str(data.get("runId") or state_file.stem)
    done_count, partials = _journal_progress(session_dir, run_id)
    if done_count is None:
        result = data.get("result")
        done_count = len(result) if isinstance(result, list) else 0
    return WorkflowProgress(
        run_id=run_id,
        name=str(data.get("workflowName") or run_id),
        phase=_latest_phase(data),
        agent_count=_as_int(data.get("agentCount")),
        done_count=done_count,
        status=str(data.get("status") or "running"),
        partials=partials or None,
    )


def _journal_progress(session_dir: Path, run_id: str) -> tuple[int | None, list[str]]:
    """``(done_count, partials)`` from the run journal, or ``(None, [])`` if
    there is no journal (caller falls back to ``result[]``)."""
    journal = session_dir / "subagents" / "workflows" / run_id / "journal.jsonl"
    try:
        lines = journal.read_text().splitlines()
    except OSError:
        return None, []
    done = 0
    results: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # tolerate a torn / garbage line
        if isinstance(event, dict) and event.get("type") == "result":
            done += 1
            results.append(_truncate(event.get("result")))
    return done, results[-MAX_PARTIALS:]


def _latest_phase(data: dict) -> str | None:
    for key in ("workflowProgress", "phases"):
        seq = data.get(key)
        if isinstance(seq, list):
            titles = [e.get("title") for e in seq if isinstance(e, dict) and e.get("title")]
            if titles:
                return str(titles[-1])
    return None


def _truncate(payload: object) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return text[:PARTIAL_CHARS]


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
