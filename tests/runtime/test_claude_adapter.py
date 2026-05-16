"""Tests for ClaudeAdapter step 1 (wraps ClaudeSession subprocess)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig
from hive.process.claude_session import ClaudeSession


def _make_session(text: str, session_id: str = "s1", usage: dict | None = None) -> ClaudeSession:
    """Build a ClaudeSession that echoes a fake stream-json result event."""
    raw_usage = usage or {"input_tokens": 10, "output_tokens": 5}
    event = {
        "type": "result",
        "result": text,
        "session_id": session_id,
        "usage": raw_usage,
    }
    return ClaudeSession(args=["bash", "-c", f"echo '{json.dumps(event)}'"])


def _factory(session: ClaudeSession):
    """Session factory that always returns the pre-built session."""

    def factory(args: list[str], cwd: Path | None) -> ClaudeSession:
        return session

    return factory


def _config(**kwargs) -> ClaudeAdapterConfig:
    defaults = dict(
        model="sonnet",
        system_prompt="",
        allowed_tools=[],
        disallowed_tools=[],
        permission_mode="default",
        loop_mode="ralph",
        role="worker",
        name="alice",
        mcp_config_path=None,
    )
    defaults.update(kwargs)
    return ClaudeAdapterConfig(**defaults)


async def test_send_turn_returns_text_and_usage() -> None:
    session = _make_session("Hello from Claude")
    adapter = ClaudeAdapter(_config(), session_factory=_factory(session))
    await adapter.start()

    text, usage = await adapter.send_turn("hi")

    assert text == "Hello from Claude"
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5


async def test_initial_session_id_used_as_resume_on_first_turn() -> None:
    captured_args: list[list[str]] = []

    def capturing_factory(args: list[str], cwd: Path | None) -> ClaudeSession:
        captured_args.append(args[:])
        return _make_session("ok", session_id="sess-new")

    adapter = ClaudeAdapter(
        _config(), session_factory=capturing_factory, initial_session_id="sess-prior"
    )
    await adapter.send_turn("first prompt")

    assert "--resume" in captured_args[0]
    assert "sess-prior" in captured_args[0]


async def test_send_turn_passes_resume_flag_after_first_turn() -> None:
    captured_args: list[list[str]] = []

    def capturing_factory(args: list[str], cwd: Path | None) -> ClaudeSession:
        captured_args.append(args[:])
        return _make_session("ok", session_id="sess-abc")

    adapter = ClaudeAdapter(_config(), session_factory=capturing_factory)
    await adapter.send_turn("first prompt")  # establishes session_id
    await adapter.send_turn("second prompt")  # should include --resume

    assert "--resume" not in " ".join(captured_args[0])
    assert "--resume" in captured_args[1]
    assert "sess-abc" in captured_args[1]


def test_build_args_includes_model() -> None:
    adapter = ClaudeAdapter(_config(model="opus"))
    args = adapter._build_args()
    assert "--model" in args
    assert args[args.index("--model") + 1] == "opus"


def test_build_args_includes_allowed_tools() -> None:
    adapter = ClaudeAdapter(_config(allowed_tools=["Bash", "Read"]))
    args = adapter._build_args()
    assert "--allowedTools" in args
    idx = args.index("--allowedTools")
    assert "Bash" in args[idx:]
    assert "Read" in args[idx:]


def test_build_args_dangerous_mode_skips_permissions() -> None:
    adapter = ClaudeAdapter(_config(permission_mode="yolo"))
    args = adapter._build_args()
    assert "--dangerously-skip-permissions" in args
    assert "--permission-mode" not in args


def test_is_alive_returns_true_in_step_1() -> None:
    adapter = ClaudeAdapter(_config())
    assert adapter.is_alive() is True


async def test_pty_mode_start_spawns_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as MockPtySession:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        MockPtySession.return_value = mock_pty

        adapter = ClaudeAdapter(_config(), use_pty=True)
        await adapter.start()

        MockPtySession.assert_called_once()
        mock_pty.start.assert_awaited_once()


async def test_pty_mode_send_turn_returns_text() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as MockPtySession:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        mock_pty.send.return_value = "PTY response text"
        MockPtySession.return_value = mock_pty

        adapter = ClaudeAdapter(_config(), use_pty=True)
        await adapter.start()
        text, usage = await adapter.send_turn("hello via pty")

    assert text == "PTY response text"
    assert "input_tokens" in usage
    assert usage["session_id"] is None


async def test_pty_mode_stop_terminates_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as MockPtySession:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        MockPtySession.return_value = mock_pty

        adapter = ClaudeAdapter(_config(), use_pty=True)
        await adapter.start()
        await adapter.stop()

        mock_pty.stop.assert_awaited_once()


async def test_pty_mode_is_alive_delegates_to_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as MockPtySession:
        mock_pty = MagicMock()
        mock_pty.is_alive.return_value = False
        MockPtySession.return_value = mock_pty

        adapter = ClaudeAdapter(_config(), use_pty=True)
        adapter._pty = mock_pty  # inject without calling start()

        assert adapter.is_alive() is False
        mock_pty.is_alive.assert_called()


async def test_usage_dict_has_all_expected_keys() -> None:
    session = _make_session(
        "response",
        usage={
            "input_tokens": 3,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    )
    adapter = ClaudeAdapter(_config(), session_factory=_factory(session))
    _, usage = await adapter.send_turn("hello")

    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "session_id",
        "cost_usd",
    ):
        assert key in usage, f"Missing key: {key}"
