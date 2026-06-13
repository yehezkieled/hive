"""Tests for the Workflow run-record surface (Ticket 017 slice #116).

A Lead's Claude Code Workflow run writes, under the session working dir:
  <session>/workflows/wf_<id>.json                 — state snapshot
  <session>/subagents/workflows/wf_<id>/journal.jsonl — started/result events

`parse_run_dir` turns those into uniform `WorkflowProgress`, fail-soft (never
raises into the Lead path). `run_active` is the no-hang liveness predicate the
transcript reader consults. The adapter wires both to the pinned session dir.
"""

from __future__ import annotations

import json
from pathlib import Path

from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig
from hive.runtime.workflow_progress import (
    WorkflowProgress,
    parse_run_dir,
    run_active,
)


def _write_run(
    session_dir: Path,
    run_id: str,
    *,
    name: str = "demo",
    status: str = "running",
    agent_count: int = 3,
    started: int = 3,
    results: int = 0,
    phase_titles: list[str] | None = None,
    result_payloads: list[object] | None = None,
) -> Path:
    """Materialise a realistic run dir; returns the wf_<id>.json path."""
    wdir = session_dir / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    titles = phase_titles or ["Implement"]
    state = {
        "runId": run_id,
        "workflowName": name,
        "agentCount": agent_count,
        "status": status,
        "phases": [{"title": t} for t in titles],
        "workflowProgress": [
            {"type": "phase", "index": i, "title": t} for i, t in enumerate(titles)
        ],
        "result": result_payloads or [],
    }
    state_path = wdir / f"{run_id}.json"
    state_path.write_text(json.dumps(state))

    jdir = session_dir / "subagents" / "workflows" / run_id
    jdir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i in range(started):
        lines.append(json.dumps({"type": "started", "agentId": f"a{i}"}))
    for i in range(results):
        payload = result_payloads[i] if result_payloads and i < len(result_payloads) else {"i": i}
        lines.append(json.dumps({"type": "result", "agentId": f"a{i}", "result": payload}))
    (jdir / "journal.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))
    return state_path


# ── parse_run_dir ────────────────────────────────────────────────────────────


def test_completed_run_maps_fields(tmp_path: Path) -> None:
    _write_run(
        tmp_path, "wf_a", name="review", status="completed", agent_count=2, started=2, results=2
    )
    runs = parse_run_dir(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert isinstance(run, WorkflowProgress)
    assert run.run_id == "wf_a"
    assert run.name == "review"
    assert run.status == "completed"
    assert run.agent_count == 2
    assert run.done_count == 2


def test_running_run_is_partial(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_b", status="running", agent_count=8, started=8, results=5)
    (run,) = parse_run_dir(tmp_path)
    assert run.status == "running"
    assert run.agent_count == 8
    assert run.done_count == 5  # N < M


def test_failed_run_status_preserved(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_c", status="failed", agent_count=3, started=3, results=1)
    (run,) = parse_run_dir(tmp_path)
    assert run.status == "failed"


def test_done_count_from_journal_results(tmp_path: Path) -> None:
    # 6 started, 3 results → done_count counts results, not starts.
    _write_run(tmp_path, "wf_d", agent_count=6, started=6, results=3)
    (run,) = parse_run_dir(tmp_path)
    assert run.done_count == 3


def test_phase_is_latest_title(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_e", phase_titles=["Scan", "Fix"])
    (run,) = parse_run_dir(tmp_path)
    assert run.phase == "Fix"


def test_partials_capped_and_truncated(tmp_path: Path) -> None:
    big = "x" * 5000
    _write_run(
        tmp_path,
        "wf_f",
        agent_count=5,
        started=5,
        results=5,
        result_payloads=[big, big, big, big, big],
    )
    (run,) = parse_run_dir(tmp_path)
    assert run.partials is not None
    assert len(run.partials) == 3  # last 3 only
    assert all(len(p) <= 280 for p in run.partials)


def test_multiple_runs_each_parsed(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_g1", status="completed")
    _write_run(tmp_path, "wf_g2", status="running")
    runs = parse_run_dir(tmp_path)
    assert {r.run_id for r in runs} == {"wf_g1", "wf_g2"}


# ── fail-soft (per-file, never raises) ───────────────────────────────────────


def test_corrupt_state_file_skipped_others_survive(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_ok", status="completed")
    # A half-written / corrupt state file in the same glob.
    (tmp_path / "workflows" / "wf_bad.json").write_text("{ not valid json")
    runs = parse_run_dir(tmp_path)
    assert [r.run_id for r in runs] == ["wf_ok"]  # bad one skipped, good survives


def test_corrupt_journal_line_tolerated(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_h", agent_count=3, started=3, results=2)
    journal = tmp_path / "subagents" / "workflows" / "wf_h" / "journal.jsonl"
    journal.write_text(
        json.dumps({"type": "result", "agentId": "a0", "result": {}})
        + "\nnot-json-garbage\n"
        + json.dumps({"type": "result", "agentId": "a1", "result": {}})
        + "\n"
    )
    (run,) = parse_run_dir(tmp_path)
    assert run.done_count == 2  # the two valid result lines, bad line ignored


def test_missing_session_dir_returns_empty(tmp_path: Path) -> None:
    assert parse_run_dir(tmp_path / "does-not-exist") == []


def test_none_session_dir_returns_empty() -> None:
    assert parse_run_dir(None) == []


def test_no_workflow_files_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "workflows").mkdir()
    assert parse_run_dir(tmp_path) == []


# ── run_active (no-hang liveness) ────────────────────────────────────────────


def test_run_active_true_when_fresh(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_live", started=2, results=1)
    # Files just written → mtime ~now → within any reasonable window.
    assert run_active(tmp_path, window=180.0) is True


def test_run_active_false_when_stale_orphan(tmp_path: Path) -> None:
    # The no-hang guarantee (ADR 0014): a frozen orphan's files are old, so
    # liveness is False and the reader stops resetting → times out, no loop.
    state = _write_run(tmp_path, "wf_orphan", status="running", started=3, results=1)
    journal = tmp_path / "subagents" / "workflows" / "wf_orphan" / "journal.jsonl"
    old = 10_000.0  # ~old epoch second; far outside the window
    import os

    os.utime(state, (old, old))
    os.utime(journal, (old, old))
    assert run_active(tmp_path, window=180.0) is False


def test_run_active_false_when_no_runs(tmp_path: Path) -> None:
    assert run_active(tmp_path, window=180.0) is False
    assert run_active(None, window=180.0) is False


# ── adapter wiring ───────────────────────────────────────────────────────────


def test_poll_workflow_progress_empty_without_session() -> None:
    adapter = ClaudeAdapter(ClaudeAdapterConfig())
    # No PTY started → no session dir → [] (never raises).
    assert adapter.poll_workflow_progress() == []
    assert adapter.workflow_active(180.0) is False


def test_poll_workflow_progress_reads_session_dir(tmp_path: Path) -> None:
    _write_run(tmp_path, "wf_w", status="completed", agent_count=2, started=2, results=2)

    class _FakePty:
        session_dir = tmp_path

    adapter = ClaudeAdapter(ClaudeAdapterConfig())
    adapter._pty = _FakePty()  # type: ignore[assignment]
    runs = adapter.poll_workflow_progress()
    assert [r.run_id for r in runs] == ["wf_w"]
    assert adapter.workflow_active(180.0) is True


def test_pty_session_dir_derives_from_session_path(tmp_path: Path) -> None:
    from hive.runtime.pty_session import PtySession

    pty = PtySession()
    assert pty.session_dir is None  # not resolved yet
    pty._session_path = tmp_path / "abc-123.jsonl"
    assert pty.session_dir == tmp_path / "abc-123"
