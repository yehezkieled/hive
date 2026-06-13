"""Tests for TranscriptReader — reads turns from Claude Code session .jsonl files."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hive.runtime.gates import GateDetector
from hive.runtime.transcript_reader import Gated, TranscriptReader


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _assistant_entry(
    text: str,
    *,
    session_id: str = "sess-1",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_creation: int = 0,
    cache_read: int = 0,
    extra_blocks: list[dict] | None = None,
) -> dict:
    """Build one assistant entry. extra_blocks are inserted BEFORE the text block."""
    content: list[dict] = list(extra_blocks or [])
    content.append({"type": "text", "text": text})
    return {
        "type": "assistant",
        "sessionId": session_id,
        "uuid": f"uuid-{text[:8]}",
        "timestamp": "2026-05-20T00:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _user_entry(text: str, *, session_id: str = "sess-1") -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "uuid": f"uuid-u-{text[:8]}",
        "timestamp": "2026-05-20T00:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def test_identify_session_returns_new_jsonl_when_file_appears(tmp_path: Path) -> None:
    """Fresh session: a brand-new *.jsonl appears in project_dir after spawn."""
    # Pre-spawn snapshot: two files already exist.
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text("old\n")
    b.write_text("older\n")
    snapshot = {a: a.stat().st_size, b: b.stat().st_size}

    # New file appears.
    c = tmp_path / "c.jsonl"
    c.write_text("fresh\n")

    reader = TranscriptReader(tmp_path)
    found = reader.identify_session(snapshot, timeout=1.0)
    assert found == c


def test_identify_session_returns_grown_jsonl_when_continue_resumes(tmp_path: Path) -> None:
    """--continue case: no new file, but an existing *.jsonl grows past its snapshot size."""
    a = tmp_path / "a.jsonl"
    a.write_text("line one\n")
    snapshot = {a: a.stat().st_size}

    # Same file grows (simulating --continue appending new turns).
    with a.open("a", encoding="utf-8") as fh:
        fh.write("line two appended after spawn\n")

    reader = TranscriptReader(tmp_path)
    found = reader.identify_session(snapshot, timeout=1.0)
    assert found == a


def test_identify_session_raises_timeout_when_nothing_changes(tmp_path: Path) -> None:
    """Timeout: no new file, no growth → TimeoutError."""
    a = tmp_path / "a.jsonl"
    a.write_text("unchanged\n")
    snapshot = {a: a.stat().st_size}

    reader = TranscriptReader(tmp_path)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        reader.identify_session(snapshot, timeout=0.3)
    elapsed = time.monotonic() - start
    # Should wait roughly the timeout (allow generous slack for poll cadence).
    assert 0.25 <= elapsed < 1.5


def test_resolve_session_returns_pinned_path_for_session_id(tmp_path: Path) -> None:
    """Session pinning (ADR 0011): a known session id maps to an exact path.

    No directory scanning, no waiting — <project_dir>/<session_id>.jsonl is
    the transcript, even when the file doesn't exist yet (Claude Code creates
    it lazily on first input) and even while sibling files grow in the dir.
    """
    # A decoy that the new-or-growing heuristic WOULD pick (it's brand-new).
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_text("sibling session\n")

    reader = TranscriptReader(tmp_path)
    found = reader.resolve_session("pin-sess", before_sizes={}, timeout=0.1)

    assert found == tmp_path / "pin-sess.jsonl"


def test_resolve_session_without_id_falls_back_to_heuristic_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No session id → the new-or-growing heuristic runs, and the bind is LOUD.

    A fallback bind is a directory guess that can silently mis-attribute turns
    (F3); it must leave a WARNING in the journal so a mis-bind is diagnosable.
    """
    fresh = tmp_path / "fresh.jsonl"
    fresh.write_text("new session\n")

    reader = TranscriptReader(tmp_path)
    with caplog.at_level(logging.WARNING, logger="hive.runtime.transcript_reader"):
        found = reader.resolve_session(None, before_sizes={}, timeout=1.0)

    assert found == fresh
    assert any("session pin unavailable" in m and "falling back" in m for m in caplog.messages)


