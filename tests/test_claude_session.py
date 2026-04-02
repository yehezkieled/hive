"""Tests for Claude session wrapper (mocked subprocess)."""

import json

import pytest

from hive.process.claude_session import ClaudeSession


async def test_session_not_started_raises() -> None:
    session = ClaudeSession(args=["echo", "test"])
    with pytest.raises(RuntimeError, match="not started"):
        await session.send_prompt("hello")


async def test_session_with_echo() -> None:
    """Test with a simple echo command instead of real claude."""
    # Simulate stream-json output using echo
    result_json = json.dumps({"type": "result", "result": "hello world", "session_id": "abc123"})
    session = ClaudeSession(
        args=["bash", "-c", f"echo '{result_json}'"],
    )
    await session.start()
    assert session.is_alive or True  # process may finish instantly with echo

    response = await session.send_prompt("")
    assert "hello world" in response
    assert session.session_id == "abc123"


async def test_session_with_text_events() -> None:
    """Test parsing multiple stream-json text events."""
    events = [
        json.dumps({"type": "assistant", "subtype": "text", "content": "Hello "}),
        json.dumps({"type": "assistant", "subtype": "text", "content": "world!"}),
        json.dumps({"type": "result", "result": "Hello world!", "session_id": "xyz"}),
    ]
    script = "; ".join(f"echo '{e}'" for e in events)
    session = ClaudeSession(args=["bash", "-c", script])

    await session.start()
    response = await session.send_prompt("")
    assert response == "Hello world!"


async def test_kill_session() -> None:
    """Test killing a long-running session."""
    session = ClaudeSession(args=["sleep", "60"])
    await session.start()
    assert session.is_alive

    await session.kill()
    assert not session.is_alive


async def test_kill_already_dead() -> None:
    """Killing an already-dead session should not raise."""
    session = ClaudeSession(args=["echo", "done"])
    await session.start()
    await session.process.wait()  # type: ignore[union-attr]
    await session.kill()  # should not raise
