"""Tests for PtySession — PTY-based Claude Code session manager."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.runtime.pty_session import (
    PtySession,
    _build_spawn_args,
    _claude_projects_dir,
    _resolve_claude_version,
)


def _user_line(text: str, session_id: str) -> str:
    """One transcript line: a user entry (the shape CC writes on input)."""
    entry = {
        "type": "user",
        "sessionId": session_id,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return json.dumps(entry) + "\n"


def _assistant_line(text: str, session_id: str) -> str:
    """One transcript line: a completed assistant entry with usage."""
    entry = {
        "type": "assistant",
        "sessionId": session_id,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }
    return json.dumps(entry) + "\n"


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
        mock_reader.resolve_session.return_value = Path("/tmp/fake-session.jsonl")
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


async def test_send_resolves_session_only_on_first_call(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    """Session resolution should run on the FIRST send() only; cached thereafter."""
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()

    await session.send("first")
    await session.send("second")
    await session.send("third")

    assert mock_transcript_reader.resolve_session.call_count == 1


async def test_send_handles_plan_gate_then_resumes(mock_spawn, tmp_path: Path) -> None:
    """On a Gated outcome, send() resolves the gate, injects the keys, and
    resumes awaiting the real assistant turn — no hang, same Turn continues."""
    from hive.runtime.gates import Gate
    from hive.runtime.transcript_reader import Gated

    mock_cls, proc = mock_spawn

    # The reader returns Gated first, then the real completed turn on the
    # second await (after the keypress is injected).
    plan_gate = Gate(kind="plan", payload={"plan": "1. ship it"})
    real_turn = (
        "plan executed",
        {
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "session_id": "sess-gate",
        },
    )

    with patch("hive.runtime.pty_session.TranscriptReader") as mock_reader_cls:
        mock_reader = mock_reader_cls.return_value
        mock_reader.resolve_session.return_value = Path("/tmp/fake.jsonl")
        mock_reader.await_next_assistant_turn = AsyncMock(side_effect=[Gated(plan_gate), real_turn])

        # Coordinator resolves the gate to the approve keypress.
        coordinator = MagicMock()
        coordinator.resolve = AsyncMock(return_value=["\r"])

        states: list[str] = []
        session = PtySession(
            model="sonnet",
            cwd=tmp_path,
            gate_coordinator=coordinator,
            entity_name="dev",
            on_gate_state=lambda name, state: states.append(state),
        )
        await session.start()
        text, usage = await session.send("plan something")

    assert text == "plan executed"
    assert usage["session_id"] == "sess-gate"
    # The gate was resolved exactly once for this entity.
    coordinator.resolve.assert_awaited_once()
    assert coordinator.resolve.call_args.args[0] == "dev"
    # State went GATED then back to RUNNING (resume).
    assert states == ["gated", "running"]
    # The approve keypress reached the PTY.
    written = b"".join(c[0][0] for c in proc.write.call_args_list)
    assert b"\r" in written


async def test_send_without_coordinator_keeps_two_outcome_contract(
    mock_spawn, mock_transcript_reader, tmp_path: Path
) -> None:
    """No coordinator wired → send() behaves exactly as before (text, usage)."""
    session = PtySession(model="sonnet", cwd=tmp_path)
    await session.start()
    text, usage = await session.send("hi")
    assert text == "canned response"
    assert usage["session_id"] == "sess-mock"


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


# --- Claude binary resolution + version logging (Ticket 009) -----------------


def test_resolve_claude_version_reads_symlink_target(tmp_path: Path) -> None:
    """Native-install happy path: a symlink resolves to versions/<X>; basename is the version.

    Mirrors ~/.local/bin/claude -> ~/.local/share/claude/versions/2.1.162 with no
    subprocess — the cheap path resolves the real symlink on disk.
    """
    versions = tmp_path / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    version_file = versions / "2.1.162"
    version_file.write_text("#!/bin/sh\n")  # stand-in for the real binary
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    symlink = bin_dir / "claude"
    symlink.symlink_to(version_file)

    path, version = _resolve_claude_version(str(symlink))

    assert version == "2.1.162"
    assert path == str(version_file)


def test_resolve_claude_version_falls_back_to_subprocess(tmp_path: Path) -> None:
    """Non-version basename (e.g. an npm wrapper) shells out to `claude --version`."""
    binary = tmp_path / "claude"  # real file; basename "claude" is not a version
    binary.write_text("#!/bin/sh\n")

    with patch("hive.runtime.pty_session.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="2.1.140 (Claude Code)\n", returncode=0)
        path, version = _resolve_claude_version(str(binary))

    assert version == "2.1.140"
    assert path == str(binary)
    # the probe targets the same binary we resolve
    assert mock_run.call_args[0][0][0] == str(binary)
    assert "--version" in mock_run.call_args[0][0]


def test_resolve_claude_version_subprocess_failure_returns_unknown(tmp_path: Path) -> None:
    """A timed-out / failed version probe degrades to "unknown" — never crashes a spawn."""
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")

    with patch(
        "hive.runtime.pty_session.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5.0),
    ):
        path, version = _resolve_claude_version(str(binary))

    assert version == "unknown"
    assert path == str(binary)


def test_build_spawn_args_uses_configured_binary(monkeypatch) -> None:
    """argv[0] is the configured CLAUDE_BINARY, not a hardcoded bare 'claude'."""
    monkeypatch.setattr("hive.runtime.pty_session.CLAUDE_BINARY", "/home/hezki/.local/bin/claude")
    args = _build_spawn_args("opus", None, [], [])
    assert args[0] == "/home/hezki/.local/bin/claude"


def test_build_spawn_args_defaults_to_bare_claude(monkeypatch) -> None:
    """Unset knob keeps the legacy bare-'claude' PATH lookup (back-compatible)."""
    monkeypatch.setattr("hive.runtime.pty_session.CLAUDE_BINARY", "claude")
    args = _build_spawn_args("opus", None, [], [])
    assert args[0] == "claude"


async def test_start_logs_resolved_claude_version(mock_spawn, tmp_path: Path, caplog) -> None:
    """Every spawn logs the entity, resolved version, and binary path (drift is visible)."""
    mock_cls, proc = mock_spawn
    session = PtySession(model="sonnet", cwd=tmp_path, entity_name="worker-3")

    with patch(
        "hive.runtime.pty_session._resolve_claude_version",
        return_value=("/home/hezki/.local/share/claude/versions/2.1.162", "2.1.162"),
    ):
        with caplog.at_level(logging.INFO, logger="hive.runtime.pty_session"):
            await session.start()

    assert any(
        "worker-3" in m and "2.1.162" in m and "versions/2.1.162" in m for m in caplog.messages
    )


# --- Session pinning (Ticket 023, ADR 0011) ----------------------------------


async def test_send_reads_pinned_transcript_while_decoy_grows(tmp_path: Path) -> None:
    """F3 reproduction (ADR 0011): read the pinned transcript, never a dir guess.

    Two sessions share one project dir. A DECOY .jsonl grows first — exactly
    the bait the new-or-growing heuristic bound to in the 2026-06-11 smoke
    test, silently feeding the adapter a sibling session's turns. With the
    pid-state file present (~/.claude/sessions/<pid>.json carrying sessionId),
    send() must return the turn from <project_dir>/<sessionId>.jsonl — and
    attribute it to the pinned session.
    """
    projects_dir = _claude_projects_dir(tmp_path)
    projects_dir.mkdir(parents=True, exist_ok=True)
    decoy = projects_dir / "decoy.jsonl"
    pinned = projects_dir / "pin-sess.jsonl"
    decoy.write_text(_user_line("sibling chatter", "decoy-sess"))
    pinned.write_text(_user_line("kickoff", "pin-sess"))

    pid = 4242
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "sessionId": "pin-sess", "status": "busy"})
    )

    proc = _make_mock_proc()
    proc.pid = pid

    async def writer() -> None:
        # The decoy grows FIRST — under the old heuristic this wins the bind.
        await asyncio.sleep(0.9)
        with decoy.open("a", encoding="utf-8") as fh:
            fh.write(_user_line("more sibling chatter", "decoy-sess"))
        await asyncio.sleep(0.3)
        with decoy.open("a", encoding="utf-8") as fh:
            fh.write(_assistant_line("WRONG — a sibling session's answer", "decoy-sess"))
        await asyncio.sleep(0.3)
        with pinned.open("a", encoding="utf-8") as fh:
            fh.write(_assistant_line("the real answer", "pin-sess"))

    try:
        with patch("hive.runtime.pty_session.PtyProcess") as mock_cls:
            mock_cls.spawn.return_value = proc
            session = PtySession(model="sonnet", cwd=tmp_path, sessions_dir=sessions_dir)
            await session.start()
            write_task = asyncio.create_task(writer())
            text, usage = await session.send("do the work")
            await write_task
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)

    assert text == "the real answer"
    assert usage["session_id"] == "pin-sess"


async def test_send_falls_back_to_heuristic_when_pid_state_file_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing/late pid-state file → directory heuristic + loud WARNING.

    Claude Code's sessions file is an undocumented interface (ADR 0011); if
    it vanishes in an upgrade Hive must keep working. The poll times out, the
    reader falls back to the new-or-growing heuristic, and the bind is logged
    loudly so a silent mis-bind is diagnosable from the journal.
    """
    projects_dir = _claude_projects_dir(tmp_path)
    projects_dir.mkdir(parents=True, exist_ok=True)
    transcript = projects_dir / "only-session.jsonl"
    transcript.write_text(_user_line("kickoff", "sess-fb"))

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()  # exists, but holds no <pid>.json

    proc = _make_mock_proc()
    proc.pid = 4243

    async def writer() -> None:
        # The transcript grows on input (the --continue shape the heuristic
        # was written for), then the assistant answer lands.
        await asyncio.sleep(1.0)
        with transcript.open("a", encoding="utf-8") as fh:
            fh.write(_user_line("do the work", "sess-fb"))
        await asyncio.sleep(0.8)
        with transcript.open("a", encoding="utf-8") as fh:
            fh.write(_assistant_line("answer via fallback", "sess-fb"))

    try:
        with (
            patch("hive.runtime.pty_session.PtyProcess") as mock_cls,
            patch("hive.runtime.pty_session._PIN_POLL_TIMEOUT_S", 0.2),
        ):
            mock_cls.spawn.return_value = proc
            session = PtySession(model="sonnet", cwd=tmp_path, sessions_dir=sessions_dir)
            await session.start()
            write_task = asyncio.create_task(writer())
            with caplog.at_level(logging.WARNING):
                text, usage = await session.send("do the work")
            await write_task
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)

    assert text == "answer via fallback"
    assert usage["session_id"] == "sess-fb"
    assert any("falling back to directory heuristic" in m for m in caplog.messages)