async def test_await_next_assistant_turn_returns_text_and_usage(tmp_path: Path) -> None:
    """Happy path: a single assistant entry with one text block.

    Strict mode: the assistant entry must appear AFTER the await begins.
    """
    session = tmp_path / "session.jsonl"
    # Pre-write only the user entry; the assistant arrives mid-await.
    _write_jsonl(session, [_user_entry("hello")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _assistant_entry(
                    "hi there",
                    session_id="sess-happy",
                    input_tokens=42,
                    output_tokens=17,
                    cache_creation=3,
                    cache_read=8,
                ),
                _sentinel_entry(session_id="sess-happy"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, usage = await reader.await_next_assistant_turn(session, timeout=1.0, quiescence_ms=50)
    await write_task

    assert text == "hi there"
    assert usage["input_tokens"] == 42
    assert usage["output_tokens"] == 17
    assert usage["cache_creation_input_tokens"] == 3
    assert usage["cache_read_input_tokens"] == 8
    assert usage["session_id"] == "sess-happy"


async def test_await_next_assistant_turn_returns_final_text_after_tool_use(
    tmp_path: Path,
) -> None:
    """A real turn: user → assistant(text+tool_use) → user(tool_result) → assistant(final text).

    Must return the FINAL text block and the LAST assistant entry's usage —
    not the first assistant's intermediate "thinking out loud" text.
    """
    session = tmp_path / "session.jsonl"
    intermediate_assistant = {
        "type": "assistant",
        "sessionId": "sess-tool",
        "uuid": "uuid-a1",
        "timestamp": "2026-05-20T00:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me check..."},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }
    tool_result_user = {
        "type": "user",
        "sessionId": "sess-tool",
        "uuid": "uuid-u-tr",
        "timestamp": "2026-05-20T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "file1.txt"}],
        },
    }
    # Pre-write user + intermediate-assistant + tool_result. The FINAL
    # assistant entry arrives mid-await (strict mode: count must increase).
    _write_jsonl(
        session,
        [
            _user_entry("list files"),
            intermediate_assistant,
            tool_result_user,
        ],
    )

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _assistant_entry(
                    "Here is the final answer.",
                    session_id="sess-tool",
                    input_tokens=200,
                    output_tokens=75,
                    cache_creation=11,
                    cache_read=22,
                ),
                _sentinel_entry(session_id="sess-tool"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, usage = await reader.await_next_assistant_turn(session, timeout=1.0, quiescence_ms=50)
    await write_task

    assert text == "Here is the final answer."
    # Usage must come from the LAST assistant entry, not the intermediate one.
    assert usage["input_tokens"] == 200
    assert usage["output_tokens"] == 75
    assert usage["cache_creation_input_tokens"] == 11
    assert usage["cache_read_input_tokens"] == 22
    assert usage["session_id"] == "sess-tool"


async def test_await_does_not_grab_mid_write_entry_before_sentinel(tmp_path: Path) -> None:
    """A mid-stream assistant entry without a sentinel must not be returned.

    Simulate: start polling, write an assistant entry (no sentinel), pause,
    then append the real final entry together with the turn-end sentinel.
    The reader must return the SECOND entry's text — the first was a
    mid-write flush of an unfinished turn.
    """
    session = tmp_path / "session.jsonl"
    # Start with just a user entry — no assistant yet.
    _write_jsonl(session, [_user_entry("hi")])

    async def writer() -> None:
        # First assistant entry arrives — turn NOT over (no sentinel).
        await asyncio.sleep(0.05)
        _append_jsonl(session, [_assistant_entry("first chunk", session_id="sess-q")])
        await asyncio.sleep(0.2)
        _append_jsonl(
            session,
            [
                _assistant_entry("final chunk", session_id="sess-q"),
                _sentinel_entry(session_id="sess-q"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, _ = await reader.await_next_assistant_turn(session, timeout=3.0, quiescence_ms=50)
    await write_task

    assert text == "final chunk"


async def test_await_next_assistant_turn_raises_timeout_when_no_assistant_entry(
    tmp_path: Path,
) -> None:
    """File never gets an assistant entry → TimeoutError."""
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("hello, anyone home?")])

    reader = TranscriptReader(tmp_path)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await reader.await_next_assistant_turn(session, timeout=0.3, quiescence_ms=50)
    elapsed = time.monotonic() - start
    assert 0.25 <= elapsed < 1.5


async def test_await_returns_gated_when_plan_gate_detected(tmp_path: Path) -> None:
    """With a detector wired, an unanswered ExitPlanMode returns Gated, not text.

    The plan gate freezes the PTY mid-Turn — no completed assistant entry is
    ever written. The reader must return a Gated outcome instead of hanging to
    the timeout.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("plan it")])

    plan_assistant = {
        "type": "assistant",
        "sessionId": "sess-gate",
        "uuid": "uuid-plan",
        "timestamp": "2026-05-20T00:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Here is my plan."},
                {
                    "type": "tool_use",
                    "id": "tu-plan",
                    "name": "ExitPlanMode",
                    "input": {"plan": "1. build the spine"},
                },
            ],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(plan_assistant) + "\n")

    reader = TranscriptReader(tmp_path, gate_detector=GateDetector())
    write_task = asyncio.create_task(writer())
    result = await reader.await_next_assistant_turn(session, timeout=2.0, quiescence_ms=50)
    await write_task

    assert isinstance(result, Gated)
    assert result.gate.kind == "plan"
    assert "build the spine" in result.gate.payload["plan"]


async def test_await_returns_text_not_gated_for_normal_turn_with_detector(
    tmp_path: Path,
) -> None:
    """A normal completed turn still returns (text, usage) even with a detector."""
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("hi")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _assistant_entry("all done", session_id="sess-ok"),
                _sentinel_entry(session_id="sess-ok"),
            ],
        )

    reader = TranscriptReader(tmp_path, gate_detector=GateDetector())
    write_task = asyncio.create_task(writer())
    result = await reader.await_next_assistant_turn(session, timeout=2.0, quiescence_ms=50)
    await write_task

    assert not isinstance(result, Gated)
    text, _usage = result
    assert text == "all done"


async def test_pending_tool_use_in_last_assistant_entry_is_not_accepted(
    tmp_path: Path,
) -> None:
    """A Turn blocked on an in-flight tool call must NOT be accepted early.

    A long sync-wait (e.g. Workflow ``TaskOutput``) writes an assistant entry
    with a ``tool_use`` block, then the file goes quiet while the tool runs.
    Quiescence alone would accept that intermediate entry — the mis-attribution
    race. The reader must keep polling while the LAST assistant entry holds a
    ``tool_use`` with no matching ``tool_result`` anywhere in the file.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("fan out the leaf work")])

    pending_assistant = _assistant_entry(
        "kicking off the run...",
        session_id="sess-pend",
        extra_blocks=[
            {
                "type": "tool_use",
                "id": "tool-pending",
                "name": "TaskOutput",
                "input": {"task_id": "wf-1"},
            }
        ],
    )

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pending_assistant) + "\n")

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    # The reader must still be polling well after the quiescence window —
    # the outer wait_for cancels it, proving the entry was never accepted.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            reader.await_next_assistant_turn(session, timeout=5.0, quiescence_ms=50),
            timeout=0.5,
        )
    await write_task


