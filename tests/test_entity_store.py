"""Tests for the asyncpg-backed EntityStore."""

from datetime import UTC, datetime
from pathlib import Path

from hive.bus.entity_store import EntityStore
from hive.models.entity import Entity, EntityState
from hive.models.vault import Vault


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
    """Loading an entity with role='maestro' should return a Vault."""
    await entity_store.upsert(Vault(name="dev", model="sonnet"))
    loaded = await entity_store.load("dev")
    assert isinstance(loaded, Vault)


async def test_hierarchy_columns_roundtrip(entity_store: EntityStore) -> None:
    """parent_name and team_name should survive upsert -> load."""
    backend_lead = Vault(
        name="dev.backend",
    )
    await entity_store.upsert(backend_lead)

    frontend_lead = Vault(
        name="dev.frontend",
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


async def test_codex_usage_round_trips(entity_store: EntityStore) -> None:
    entity = Entity(name="dev", role="lead", session_id="codex-thread")
    entity.codex_usage = {
        "session_id": "codex-thread",
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "output_tokens": 20,
        "cache_write_input_tokens": 0,
    }
    await entity_store.upsert(entity)
    loaded = await entity_store.load("dev")
    assert loaded is not None
    assert loaded.codex_usage == entity.codex_usage


async def test_migration_035_retires_maestro_columns_and_projects(store) -> None:
    """The cut-over migration (ADR 0033) drops the retired columns and the projects table."""
    cols = {
        r["column_name"]
        for r in await store.pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'entities'"
        )
    }
    retired = {
        "parent_name",
        "team_name",
        "worktree_path",
        "task_id",
        "awaiting_decision",
        "confirmed_with_user",
        "phase_confirm",
        "last_decision_question",
    }
    assert not retired & cols
    assert await store.pool.fetchval("SELECT to_regclass('public.projects')") is None
