"""Tests for /heartbeat command and format_heartbeat()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def make_bridge(enabled: bool = False, interval: int = 30) -> object:
    """Create a minimal TelegramBridge stub for unit tests."""
    from hive.telegram.bridge import TelegramBridge

    manager = MagicMock()
    manager.get_status.return_value = []
    bridge = TelegramBridge.__new__(TelegramBridge)
    bridge.process_manager = manager
    bridge.heartbeat_enabled = enabled
    bridge.heartbeat_interval_minutes = interval
    bridge._send_notification = AsyncMock()
    return bridge


class TestExecuteHeartbeat:
    async def test_on_enables(self) -> None:
        bridge = make_bridge(enabled=False)
        result = await bridge._execute_heartbeat("on", "")
        assert bridge.heartbeat_enabled is True
        assert "enabled" in result

    async def test_off_disables(self) -> None:
        bridge = make_bridge(enabled=True)
        result = await bridge._execute_heartbeat("off", "")
        assert bridge.heartbeat_enabled is False
        assert "disabled" in result.lower()

    async def test_on_with_minutes_sets_interval(self) -> None:
        bridge = make_bridge()
        await bridge._execute_heartbeat("on", "15")
        assert bridge.heartbeat_interval_minutes == 15

    async def test_integer_sets_interval(self) -> None:
        bridge = make_bridge()
        result = await bridge._execute_heartbeat("60", "")
        assert bridge.heartbeat_interval_minutes == 60
        assert "60" in result

    async def test_status_reports_state(self) -> None:
        bridge = make_bridge(enabled=True, interval=45)
        result = await bridge._execute_heartbeat("status", "")
        assert "enabled" in result
        assert "45" in result

    async def test_unknown_returns_usage(self) -> None:
        bridge = make_bridge()
        result = await bridge._execute_heartbeat("banana", "")
        assert "Usage" in result


class TestFormatHeartbeat:
    def test_no_agents(self) -> None:
        bridge = make_bridge()
        result = bridge.format_heartbeat()
        assert "No agents registered" in result

    def test_with_running_agent(self) -> None:
        bridge = make_bridge(interval=30)
        bridge.process_manager.get_status.return_value = [
            {
                "name": "dev",
                "role": "maestro",
                "state": "running",
                "alive": True,
                "uptime": 3720,  # 1h 2m
                "pid": 123,
                "model": "sonnet",
            }
        ]
        result = bridge.format_heartbeat()
        assert "dev" in result
        assert "RUNNING" in result
        assert "1h 2m" in result

    def test_error_agent_flagged(self) -> None:
        bridge = make_bridge()
        bridge.process_manager.get_status.return_value = [
            {
                "name": "dev",
                "role": "maestro",
                "state": "error",
                "alive": False,
                "uptime": None,
                "pid": None,
                "model": "sonnet",
            }
        ]
        result = bridge.format_heartbeat()
        assert "error" in result.lower()