async def test_accepts_true_final_entry_after_pending_tool_resolves(
    tmp_path: Path,
) -> None:
    """When the tool_result lands and a final entry follows, accept THAT entry.

    The quiet stretch between the tool_use and its result is longer than the
    quiescence window, so without the pending-tool guard the reader would have
    returned the intermediate entry. It must instead return the final entry's
    text and usage.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("fan out the leaf work")])

    pending_assistant = _assistant_entry(
        "launching the workflow...",
        session_id="sess-wf",
        input_tokens=100,
        output_tokens=50,
        extra_blocks=[
            {
                "type": "tool_use",
                "id": "tool-wf",
                "name": "TaskOutput",
                "input": {"task_id": "wf-1"},
            }
        ],
    )
    tool_result_user = {
        "type": "user",
        "sessionId": "sess-wf",
        "uuid": "uuid-u-wf",
        "timestamp": "2026-06-10T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tool-wf", "content": "done"}],
        },
    }

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pending_assistant) + "\n")
        # Quiet stretch well beyond quiescence — the tool is "running".
        await asyncio.sleep(0.25)
        _append_jsonl(
            session,
            [
                tool_result_user,
                _assistant_entry(
                    "All leaf agents finished.",
                    session_id="sess-wf",
                    input_tokens=300,
                    output_tokens=80,
                    cache_creation=7,
                    cache_read=9,
                ),
                _sentinel_entry(session_id="sess-wf"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, usage = await reader.await_next_assistant_turn(session, timeout=2.0, quiescence_ms=50)
    await write_task

    assert text == "All leaf agents finished."
    # Usage must come from the final entry, not the intermediate one.
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 80
    assert usage["cache_creation_input_tokens"] == 7
    assert usage["cache_read_input_tokens"] == 9
    assert usage["session_id"] == "sess-wf"


async def test_gate_detection_wins_over_pending_tool_guard(tmp_path: Path) -> None:
    """An unanswered gate must surface as Gated even though it is ALSO a
    pending tool_use.

    A gate (ExitPlanMode / AskUserQuestion) is itself a tool_use with no
    tool_result. If the pending-tool guard ran before gate detection, the
    reader would poll forever instead of returning Gated — gates would never
    surface. Pin the order: gate check first.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("plan it")])

    gated_assistant = _assistant_entry(
        "Here is my plan.",
        session_id="sess-order",
        extra_blocks=[
            # A non-gate pending tool_use alongside the gate — the guard
            # alone would hold the Turn open on either of these.
            {
                "type": "tool_use",
                "id": "tu-bash",
                "name": "Bash",
                "input": {"command": "ls"},
            },
            {
                "type": "tool_use",
                "id": "tu-plan",
                "name": "ExitPlanMode",
                "input": {"plan": "1. build the spine"},
            },
        ],
    )

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(gated_assistant) + "\n")

    reader = TranscriptReader(tmp_path, gate_detector=GateDetector())
    write_task = asyncio.create_task(writer())
    result = await reader.await_next_assistant_turn(session, timeout=2.0, quiescence_ms=50)
    await write_task

    assert isinstance(result, Gated)
    assert result.gate.kind == "plan"
    assert "build the spine" in result.gate.payload["plan"]


