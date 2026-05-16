"""Tests for PtySession — PTY-based Claude Code session manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from hive.runtime.pty_session import PtySession


def _make_mock_proc(read_sequence: list[bytes | Exception] | None = None) -> MagicMock:
    """Build a mock PtyProcess that emits read_sequence on successive read() calls.

    Ends with EOFError once the sequence is exhausted.
    """
    proc = MagicMock()
    proc.isalive.return_value = True
    proc.exitstatus = None

    remaining = list(read_sequence or [])

    def _read(size: int = 1024) -> bytes:
        if not remaining:
            raise EOFError()
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    proc.read.side_effect = _read
    return proc


@pytest.fixture
def mock_spawn():
    """Patch ptyprocess.PtyProcess.spawn and return the mock proc."""
    # Default: trust prompt phase gets EOFError, send phase also gets EOFError
    proc = _make_mock_proc()
    with patch("hive.runtime.pty_session.PtyProcess") as MockPtyProcess:
        MockPtyProcess.spawn.return_value = proc
        yield MockPtyProcess, proc


def _make_proc_with_sequence(sequences: list[bytes | Exception]) -> MagicMock:
    """Convenience wrapper for tests that need a specific read sequence."""
    return _make_mock_proc(sequences)


async def test_start_spawns_with_dangerously_skip_for_dangerous_mode(
    mock_spawn, tmp_path: Path
) -> None:
    MockPtyProcess, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path, permission_mode="yolo")

    await session.start()

    spawn_args = MockPtyProcess.spawn.call_args[0][0]
    assert "--dangerously-skip-permissions" in spawn_args
    assert "--permission-mode" not in spawn_args


async def test_start_spawns_with_permission_mode_flag_for_bypass(
    mock_spawn, tmp_path: Path
) -> None:
    MockPtyProcess, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path, permission_mode="bypassPermissions")

    await session.start()

    spawn_args = MockPtyProcess.spawn.call_args[0][0]
    assert "--permission-mode" in spawn_args
    idx = spawn_args.index("--permission-mode")
    assert spawn_args[idx + 1] == "bypassPermissions"
    assert "--dangerously-skip-permissions" not in spawn_args


async def test_start_spawns_with_model_flag(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    session = PtySession(model="opus", cwd=tmp_path)

    await session.start()

    spawn_args = MockPtyProcess.spawn.call_args[0][0]
    assert "--model" in spawn_args
    idx = spawn_args.index("--model")
    assert spawn_args[idx + 1] == "opus"


async def test_start_adds_continue_when_prior_session_exists(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    # Simulate an existing Claude session: create the projects dir + .jsonl file
    cwd_slug = str(tmp_path).replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / cwd_slug
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "session.jsonl").write_text('{"role":"assistant"}\n')

    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    spawn_args = MockPtyProcess.spawn.call_args[0][0]
    assert "--continue" in spawn_args

    # cleanup
    import shutil

    shutil.rmtree(projects_dir, ignore_errors=True)


async def test_start_no_continue_when_no_prior_session(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    # Ensure the projects dir does NOT have a jsonl for this cwd
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    spawn_args = MockPtyProcess.spawn.call_args[0][0]
    assert "--continue" not in spawn_args


async def test_send_injects_via_bracketed_paste(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    # Default mock raises EOFError on all reads — send() resolves after EOF
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    await session.send("hello world")

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"\x1b[200~" in written  # paste start
    assert b"hello world" in written
    assert b"\x1b[201~" in written  # paste end


async def test_stop_sends_exit_command(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    await session.stop()

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"/exit" in written


async def test_crash_recovery_respawns_on_dead_proc(tmp_path: Path) -> None:
    dead_proc = _make_mock_proc()
    dead_proc.isalive.return_value = False

    recovered_proc = _make_mock_proc([EOFError(), b"recovered response\n\xe2\x9d\xaf "])
    recovered_proc.isalive.return_value = True

    spawn_count = 0

    def _spawn(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        return dead_proc if spawn_count == 1 else recovered_proc

    with (
        patch("hive.runtime.pty_session.PtyProcess") as MockPtyProcess,
        patch("hive.runtime.pty_session.asyncio.sleep"),
    ):
        MockPtyProcess.spawn.side_effect = _spawn
        session = PtySession(model="sonnet", cwd=tmp_path)
        await session.start()
        result = await session.send("hello after crash")

    assert spawn_count >= 2  # initial spawn + at least one recovery spawn
    assert "recovered response" in result


async def test_trust_prompt_auto_accept_writes_carriage_return(tmp_path: Path) -> None:
    proc = _make_mock_proc([b"Do you trust the files in this folder? (y/n)\r\n"])
    with patch("hive.runtime.pty_session.PtyProcess") as MockPtyProcess:
        MockPtyProcess.spawn.return_value = proc
        session = PtySession(model="sonnet", cwd=tmp_path)
        await session.start()

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"\r" in written


async def test_send_returns_text_before_idle_prompt(tmp_path: Path) -> None:
    # Trust-prompt phase gets EOFError immediately; send phase gets real output
    proc = _make_mock_proc([EOFError(), b"Here is my answer.\n\xe2\x9d\xaf "])
    with patch("hive.runtime.pty_session.PtyProcess") as MockPtyProcess:
        MockPtyProcess.spawn.return_value = proc
        session = PtySession(model="sonnet", cwd=tmp_path)
        await session.start()
        result = await session.send("question")

    assert "Here is my answer." in result
    assert "❯" not in result  # idle prompt stripped


async def test_send_chunks_large_payload(mock_spawn, tmp_path: Path) -> None:
    MockPtyProcess, proc = mock_spawn
    large_prompt = "x" * 8192  # 2× chunk size
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    await session.send(large_prompt)

    # Collect all payload bytes written between the paste delimiters
    all_written = b"".join(c[0][0] for c in proc.write.call_args_list)
    start = all_written.find(b"\x1b[200~") + len(b"\x1b[200~")
    end = all_written.find(b"\x1b[201~")
    payload = all_written[start:end]
    assert payload == large_prompt.encode("utf-8")
