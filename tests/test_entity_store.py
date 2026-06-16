"""Tests for the asyncpg-backed EntityStore."""

from datetime import UTC, datetime
from pathlib import Path

from hive.bus.entity_store import EntityStore
from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead


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
    await entity_store.upsert(Entity(name="charlie", role="lead", model="haiku"))
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


async def test_hierarchy_columns_roundtrip(entity_store: EntityStore) -> None:
    """parent_name and team_name should survive upsert -> load."""
    backend_lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
    )
    await entity_store.upsert(backend_lead)

    frontend_lead = TeamLead(
        name="dev.frontend",
        team_name="frontend",
        maestro_name="dev",
    )
    await entity_store.upsert(frontend_lead)

    entities = await entity_store.all()
    assert len(entities) == 2
    names = {e.name for e in entities}
    assert names == {"dev.backend", "dev.frontend"}


async def test_permission_mode_roundtrip(entity_store: EntityStore) -> None:
    """permission_mode should survive upsert -> load."""
    e = Entity(name="test", role="lead", permission_mode="plan")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.permission_mode == "plan"


async def test_loop_mode_roundtrip(entity_store: EntityStore) -> None:
    """loop_mode should survive upsert -> load."""
    e = Entity(name="test", role="lead", loop_mode="ship-it")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.loop_mode == "ship-it"


async def test_current_priority_roundtrip(entity_store: EntityStore) -> None:
    """current_priority should survive upsert -> load."""
    e = Entity(name="test", role="lead", current_priority=0)
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.current_priority == 0


# -- last_activity_at (Sprint 10) --


async def test_last_activity_at_roundtrip(entity_store: EntityStore) -> None:
    """last_activity_at should survive upsert -> load."""
    ts = datetime.now(UTC)
    e = Entity(name="test", role="lead", last_activity_at=ts)
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.last_activity_at is not None
    # Allow small rounding difference from DB
    assert abs((loaded.last_activity_at - ts).total_seconds()) < 1


async def test_last_activity_at_null_roundtrip(entity_store: EntityStore) -> None:
    """Entities without last_activity_at should load back with None."""
    e = Entity(name="test", role="lead")
    await entity_store.upsert(e)

    loaded = await entity_store.load("test")
    assert loaded is not None
    assert loaded.last_activity_at is None


# -- awaiting_decision (Ticket 029: maestro→user decision channel) --


async def test_awaiting_decision_roundtrip(entity_store: EntityStore) -> None:
    """awaiting_decision should survive upsert -> load (Ticket 029).

    The flag marks an entity parked on a request_decision to the user; it must
    be durable so a Hive restart cannot make the entity forget it is waiting and
    get poked into acting unconfirmed.
    """
    e = Entity(name="dev", role="maestro")
    e.awaiting_decision = True
    await entity_store.upsert(e)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.awaiting_decision is True


async def test_awaiting_decision_defaults_false(entity_store: EntityStore) -> None:
    """A fresh entity loads back with awaiting_decision False."""
    await entity_store.upsert(Entity(name="dev", role="maestro"))

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.awaiting_decision is False


async def test_awaiting_decision_update(entity_store: EntityStore) -> None:
    """Clearing the flag (a user reply landed) survives a re-upsert."""
    e = Entity(name="dev", role="maestro")
    e.awaiting_decision = True
    await entity_store.upsert(e)

    e.awaiting_decision = False
    await entity_store.upsert(e)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.awaiting_decision is False


# -- purge_role (Ticket 018: retire the Worker entity) --


async def test_purge_role_removes_worker_rows(entity_store: EntityStore) -> None:
    """purge_role deletes every row for a retired role and returns the count.

    Guards startup against a leftover ``role='worker'`` row zombie-restoring
    as a bare Entity now that the Worker subclass is gone (Ticket 018).
    """
    # Two retired-role rows plus one live row that must survive.
    await entity_store.upsert(Entity(name="dev.backend.w1", role="worker", model="haiku"))
    await entity_store.upsert(Entity(name="dev.backend.w2", role="worker", model="haiku"))
    await entity_store.upsert(Entity(name="dev", role="maestro", model="sonnet"))

    removed = await entity_store.purge_role("worker")
    assert removed == 2

    # Only the non-worker row remains.
    remaining = await entity_store.all()
    assert [e.name for e in remaining] == ["dev"]


async def test_purge_role_absent_returns_zero(entity_store: EntityStore) -> None:
    """purge_role on a role with no rows removes nothing and returns 0."""
    await entity_store.upsert(Entity(name="dev", role="maestro", model="sonnet"))

    removed = await entity_store.purge_role("worker")
    assert removed == 0
    assert len(await entity_store.all()) == 1


async def test_phase_confirmation_fields_persist(entity_store: EntityStore) -> None:
    """Ticket 019 (ADR 0019): confirmed_with_user + phase_confirm survive a round-trip."""
    m = Maestro(name="dev", model="opus")
    m.confirmed_with_user = True
    m.phase_confirm = False
    await entity_store.upsert(m)

    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.confirmed_with_user is True
    assert loaded.phase_confirm is False


async def test_phase_confirmation_defaults_on_restore(entity_store: EntityStore) -> None:
    """A maestro stored with defaults restores unconfirmed with the gate armed."""
    await entity_store.upsert(Maestro(name="fresh", model="opus"))
    loaded = await entity_store.load("fresh")
    assert loaded is not None
    assert loaded.confirmed_with_user is False
    assert loaded.phase_confirm is True