async def test_timeout_resets_while_a_tool_is_pending(tmp_path: Path) -> None:
    """``timeout`` is a no-progress deadline: an in-flight tool counts as progress.

    The transcript sits quiet with a pending tool_use for longer than the
    whole timeout window. A wall-clock timeout would raise mid-wait; the
    no-progress deadline must keep resetting and accept the final entry once
    the tool resolves.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("run the long fan-out")])

    pending_assistant = _assistant_entry(
        "working...",
        session_id="sess-long",
        extra_blocks=[
            {
                "type": "tool_use",
                "id": "tool-long",
                "name": "TaskOutput",
                "input": {"task_id": "wf-long"},
            }
        ],
    )
    tool_result_user = {
        "type": "user",
        "sessionId": "sess-long",
        "uuid": "uuid-u-long",
        "timestamp": "2026-06-10T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tool-long", "content": "ok"}],
        },
    }

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pending_assistant) + "\n")
        # Quiet for LONGER than the 0.3s timeout window — only the pending
        # tool keeps the deadline alive.
        await asyncio.sleep(0.6)
        _append_jsonl(
            session,
            [
                tool_result_user,
                _assistant_entry("run complete", session_id="sess-long"),
                _sentinel_entry(session_id="sess-long"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, _usage = await reader.await_next_assistant_turn(session, timeout=0.3, quiescence_ms=50)
    await write_task

    assert text == "run complete"


async def test_timeout_resets_when_transcript_advances(tmp_path: Path) -> None:
    """A transcript write (mtime advance) resets the no-progress deadline.

    The file is touched at ~0.2s — no assistant entry, no pending tool, just
    movement. The 0.3s deadline must restart from that write, so the
    TimeoutError fires at ~0.5s, not at the original 0.3s mark.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("anyone home?")])

    async def writer() -> None:
        await asyncio.sleep(0.2)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_user_entry("still streaming context...")) + "\n")

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await reader.await_next_assistant_turn(session, timeout=0.3, quiescence_ms=50)
    elapsed = time.monotonic() - start
    await write_task

    # Reset at ~0.2s + a fresh 0.3s window = ~0.5s minimum.
    assert 0.45 <= elapsed < 1.5