@pytest.mark.parametrize(
    "state_content",
    [
        pytest.param("not json {{{", id="malformed-json"),
        pytest.param(json.dumps({"pid": 4244, "status": "busy"}), id="missing-sessionId"),
    ],
)
async def test_send_falls_back_when_pid_state_file_unusable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, state_content: str
) -> None:
    """A present-but-unusable pid-state file must degrade to the fallback.

    Undocumented interface (ADR 0011): a CC upgrade could garble the file or
    drop the sessionId key. Either way the spawn must not crash — heuristic
    bind + loud warning, same as a missing file.
    """
    projects_dir = _claude_projects_dir(tmp_path)
    projects_dir.mkdir(parents=True, exist_ok=True)
    transcript = projects_dir / "only-session.jsonl"
    transcript.write_text(_user_line("kickoff", "sess-mal"))

    pid = 4244
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / f"{pid}.json").write_text(state_content)

    proc = _make_mock_proc()
    proc.pid = pid

    async def writer() -> None:
        await asyncio.sleep(1.0)
        with transcript.open("a", encoding="utf-8") as fh:
            fh.write(_user_line("do the work", "sess-mal"))
        await asyncio.sleep(0.8)
        with transcript.open("a", encoding="utf-8") as fh:
            fh.write(_assistant_line("survived the bad state file", "sess-mal"))

    try:
        with (
            patch("hive.runtime.pty_session.PtyProcess") as mock_cls,
            patch("hive.runtime.pty_session._PIN_POLL_TIMEOUT_S", 0.2),
        ):
            mock_cls.spawn.return_value = proc
            session = PtySession(model="sonnet", cwd=tmp_path, sessions_dir=sessions_dir)
            await session.start()
            write_task = asyncio.create_task(writer())
            with caplog.at_level(logging.WARNING):
                text, usage = await session.send("do the work")
            await write_task
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)

    assert text == "survived the bad state file"
    assert usage["session_id"] == "sess-mal"
    assert any("falling back to directory heuristic" in m for m in caplog.messages)


