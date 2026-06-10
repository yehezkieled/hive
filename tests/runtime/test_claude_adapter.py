"""Tests for ClaudeAdapter — the PTY-only Claude Code runtime.

The adapter drives one persistent ``PtySession`` per entity: it builds the
append-system-prompts and extra CLI args, forwards the interactive-gate bridge,
and turns each ``PtySession.send`` into a uniform ``(text, usage)`` result.
These tests mock ``hive.runtime.claude_adapter.PtySession``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig


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


def test_pty_extra_args_includes_strict_mcp_config() -> None:
    adapter = ClaudeAdapter(_config(mcp_config_path=Path("/tmp/hive-mcp-alice.json")))
    args = adapter._build_pty_extra_args()
    assert "--mcp-config" in args
    assert "--strict-mcp-config" in args


async def test_pty_mode_start_spawns_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty_cls:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        mock_pty_cls.return_value = mock_pty

        adapter = ClaudeAdapter(_config())
        await adapter.start()

        mock_pty_cls.assert_called_once()
        mock_pty.start.assert_awaited_once()


async def test_pty_mode_send_turn_returns_text() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty_cls:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        # PtySession.send now returns (text, usage) — usage from transcript.
        mock_pty.send.return_value = (
            "PTY response text",
            {
                "input_tokens": 5,
                "output_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "session_id": "sess-pty",
            },
        )
        mock_pty_cls.return_value = mock_pty

        adapter = ClaudeAdapter(_config())
        await adapter.start()
        text, usage = await adapter.send_turn("hello via pty")

    assert text == "PTY response text"
    assert usage["input_tokens"] == 5
    assert usage["output_tokens"] == 10
    assert usage["session_id"] == "sess-pty"
    assert usage["cost_usd"] is None  # plan-billed: no marginal dollar cost


async def test_pty_mode_stop_terminates_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty_cls:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        mock_pty_cls.return_value = mock_pty

        adapter = ClaudeAdapter(_config())
        await adapter.start()
        await adapter.stop()

        mock_pty.stop.assert_awaited_once()


async def test_pty_mode_is_alive_delegates_to_pty() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty_cls:
        mock_pty = MagicMock()
        mock_pty.is_alive.return_value = False
        mock_pty_cls.return_value = mock_pty

        adapter = ClaudeAdapter(_config())
        adapter._pty = mock_pty  # inject without calling start()

        assert adapter.is_alive() is False
        mock_pty.is_alive.assert_called()


async def test_usage_dict_has_all_expected_keys() -> None:
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty_cls:
        mock_pty = AsyncMock()
        mock_pty.is_alive.return_value = True
        mock_pty.send.return_value = (
            "response",
            {
                "input_tokens": 3,
                "output_tokens": 7,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "session_id": "sess-keys",
            },
        )
        mock_pty_cls.return_value = mock_pty

        adapter = ClaudeAdapter(_config())
        await adapter.start()
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


async def test_start_threads_gate_wiring_into_pty() -> None:
    """ClaudeAdapter forwards the interactive-gate wiring into the PtySession it
    builds, so the bridge is live in PTY mode (Ticket 003 runtime wiring)."""

    coordinator = MagicMock()

    def on_state(entity: str, state: str) -> None:
        pass

    adapter = ClaudeAdapter(
        _config(),
        gate_coordinator=coordinator,
        entity_name="dev",
        on_gate_state=on_state,
    )
    with patch("hive.runtime.claude_adapter.PtySession") as mock_pty:
        mock_pty.return_value.start = AsyncMock()
        await adapter.start()

    _, kwargs = mock_pty.call_args
    assert kwargs["gate_coordinator"] is coordinator
    assert kwargs["entity_name"] == "dev"
    assert kwargs["on_gate_state"] is on_state


async def test_is_busy_reflects_in_flight_turn() -> None:
    """is_busy() is True exactly while a turn holds the adapter lock —
    the idle reaper uses it to never kill an entity mid-turn (ADR 0010)."""
    adapter = ClaudeAdapter(_config())

    assert adapter.is_busy() is False
    async with adapter._lock:
        assert adapter.is_busy() is True
    assert adapter.is_busy() is False