async def test_usage_dict_has_exactly_the_five_contract_keys(tmp_path: Path) -> None:
    """The usage dict must contain EXACTLY 5 keys with the contracted shape.

    Source .jsonl has extra keys at message.usage (server_tool_use, service_tier,
    etc.) — these must be dropped. Top-level sessionId must surface as session_id.
    """
    session = tmp_path / "session.jsonl"
    rich_assistant = {
        "type": "assistant",
        "sessionId": "sess-keys",
        "uuid": "uuid-keys",
        "timestamp": "2026-05-20T00:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 3,
                "cache_read_input_tokens": 4,
                # Extra keys present in real prod files — must be IGNORED.
                "server_tool_use": {"web_search_requests": 0},
                "service_tier": "standard",
                "cache_creation": {"ephemeral_5m_input_tokens": 0},
            },
        },
    }
    _write_jsonl(session, [_user_entry("hi")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(session, [rich_assistant, _sentinel_entry(session_id="sess-keys")])

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    _, usage = await reader.await_next_assistant_turn(session, timeout=1.0, quiescence_ms=50)
    await write_task

    assert set(usage.keys()) == {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "session_id",
    }
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 2
    assert usage["cache_creation_input_tokens"] == 3
    assert usage["cache_read_input_tokens"] == 4
    assert usage["session_id"] == "sess-keys"


# ---------------------------------------------------------------------------
# Ticket 026 — sentinel-primary acceptance (ADR 0012)
# ---------------------------------------------------------------------------


def _sentinel_entry(*, session_id: str = "sess-1", duration_ms: int = 1000) -> dict:
    """The turn-end sentinel Claude Code writes when a turn truly completes."""
    return {
        "type": "system",
        "subtype": "turn_duration",
        "sessionId": session_id,
        "uuid": f"uuid-sentinel-{duration_ms}",
        "timestamp": "2026-05-20T00:00:02.000Z",
        "durationMs": duration_ms,
    }


def _append_jsonl(path: Path, entries: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


async def test_sentinel_accepts_without_quiescence_wait(tmp_path: Path) -> None:
    """A turn followed by its sentinel is accepted immediately — no dead-wait.

    quiescence_ms is set far above the timeout: only sentinel acceptance can
    return in time. The sentinel is deterministic (file order verified
    1,942/1,942 in research.md), so no quiet period is needed.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("hello")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _assistant_entry("final answer", session_id="sess-s1"),
                _sentinel_entry(session_id="sess-s1"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    start = time.monotonic()
    text, usage = await reader.await_next_assistant_turn(session, timeout=3.0, quiescence_ms=5000)
    elapsed = time.monotonic() - start
    await write_task

    assert text == "final answer"
    assert usage["session_id"] == "sess-s1"
    assert elapsed < 1.5


async def test_sentinel_acceptance_is_count_based(tmp_path: Path) -> None:
    """--continue shape: stale sentinels from prior turns must not trigger.

    The transcript is pre-seeded with a completed prior turn (assistant +
    sentinel). The new turn's assistant entry arrives WITHOUT a sentinel
    first — the reader must keep waiting — then the NEW sentinel lands and
    only then is the turn accepted, with the NEW text.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(
        session,
        [
            _user_entry("old prompt"),
            _assistant_entry("old answer", session_id="sess-cont"),
            _sentinel_entry(session_id="sess-cont", duration_ms=111),
            _user_entry("new prompt"),
        ],
    )

    accepted_at: list[float] = []
    sentinel_written_at: list[float] = []

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(session, [_assistant_entry("new answer", session_id="sess-cont")])
        # Quiet stretch with a new assistant entry but NO new sentinel: the
        # reader must not accept on the stale sentinel count.
        await asyncio.sleep(0.4)
        sentinel_written_at.append(time.monotonic())
        _append_jsonl(session, [_sentinel_entry(session_id="sess-cont", duration_ms=222)])

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, _usage = await reader.await_next_assistant_turn(session, timeout=3.0, quiescence_ms=5000)
    accepted_at.append(time.monotonic())
    await write_task

    assert text == "new answer"
    # Acceptance must come after the NEW sentinel was written, not before.
    assert accepted_at[0] >= sentinel_written_at[0]


def _tool_use_assistant(text: str, *, tool_id: str, session_id: str = "sess-1") -> dict:
    """Assistant entry that emits a tool call (stop_reason=tool_use)."""
    entry = _assistant_entry(
        text,
        session_id=session_id,
        extra_blocks=[],
    )
    entry["message"]["content"].append(
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": "ls"}}
    )
    entry["message"]["stop_reason"] = "tool_use"
    return entry


def _tool_result_user(tool_id: str, *, session_id: str = "sess-1") -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "uuid": f"uuid-u-{tool_id}",
        "timestamp": "2026-05-20T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
        },
    }


async def test_does_not_accept_during_post_tool_thinking_gap(tmp_path: Path) -> None:
    """The 023 smoke failure class: a resolved tool followed by silent thinking.

    Once the tool_result lands, the pending-tool guard passes and the file
    goes quiet while the model thinks — far longer than any quiescence
    window (fleet p50 = 4.8 s vs the old 500 ms). The reader must NOT
    accept the intermediate tool-calling entry; it must keep waiting for
    the real final message (here marked by its sentinel).
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(
        session,
        [_user_entry("do the thing", session_id="sess-gap")],
    )

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _tool_use_assistant("let me check...", tool_id="t-gap", session_id="sess-gap"),
                _tool_result_user("t-gap", session_id="sess-gap"),
            ],
        )
        # Post-tool thinking: file silent, well beyond the quiescence window.
        await asyncio.sleep(0.4)
        _append_jsonl(
            session,
            [
                _assistant_entry("the real final answer", session_id="sess-gap"),
                _sentinel_entry(session_id="sess-gap"),
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, _usage = await reader.await_next_assistant_turn(session, timeout=3.0, quiescence_ms=50)
    await write_task

    assert text == "the real final answer"


async def test_smoke_timeline_replay_yields_final_message(tmp_path: Path) -> None:
    """The 023 live-smoke incident (transcript a012a36d), replayed as a fixture.

    Real sequence: assistant(thinking) → assistant(text) → assistant(tool_use)
    → tool_result → 3.2 s silent thinking → final batch (thinking + text with
    the hive_actions proposal, both stamped end_turn) → sentinel 158 ms later.
    The old reader accepted during the silent gap and lost the proposal; the
    sentinel ladder must deliver it.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("status-check the org", session_id="a012a36d")])

    final_text = 'Proposal ready.\n<hive_actions>[{"to": "maestro"}]</hive_actions>'

    def _thinking_entry(stop_reason: str | None) -> dict:
        entry = _assistant_entry("", session_id="a012a36d")
        entry["message"]["content"] = [{"type": "thinking", "thinking": "..."}]
        if stop_reason:
            entry["message"]["stop_reason"] = stop_reason
        return entry

    async def writer() -> None:
        await asyncio.sleep(0.05)
        intermediate_text = _assistant_entry("Let me run a check.", session_id="a012a36d")
        intermediate_text["message"]["stop_reason"] = "tool_use"
        _append_jsonl(
            session,
            [
                _thinking_entry("tool_use"),
                intermediate_text,
                _tool_use_assistant("", tool_id="t-smoke", session_id="a012a36d"),
                _tool_result_user("t-smoke", session_id="a012a36d"),
            ],
        )
        # The 3.2 s post-tool thinking gap, scaled down — file totally silent.
        await asyncio.sleep(0.4)
        final_entry = _assistant_entry(final_text, session_id="a012a36d")
        final_entry["message"]["stop_reason"] = "end_turn"
        _append_jsonl(
            session,
            [_thinking_entry("end_turn"), final_entry, _sentinel_entry(session_id="a012a36d")],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    text, _usage = await reader.await_next_assistant_turn(session, timeout=3.0, quiescence_ms=50)
    await write_task

    assert text == final_text
    assert "<hive_actions>" in text


async def test_fallback_accepts_endturn_text_after_window_with_loud_logging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sentinel-less transcript: hardened-quiescence fallback, degrade loudly.

    Acceptance requires stop_reason=end_turn + a text block + the fallback
    window of silence. The FIRST fallback acceptance for a session logs at
    ERROR (a missing sentinel means the CC transcript format may have
    changed); subsequent ones drop to WARNING.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("turn one", session_id="sess-fb")])

    def _final(text: str) -> dict:
        entry = _assistant_entry(text, session_id="sess-fb")
        entry["message"]["stop_reason"] = "end_turn"
        return entry

    reader = TranscriptReader(tmp_path)

    async def writer_one() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(session, [_final("first turn answer")])

    with caplog.at_level(logging.WARNING, logger="hive.runtime.transcript_reader"):
        task = asyncio.create_task(writer_one())
        text, _usage = await reader.await_next_assistant_turn(
            session, timeout=3.0, quiescence_ms=50, fallback_quiescence_s=0.2
        )
        await task
        assert text == "first turn answer"

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "sentinel" in errors[0].message.lower()

        # Second turn on the same session: fallback again → WARNING, not ERROR.
        caplog.clear()
        _append_jsonl(session, [_user_entry("turn two", session_id="sess-fb")])

        async def writer_two() -> None:
            await asyncio.sleep(0.05)
            _append_jsonl(session, [_final("second turn answer")])

        task = asyncio.create_task(writer_two())
        text, _usage = await reader.await_next_assistant_turn(
            session, timeout=3.0, quiescence_ms=50, fallback_quiescence_s=0.2
        )
        await task
        assert text == "second turn answer"

        assert not [r for r in caplog.records if r.levelno == logging.ERROR]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "sentinel" in warnings[0].message.lower()