async def test_respawn_repins_to_new_pid_session(tmp_path: Path) -> None:
    """The pin is per-PROCESS: a respawn (new pid) must re-pin, not reuse.

    ``--continue`` after a crash produces a new pid and may resume a prior
    session id (ADR 0011). A pin cached across the respawn would read a dead
    session's file forever. After recovery, send() must resolve the NEW pid's
    state file and read the NEW session's transcript.
    """
    projects_dir = _claude_projects_dir(tmp_path)
    projects_dir.mkdir(parents=True, exist_ok=True)
    transcript_a = projects_dir / "sess-A.jsonl"
    transcript_b = projects_dir / "sess-B.jsonl"
    transcript_a.write_text(_user_line("kickoff", "sess-A"))
    transcript_b.write_text(_user_line("kickoff", "sess-B"))

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "111.json").write_text(json.dumps({"pid": 111, "sessionId": "sess-A"}))
    (sessions_dir / "222.json").write_text(json.dumps({"pid": 222, "sessionId": "sess-B"}))

    proc_a = _make_mock_proc()
    proc_a.pid = 111
    proc_b = _make_mock_proc()
    proc_b.pid = 222
    procs = [proc_a, proc_b]

    def _spawn(*args, **kwargs):
        return procs.pop(0)

    async def append_later(path: Path, line: str, delay: float) -> None:
        await asyncio.sleep(delay)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    try:
        with patch("hive.runtime.pty_session.PtyProcess") as mock_cls:
            mock_cls.spawn.side_effect = _spawn
            session = PtySession(model="sonnet", cwd=tmp_path, sessions_dir=sessions_dir)
            await session.start()

            # Turn 1 on pid 111 → pinned to sess-A.
            writer_1 = asyncio.create_task(
                append_later(transcript_a, _assistant_line("answer from session A", "sess-A"), 1.0)
            )
            text_1, usage_1 = await session.send("first")
            await writer_1

            # The harness crashes; send() recovers (respawn → pid 222).
            proc_a.isalive.return_value = False
            # Recovery sleeps 2.0s before respawning; the new await starts
            # after inject (~2.6s in) — append well after that.
            writer_2 = asyncio.create_task(
                append_later(transcript_b, _assistant_line("answer from session B", "sess-B"), 3.5)
            )
            text_2, usage_2 = await session.send("second")
            await writer_2
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)

    assert text_1 == "answer from session A"
    assert usage_1["session_id"] == "sess-A"
    assert text_2 == "answer from session B"
    assert usage_2["session_id"] == "sess-B"
