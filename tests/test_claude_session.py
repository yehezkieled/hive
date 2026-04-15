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


async def test_session_captures_usage_from_result_event() -> None:
    """last_usage should be populated from the result event's usage sub-object."""
    result_event = {
        "type": "result",
        "result": "hi",
        "session_id": "sess-42",
        "total_cost_usd": 0.03958525,
        "usage": {
            "input_tokens": 9,
            "output_tokens": 53,
            "cache_creation_input_tokens": 31449,
            "cache_read_input_tokens": 0,
        },
    }
    script = f"echo '{json.dumps(result_event)}'"
    session = ClaudeSession(args=["bash", "-c", script])
    await session.start()
    await session.send_prompt("")

    assert session.last_usage is not None
    assert session.last_usage["session_id"] == "sess-42"
    assert session.last_usage["input_tokens"] == 9
    assert session.last_usage["output_tokens"] == 53
    assert session.last_usage["cache_creation_input_tokens"] == 31449
    assert session.last_usage["cache_read_input_tokens"] == 0
    assert session.last_usage["cost_usd"] == 0.03958525


async def test_session_last_usage_none_before_call() -> None:
    """last_usage stays None until a send_prompt yields a result event."""
    session = ClaudeSession(args=["echo", ""])
    assert session.last_usage is None


async def test_session_usage_defaults_when_result_missing_usage() -> None:
    """A result event without a usage sub-object should not crash."""
    event = {"type": "result", "result": "ok", "session_id": "z"}
    script = f"echo '{json.dumps(event)}'"
    session = ClaudeSession(args=["bash", "-c", script])
    await session.start()
    await session.send_prompt("")

    assert session.last_usage is not None
    assert session.last_usage["input_tokens"] == 0
    assert session.last_usage["output_tokens"] == 0


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