async def test_fallback_rejects_tool_use_stamped_entry(tmp_path: Path) -> None:
    """Sentinel-less + last entry stamped tool_use → never accepted.

    This is the smoke-failure entry shape (the wrongly-accepted entry was
    stop_reason=tool_use). Even with its tool resolved and the file quiet
    past the fallback window, a tool_use-stamped entry is intermediate —
    the reader must hold out to the no-progress timeout rather than
    deliver it.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(
        session,
        [_user_entry("do it", session_id="sess-rej")],
    )

    async def writer() -> None:
        await asyncio.sleep(0.05)
        _append_jsonl(
            session,
            [
                _tool_use_assistant("working on it", tool_id="t-rej", session_id="sess-rej"),
                _tool_result_user("t-rej", session_id="sess-rej"),
                # No final message, no sentinel: the session died thinking.
            ],
        )

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    with pytest.raises(TimeoutError):
        await reader.await_next_assistant_turn(
            session, timeout=0.5, quiescence_ms=50, fallback_quiescence_s=0.1
        )
    await write_task


# ---------------------------------------------------------------------------
# Ticket 017 §E2 — reader liveness-reset on a live Workflow run (absorbs 027)
# ---------------------------------------------------------------------------


async def test_live_workflow_resets_deadline_instead_of_timing_out(tmp_path: Path) -> None:
    """A live Workflow run keeps the no-progress deadline open (027 root cause).

    The Lead's transcript is frozen (no new entries, no mtime advance) while the
    run's journal advances elsewhere. Without the predicate the reader would
    declare the healthy Lead dead. With ``workflow_active`` returning True it must
    RESET the deadline at the would-be timeout and keep polling — so NO
    TimeoutError fires within a multiple of the (tiny) window.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("fan out the leaf work")])

    reader = TranscriptReader(tmp_path, workflow_active=lambda _window: True)
    # The window is tiny; with the reset the reader keeps polling. Bound the test
    # with an outer wait_for: it should still be polling when we cancel it.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            reader.await_next_assistant_turn(session, timeout=0.1, quiescence_ms=50),
            timeout=0.6,
        )


