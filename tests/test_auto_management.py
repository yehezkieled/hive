"""Tests for Sprint 10 auto-management features: daily summary formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from hive.bus.router import MessageRouter
from hive.models.maestro import Maestro
from hive.models.task import Task, TaskStatus
from hive.process.manager import ProcessManager
from hive.telegram.bridge import TelegramBridge


def _make_bridge(
    process_manager: ProcessManager,
    token_store: AsyncMock | None = None,
    task_store: AsyncMock | None = None,
    audit_log: AsyncMock | None = None,
) -> TelegramBridge:
    """Create a TelegramBridge with mocked stores for testing."""
    bridge = TelegramBridge(
        bot_token="fake",
        allowed_user_ids=[],
        process_manager=process_manager,
        default_maestro="dev",
        token_store=token_store,
        task_store=task_store,
        audit_log=audit_log,
    )
    return bridge


@pytest.fixture
def router() -> MessageRouter:
    store = MagicMock()
    return MessageRouter(store=store)


@pytest.fixture
def manager(router: MessageRouter) -> ProcessManager:
    return ProcessManager(router=router)


class TestDailySummary:
    """Test format_daily_summary output."""

    async def test_summary_includes_entities(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        bridge = _make_bridge(manager)
        summary = await bridge.format_daily_summary()

        assert "Daily Hive Summary" in summary
        assert "dev" in summary
        assert "maestro" in summary

    async def test_summary_includes_token_cost(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        token_store = AsyncMock()
        token_store.totals = AsyncMock(
            return_value={
                "input_tokens": 10000,
                "output_tokens": 5000,
                "call_count": 15,
                "cost_usd": 0.1234,
            }
        )

        bridge = _make_bridge(manager, token_store=token_store)
        summary = await bridge.format_daily_summary()

        assert "15 calls" in summary
        assert "10,000" in summary
        assert "$0.1234" in summary

    async def test_summary_includes_completed_tasks(self, manager: ProcessManager) -> None:
        task_store = AsyncMock()
        now = datetime.now(UTC)
        task_store.list = AsyncMock(
            return_value=[
                Task(
                    id=1,
                    title="Fix auth bug",
                    status=TaskStatus.COMPLETED,
                    priority=1,
                    created_by="user:123",
                    created_at=now - timedelta(hours=5),
                    completed_at=now - timedelta(hours=2),
                ),
                Task(
                    id=2,
                    title="Old task",
                    status=TaskStatus.COMPLETED,
                    priority=3,
                    created_by="user:123",
                    created_at=now - timedelta(days=5),
                    completed_at=now - timedelta(days=3),
                ),
            ]
        )

        bridge = _make_bridge(manager, task_store=task_store)
        summary = await bridge.format_daily_summary()

        assert "Tasks completed (24h): 1" in summary
        assert "Fix auth bug" in summary
        assert "Old task" not in summary

    async def test_summary_handles_empty_state(self, manager: ProcessManager) -> None:
        bridge = _make_bridge(manager)
        summary = await bridge.format_daily_summary()

        assert "Daily Hive Summary" in summary
        assert "Entities: 0 registered" in summary

    async def test_summary_includes_errors(self, manager: ProcessManager) -> None:
        audit_log = AsyncMock()
        audit_log.recent = AsyncMock(
            return_value=[
                {
                    "action": "entity.error",
                    "target": "dev.backend",
                    "timestamp": datetime.now(UTC) - timedelta(hours=1),
                    "details": {"phase": "spawn"},
                },
            ]
        )

        bridge = _make_bridge(manager, audit_log=audit_log)
        summary = await bridge.format_daily_summary()

        assert "Errors (24h): 1" in summary
        assert "dev.backend" in summary
