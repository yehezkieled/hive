"""Tests for TranscriptReader — reads turns from Claude Code session .jsonl files."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

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


async def test_await_next_assistant_turn_returns_text_and_usage(tmp_path: Path) -> None:
    """Happy path: a single assistant entry with one text block.

    Strict mode: the assistant entry must appear AFTER the await begins.
    """
    session = tmp_path / "session.jsonl"
    # Pre-write only the user entry; the assistant arrives mid-await.
    _write_jsonl(session, [_user_entry("hello")])

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    _assistant_entry(
                        "hi there",
                        session_id="sess-happy",
                        input_tokens=42,
                        output_tokens=17,
                        cache_creation=3,
                        cache_read=8,
                    )
                )
                + "\n"
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
        with session.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    _assistant_entry(
                        "Here is the final answer.",
                        session_id="sess-tool",
                        input_tokens=200,
                        output_tokens=75,
                        cache_creation=11,
                        cache_read=22,
                    )
                )
                + "\n"
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


async def test_await_next_assistant_turn_waits_for_quiescence(tmp_path: Path) -> None:
    """While the file is mid-write (mtime keeps changing), do not return.

    Simulate: start polling, then write an assistant entry, pause briefly
    (less than quiescence), append a second assistant entry, then go quiet.
    The reader must wait until after the SECOND entry's quiescence window
    and return the SECOND entry's text.
    """
    session = tmp_path / "session.jsonl"
    # Start with just a user entry — no assistant yet.
    _write_jsonl(session, [_user_entry("hi")])

    quiescence_ms = 150
    quiescence_s = quiescence_ms / 1000.0

    async def writer() -> None:
        # First assistant entry arrives.
        await asyncio.sleep(0.05)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_assistant_entry("first chunk", session_id="sess-q")) + "\n")
        # Mid-stream pause (shorter than quiescence) → reader must NOT return yet.
        await asyncio.sleep(quiescence_s * 0.4)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_assistant_entry("final chunk", session_id="sess-q")) + "\n")
        # Now go quiet.

    reader = TranscriptReader(tmp_path)

    async def runner() -> tuple[str, dict]:
        return await reader.await_next_assistant_turn(
            session, timeout=3.0, quiescence_ms=quiescence_ms
        )

    write_task = asyncio.create_task(writer())
    start = time.monotonic()
    text, _ = await runner()
    elapsed = time.monotonic() - start
    await write_task

    assert text == "final chunk"
    # Must wait long enough that the second write's quiescence window passes.
    # Lower bound: first write at 0.05s + mid pause (0.4*q) + quiescence (q)
    #            = ~0.05 + 0.06 + 0.15 = 0.26s minimum.
    assert elapsed >= 0.05 + quiescence_s


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
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_assistant_entry("all done", session_id="sess-ok")) + "\n")

    reader = TranscriptReader(tmp_path, gate_detector=GateDetector())
    write_task = asyncio.create_task(writer())
    result = await reader.await_next_assistant_turn(session, timeout=2.0, quiescence_ms=50)
    await write_task

    assert not isinstance(result, Gated)
    text, _usage = result
    assert text == "all done"


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
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rich_assistant) + "\n")

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
