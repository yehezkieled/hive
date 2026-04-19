"""Tests for the asyncpg-backed EntityStore."""

from datetime import UTC, datetime
from pathlib import Path

from hive.bus.entity_store import EntityStore
from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent


async def test_upsert_and_load(entity_store: EntityStore) -> None:
    entity = Entity(name="dev", role="maestro", model="sonnet")
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.name == "dev"
    assert loaded.role == "maestro"
    assert loaded.model == "sonnet"


async def test_load_missing_returns_none(entity_store: EntityStore) -> None:
    assert await entity_store.load("nobody") is None


async def test_upsert_updates_existing(entity_store: EntityStore) -> None:
    entity = Entity(name="dev", role="maestro", model="sonnet")
    await entity_store.upsert(entity)

    # Change something and upsert again
    entity.model = "opus"
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.model == "opus"

    # Should still be only one row
    all_entities = await entity_store.all()
    assert len(all_entities) == 1


async def test_restored_entity_is_idle(entity_store: EntityStore) -> None:
    """Restored entities always come back IDLE, regardless of stored state.

    A RUNNING entity at shutdown is a dead PID on restart — forcing IDLE on
    load lets the next spawn take the IDLE -> STARTING -> RUNNING path.
    """
    entity = Entity(name="dev", role="maestro", model="sonnet")
    entity.state = EntityState.RUNNING
    entity.pid = 12345
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.state == EntityState.IDLE
    assert loaded.pid is None
    assert loaded.started_at is None


async def test_all_returns_sorted(entity_store: EntityStore) -> None:
    await entity_store.upsert(Entity(name="charlie", role="worker", model="haiku"))
    await entity_store.upsert(Entity(name="alice", role="maestro", model="sonnet"))
    await entity_store.upsert(Entity(name="bob", role="lead", model="sonnet"))

    entities = await entity_store.all()
    assert [e.name for e in entities] == ["alice", "bob", "charlie"]


async def test_all_empty(entity_store: EntityStore) -> None:
    assert await entity_store.all() == []


async def test_delete(entity_store: EntityStore) -> None:
    await entity_store.upsert(Entity(name="dev", role="maestro", model="sonnet"))
    await entity_store.delete("dev")

    assert await entity_store.load("dev") is None
    assert await entity_store.all() == []


async def test_delete_missing_is_noop(entity_store: EntityStore) -> None:
    # Should not raise
    await entity_store.delete("nobody")


async def test_personality_path_roundtrip(entity_store: EntityStore, tmp_path: Path) -> None:
    personality = tmp_path / "maestro-dev.md"
    personality.write_text("# Dev\n")

    entity = Entity(
        name="dev",
        role="maestro",
        model="sonnet",
        personality_path=personality,
    )
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.personality_path == personality


async def test_null_personality_path(entity_store: EntityStore) -> None:
    entity = Entity(name="dev", role="maestro", model="sonnet", personality_path=None)
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.personality_path is None


async def test_session_id_roundtrip(entity_store: EntityStore) -> None:
    """session_id should survive upsert -> load."""
    entity = Entity(name="dev", role="maestro", model="sonnet")
    entity.session_id = "sess-abc-123"
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.session_id == "sess-abc-123"


async def test_session_id_null_roundtrip(entity_store: EntityStore) -> None:
    """Entities without a session_id should load back with None."""
    entity = Entity(name="dev", role="maestro", model="sonnet")
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.session_id is None


async def test_session_id_update(entity_store: EntityStore) -> None:
    """Upsert should update session_id when it changes."""
    entity = Entity(name="dev", role="maestro", model="sonnet")
    entity.session_id = "sess-1"
    await entity_store.upsert(entity)

    entity.session_id = "sess-2"
    await entity_store.upsert(entity)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.session_id == "sess-2"


# -- Polymorphic restoration (Sprint 3a Phase 2) --


async def test_load_maestro_returns_maestro_instance(entity_store: EntityStore) -> None:
    """Loading an entity with role='maestro' should return a Maestro."""
    await entity_store.upsert(Maestro(name="dev", model="sonnet"))
    loaded = await entity_store.load("dev")
    assert isinstance(loaded, Maestro)


async def test_load_lead_returns_team_lead_instance(entity_store: EntityStore) -> None:
    """Loading an entity with role='lead' should return a TeamLead."""
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
    )
    await entity_store.upsert(lead)
    loaded = await entity_store.load("dev.backend")
    assert isinstance(loaded, TeamLead)
    assert loaded.team_name == "backend"
    assert loaded.maestro_name == "dev"


async def test_load_worker_returns_worker_instance(entity_store: EntityStore) -> None:
    """Loading an entity with role='worker' should return a WorkerAgent."""
    worker = WorkerAgent(
        name="dev.backend.w1",
        team_name="backend",
        lead_name="dev.backend",
    )
    await entity_store.upsert(worker)
    loaded = await entity_store.load("dev.backend.w1")
    assert isinstance(loaded, WorkerAgent)
    assert loaded.team_name == "backend"
    assert loaded.lead_name == "dev.backend"


async def test_hierarchy_columns_roundtrip(entity_store: EntityStore) -> None:
    """parent_name and team_name should survive upsert -> load."""
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
    )
    await entity_store.upsert(lead)

    worker = WorkerAgent(
        name="dev.backend.w1",
        team_name="backend",
        lead_name="dev.backend",
    )
    await entity_store.upsert(worker)

    entities = await entity_store.all()
    assert len(entities) == 2
    names = {e.name for e in entities}
    assert names == {"dev.backend", "dev.backend.w1"}


async def test_permission_mode_roundtrip(entity_store: EntityStore) -> None:
    """permission_mode should survive upsert -> load."""
    e = Entity(name="test", role="worker", permission_mode="plan")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.permission_mode == "plan"


async def test_loop_mode_roundtrip(entity_store: EntityStore) -> None:
    """loop_mode should survive upsert -> load."""
    e = Entity(name="test", role="worker", loop_mode="ship-it")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.loop_mode == "ship-it"


async def test_current_priority_roundtrip(entity_store: EntityStore) -> None:
    """current_priority should survive upsert -> load."""
    e = Entity(name="test", role="worker", current_priority=0)
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.current_priority == 0


# -- last_activity_at (Sprint 10) --


async def test_last_activity_at_roundtrip(entity_store: EntityStore) -> None:
    """last_activity_at should survive upsert -> load."""
    ts = datetime.now(UTC)
    e = Entity(name="test", role="worker", last_activity_at=ts)
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.last_activity_at is not None
    # Allow small rounding difference from DB
    assert abs((loaded.last_activity_at - ts).total_seconds()) < 1


async def test_last_activity_at_null_roundtrip(entity_store: EntityStore) -> None:
    """Entities without last_activity_at should load back with None."""
    e = Entity(name="test", role="worker")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.last_activity_at is None
