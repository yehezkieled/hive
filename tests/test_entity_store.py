"""Tests for the asyncpg-backed EntityStore."""

from pathlib import Path

from hive.bus.entity_store import EntityStore
from hive.models.entity import Entity, EntityState


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
