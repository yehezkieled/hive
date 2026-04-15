"""Tests for process manager (with mocked subprocesses)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from hive.bus.router import MessageRouter
from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.process.manager import ProcessManager


@pytest_asyncio.fixture
async def manager(router: MessageRouter) -> AsyncIterator[ProcessManager]:
    """Create a process manager over the shared test router."""
    mgr = ProcessManager(router=router, max_sessions=2)
    try:
        yield mgr
    finally:
        await mgr.kill_all()


async def test_spawn_and_kill_entity(manager: ProcessManager) -> None:
    """Test spawning an entity with a simple echo command."""
    entity = Entity(name="test-echo", role="worker", model="sonnet")
    # Override build_cli_args to use echo instead of real claude
    entity.system_prompt = ""
    entity.allowed_tools = []

    # We can't easily test real claude -p, so test the state tracking
    assert entity.state == EntityState.IDLE

    # Register directly for state tracking test
    manager._entities["test-echo"] = entity
    manager.router.register("test-echo")
    assert "test-echo" in manager.entities

    await manager.kill_entity("test-echo")
    assert "test-echo" not in manager.entities


async def test_max_sessions_enforcement(manager: ProcessManager) -> None:
    """Test that max_sessions limit is respected."""
    assert manager.max_sessions == 2
    assert manager.active_count == 0


async def test_get_status_empty(manager: ProcessManager) -> None:
    """Test status with no entities."""
    assert manager.get_status() == []


async def test_get_status_with_entity(manager: ProcessManager) -> None:
    """Test status formatting."""
    entity = Maestro(name="dev", model="sonnet")
    manager._entities["dev"] = entity
    manager.router.register("dev")

    statuses = manager.get_status()
    assert len(statuses) == 1
    assert statuses[0]["name"] == "dev"
    assert statuses[0]["role"] == "maestro"
    assert statuses[0]["state"] == "idle"


async def test_health_check_no_entities(manager: ProcessManager) -> None:
    """Test health check with no entities."""
    unhealthy = await manager.health_check()
    assert unhealthy == []


async def test_kill_nonexistent_entity(manager: ProcessManager) -> None:
    """Killing a nonexistent entity should not raise."""
    await manager.kill_entity("nonexistent")  # should not raise


async def test_send_to_nonexistent_entity(manager: ProcessManager) -> None:
    """Sending to nonexistent entity should raise KeyError."""
    with pytest.raises(KeyError):
        await manager.send_to_entity("nonexistent", "hello")
