"""Tests for PtySession — PTY-based Claude Code session manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.runtime.pty_session import PtySession, _claude_projects_dir


def test_claude_projects_dir_replaces_slashes_and_dots() -> None:
    """Regression: Claude Code's slug rule replaces BOTH '/' and '.' with '-'.

    A cwd like /home/x/repo/.claude/worktrees/foo becomes
    -home-x-repo--claude-worktrees-foo (note the double-dash because '/.'
    becomes '--'). Earlier code only replaced '/' and silently mis-located
    the transcript dir for any cwd containing a dot.
    """
    cwd = Path("/home/hezki/projects/hive/.claude/worktrees/fix-pty-output")
    expected = (
        Path.home()
        / ".claude"
        / "projects"
        / ("-home-hezki-projects-hive--claude-worktrees-fix-pty-output")
    )
    assert _claude_projects_dir(cwd) == expected


def test_claude_projects_dir_for_dotless_path() -> None:
    """Sanity: a cwd with no dots produces the simple dash-substituted slug."""
    cwd = Path("/home/hezki/projects/hive")
    expected = Path.home() / ".claude" / "projects" / "-home-hezki-projects-hive"
    assert _claude_projects_dir(cwd) == expected


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
    with patch("hive.runtime.pty_session.PtyProcess") as mock_cls:
        mock_cls.spawn.return_value = proc
        yield mock_cls, proc


@pytest.fixture
def mock_transcript_reader():
    """Patch TranscriptReader inside pty_session and return the mock instance.

    Returns canned data so tests can exercise send() without a real .jsonl.
    """
    with patch("hive.runtime.pty_session.TranscriptReader") as mock_reader_cls:
        mock_reader = mock_reader_cls.return_value
        mock_reader.identify_session.return_value = Path("/tmp/fake-session.jsonl")
        mock_reader.await_next_assistant_turn = AsyncMock(
            return_value=(
                "canned response",
                {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "session_id": "sess-mock",
                },
            )
        )
        yield mock_reader


async def test_start_spawns_with_dangerously_skip_for_dangerous_mode(
    mock_spawn, tmp_path: Path
) -> None:
    mock_cls, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path, permission_mode="yolo")

    await session.start()

    spawn_args = mock_cls.spawn.call_args[0][0]
    assert "--dangerously-skip-permissions" in spawn_args
    assert "--permission-mode" not in spawn_args


async def test_start_spawns_with_dangerously_skip_for_bypass(mock_spawn, tmp_path: Path) -> None:
    # bypassPermissions bypasses tool prompts but NOT the trust dialog;
    # we route it through --dangerously-skip-permissions to skip both.
    mock_cls, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path, permission_mode="bypassPermissions")

    await session.start()

    spawn_args = mock_cls.spawn.call_args[0][0]
    assert "--dangerously-skip-permissions" in spawn_args
    assert "--permission-mode" not in spawn_args


async def test_start_spawns_with_model_flag(mock_spawn, tmp_path: Path) -> None:
    mock_cls, proc = mock_spawn
    session = PtySession(model="opus", cwd=tmp_path)

    await session.start()

    spawn_args = mock_cls.spawn.call_args[0][0]
    assert "--model" in spawn_args
    idx = spawn_args.index("--model")
    assert spawn_args[idx + 1] == "opus"


async def test_start_adds_continue_when_prior_session_exists(mock_spawn, tmp_path: Path) -> None:
    mock_cls, proc = mock_spawn
    # Simulate an existing Claude session: create the projects dir + .jsonl file
    cwd_slug = str(tmp_path).replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / cwd_slug
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "session.jsonl").write_text('{"role":"assistant"}\n')

    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    spawn_args = mock_cls.spawn.call_args[0][0]
    assert "--continue" in spawn_args

    # cleanup
    import shutil

    shutil.rmtree(projects_dir, ignore_errors=True)


async def test_start_no_continue_when_no_prior_session(mock_spawn, tmp_path: Path) -> None:
    mock_cls, proc = mock_spawn
    # Ensure the projects dir does NOT have a jsonl for this cwd
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    spawn_args = mock_cls.spawn.call_args[0][0]
    assert "--continue" not in spawn_args


async def test_start_snapshots_project_dir_jsonls(mock_spawn, tmp_path: Path) -> None:
    """start() must snapshot the *.jsonl set + sizes BEFORE spawning, so
    TranscriptReader can later tell our session's file apart from siblings."""
    mock_cls, proc = mock_spawn
    cwd_slug = str(tmp_path).replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / cwd_slug
    projects_dir.mkdir(parents=True, exist_ok=True)
    file_a = projects_dir / "a.jsonl"
    file_b = projects_dir / "b.jsonl"
    file_a.write_text("seed-a\n")
    file_b.write_text("seed-bigger\n")

    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    assert session._project_dir == projects_dir
    assert set(session._before_sizes.keys()) == {file_a, file_b}
    assert session._before_sizes[file_a] == file_a.stat().st_size
    assert session._before_sizes[file_b] == file_b.stat().st_size

    # cleanup
    import shutil

    shutil.rmtree(projects_dir, ignore_errors=True)


async def test_send_injects_via_bracketed_paste(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    mock_cls, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    await session.send("hello world")

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"\x1b[200~" in written  # paste start
    assert b"hello world" in written
    assert b"\x1b[201~" in written  # paste end


async def test_send_returns_text_and_usage_from_transcript(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    """send() returns (text, usage) sourced from the transcript reader, not the screen."""
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    text, usage = await session.send("hi")

    assert text == "canned response"
    assert usage["input_tokens"] == 1
    assert usage["session_id"] == "sess-mock"


async def test_send_identifies_session_only_on_first_call(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    """identify_session should run on the FIRST send() only; cached thereafter."""
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    await session.send("first")
    await session.send("second")
    await session.send("third")

    assert mock_transcript_reader.identify_session.call_count == 1


async def test_stop_sends_exit_command(mock_spawn, tmp_path: Path) -> None:
    mock_cls, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    await session.stop()

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"/exit" in written


async def test_crash_recovery_respawns_on_dead_proc(mock_transcript_reader, tmp_path: Path) -> None:
    dead_proc = _make_mock_proc()
    dead_proc.isalive.return_value = False

    recovered_proc = _make_mock_proc()
    recovered_proc.isalive.return_value = True

    spawn_count = 0

    def _spawn(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        return dead_proc if spawn_count == 1 else recovered_proc

    _real_sleep = asyncio.sleep

    async def _fast_sleep(*args, **kwargs):
        await _real_sleep(0)

    with (
        patch("hive.runtime.pty_session.PtyProcess") as mock_cls,
        patch("hive.runtime.pty_session.asyncio.sleep", side_effect=_fast_sleep),
        patch("hive.runtime.pty_session._STARTUP_QUIET_S", 0.001),
    ):
        mock_cls.spawn.side_effect = _spawn
        session = PtySession(model="sonnet", cwd=tmp_path)
        await session.start()
        await session.send("hello after crash")

    assert spawn_count >= 2  # initial spawn + at least one recovery spawn


async def test_trust_prompt_auto_accept_writes_carriage_return(tmp_path: Path) -> None:
    proc = _make_mock_proc([b"\xe2\x9d\xaf 1. Yes, I trust this folder\r\n"])
    with patch("hive.runtime.pty_session.PtyProcess") as mock_cls:
        mock_cls.spawn.return_value = proc
        session = PtySession(model="sonnet", cwd=tmp_path)
        await session.start()

    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"\r" in written


async def test_send_chunks_large_payload(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    mock_cls, proc = mock_spawn
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