async def test_quiet_session_still_times_out_when_workflow_inactive(tmp_path: Path) -> None:
    """No-hang: with ``workflow_active`` False the stale orphan still times out.

    The Lead's Turn died — files frozen, no run alive. The reader must NOT loop
    forever; the no-progress deadline must fire as before.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("anyone home?")])

    reader = TranscriptReader(tmp_path, workflow_active=lambda _window: False)
    with pytest.raises(TimeoutError):
        await reader.await_next_assistant_turn(session, timeout=0.3, quiescence_ms=50)


async def test_timeout_message_is_friendly_and_hides_path(tmp_path: Path) -> None:
    """The TimeoutError message must not leak the transcript path / ``.jsonl``.

    It should name the timeout seconds and point at the dashboard — a Workflow may
    still be running. The raw file path is an internal detail no user should see.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("quiet please")])

    reader = TranscriptReader(tmp_path)
    with pytest.raises(TimeoutError) as exc_info:
        await reader.await_next_assistant_turn(session, timeout=0.3, quiescence_ms=50)

    message = str(exc_info.value)
    assert ".jsonl" not in message
    assert str(session) not in message
    assert "0.3" in message or "0.3s" in message
    assert "dashboard" in message.lower()
    assert "workflow" in message.lower()


async def test_fallback_rejects_textless_endturn_entry(tmp_path: Path) -> None:
    """Sentinel-less + end_turn entry with no text (bare thinking) → not accepted.

    A thinking-only entry can carry the final stop_reason (batch flush stamps
    every entry of the final response — research.md §3). Accepting it would
    deliver empty text; the fallback requires a text-bearing entry.
    """
    session = tmp_path / "session.jsonl"
    _write_jsonl(session, [_user_entry("think hard", session_id="sess-think")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        thinking = _assistant_entry("", session_id="sess-think")
        thinking["message"]["content"] = [{"type": "thinking", "thinking": "hmm"}]
        thinking["message"]["stop_reason"] = "end_turn"
        _append_jsonl(session, [thinking])

    reader = TranscriptReader(tmp_path)
    write_task = asyncio.create_task(writer())
    with pytest.raises(TimeoutError):
        await reader.await_next_assistant_turn(
            session, timeout=0.5, quiescence_ms=50, fallback_quiescence_s=0.1
        )
    await write_task


# ---------------------------------------------------------------------------
# Ticket 017 §E2 — PtySession wires the liveness predicate into the reader
# ---------------------------------------------------------------------------


async def test_pty_session_wires_lazy_workflow_active_into_reader(tmp_path: Path) -> None:
    """start() must hand the reader a LAZY workflow_active predicate (§E2 wiring).

    ``self.session_dir`` is None until the session is pinned on first send, so the
    predicate cannot capture a fixed dir at construction — it must be a lambda that
    reads ``self.session_dir`` and ``run_active`` at CALL time. We capture the
    predicate start() passes, then prove it is lazy: it's still None-safe before a
    pin, and after we set ``_session_path`` it forwards (session_dir, window) to
    ``run_active``.
    """
    from hive.runtime.pty_session import PtySession

    captured: dict[str, object] = {}

    def fake_reader_ctor(project_dir: Path, *, gate_detector=None, workflow_active=None):
        captured["workflow_active"] = workflow_active
        return MagicMock()

    cwd = tmp_path / "checkout"
    cwd.mkdir()

    proc = MagicMock()
    proc.isalive.return_value = True
    proc.pid = 4321
    # Let the daemon reader thread exit cleanly instead of blocking on read()
    # and later poking a closed event loop (cosmetic teardown noise otherwise).
    proc.read.side_effect = EOFError

    with (
        patch("hive.runtime.pty_session.PtyProcess") as mock_pty_cls,
        patch("hive.runtime.pty_session.TranscriptReader", side_effect=fake_reader_ctor),
        patch.object(PtySession, "_handle_trust_prompt", return_value=None),
        patch("hive.runtime.pty_session.run_active") as mock_run_active,
    ):
        mock_pty_cls.spawn.return_value = proc
        session = PtySession(cwd=cwd)
        await session.start()

        predicate = captured["workflow_active"]
        assert callable(predicate)

        # Lazy + None-safe: no pin yet → session_dir is None → run_active sees None.
        mock_run_active.return_value = False
        assert predicate(0.3) is False
        mock_run_active.assert_called_with(None, 0.3)

        # After a pin, the SAME predicate now forwards the resolved session_dir.
        session._session_path = tmp_path / "abc-123.jsonl"
        mock_run_active.return_value = True
        assert predicate(180.0) is True
        mock_run_active.assert_called_with(tmp_path / "abc-123", 180.0)

        # Let the daemon reader thread drain its EOF and stop touching the loop.
        reader_task = session._reader_task
        if reader_task is not None:
            await asyncio.gather(reader_task, return_exceptions=True)
