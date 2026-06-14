"""Tests for the asyncpg-backed ProjectStore (Ticket 024)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hive.bus.project_store import ProjectStore
from hive.models.project import Project, ProjectOwnershipError


async def test_upsert_and_load_round_trips(project_store: ProjectStore) -> None:
    project = Project(
        name="acme",
        root_path=Path("/home/hezki/projects/acme"),
        owning_maestro="dev",
    )
    await project_store.upsert(project)

    loaded = await project_store.load("acme")
    assert loaded is not None
    assert loaded.name == "acme"
    assert loaded.root_path == Path("/home/hezki/projects/acme")
    assert loaded.owning_maestro == "dev"
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


async def test_load_missing_returns_none(project_store: ProjectStore) -> None:
    assert await project_store.load("nobody") is None


async def test_upsert_twice_updates_row(project_store: ProjectStore) -> None:
    await project_store.upsert(Project(name="acme", root_path=Path("/projects/acme")))
    # Same name, different root_path → updates in place (idempotent on PK).
    await project_store.upsert(Project(name="acme", root_path=Path("/projects/acme-moved")))

    loaded = await project_store.load("acme")
    assert loaded is not None
    assert loaded.root_path == Path("/projects/acme-moved")


async def test_by_root_path_matches_and_misses(project_store: ProjectStore) -> None:
    await project_store.upsert(Project(name="acme", root_path=Path("/projects/acme")))

    found = await project_store.by_root_path("/projects/acme")
    assert found is not None
    assert found.name == "acme"

    # Path argument is accepted too.
    found_path = await project_store.by_root_path(Path("/projects/acme"))
    assert found_path is not None
    assert found_path.name == "acme"

    assert await project_store.by_root_path("/projects/nope") is None


async def test_owned_roots_only_owned_sorted(project_store: ProjectStore) -> None:
    await project_store.upsert(
        Project(name="zeta", root_path=Path("/projects/zeta"), owning_maestro="m2")
    )
    await project_store.upsert(
        Project(name="acme", root_path=Path("/projects/acme"), owning_maestro="m1")
    )
    # Ownerless — must NOT appear in owned_roots.
    await project_store.upsert(Project(name="free", root_path=Path("/projects/free")))

    assert await project_store.owned_roots() == [
        "/projects/acme",
        "/projects/zeta",
    ]


async def test_for_maestro_returns_owned_or_none(
    project_store: ProjectStore,
) -> None:
    await project_store.upsert(
        Project(name="acme", root_path=Path("/projects/acme"), owning_maestro="dev")
    )
    await project_store.upsert(Project(name="free", root_path=Path("/projects/free")))

    owned = await project_store.for_maestro("dev")
    assert owned is not None
    assert owned.name == "acme"

    assert await project_store.for_maestro("nobody") is None


async def test_all_sorted_and_delete(project_store: ProjectStore) -> None:
    await project_store.upsert(Project(name="zeta", root_path=Path("/p/zeta")))
    await project_store.upsert(Project(name="acme", root_path=Path("/p/acme")))

    names = [p.name for p in await project_store.all()]
    assert names == ["acme", "zeta"]

    await project_store.delete("acme")
    remaining = [p.name for p in await project_store.all()]
    assert remaining == ["zeta"]


async def test_assign_sets_ownership_when_free(
    project_store: ProjectStore,
) -> None:
    await project_store.upsert(Project(name="acme", root_path=Path("/projects/acme")))

    await project_store.assign("acme", "dev")

    loaded = await project_store.load("acme")
    assert loaded is not None
    assert loaded.owning_maestro == "dev"


async def test_assign_rejects_second_maestro_on_owned_project(
    project_store: ProjectStore,
) -> None:
    await project_store.upsert(
        Project(name="acme", root_path=Path("/projects/acme"), owning_maestro="dev")
    )

    with pytest.raises(ProjectOwnershipError):
        await project_store.assign("acme", "other")

    # Ownership unchanged.
    loaded = await project_store.load("acme")
    assert loaded is not None
    assert loaded.owning_maestro == "dev"


async def test_assign_rejects_maestro_owning_another_project(
    project_store: ProjectStore,
) -> None:
    await project_store.upsert(
        Project(name="acme", root_path=Path("/projects/acme"), owning_maestro="dev")
    )
    await project_store.upsert(Project(name="beta", root_path=Path("/projects/beta")))

    with pytest.raises(ProjectOwnershipError):
        await project_store.assign("beta", "dev")

    # beta stays ownerless.
    loaded = await project_store.load("beta")
    assert loaded is not None
    assert loaded.owning_maestro is None


async def test_assign_is_idempotent_for_same_owner(
    project_store: ProjectStore,
) -> None:
    await project_store.upsert(
        Project(name="acme", root_path=Path("/projects/acme"), owning_maestro="dev")
    )

    # Re-assigning the same maestro to the project it already owns is a no-op,
    # not an error.
    await project_store.assign("acme", "dev")

    loaded = await project_store.load("acme")
    assert loaded is not None
    assert loaded.owning_maestro == "dev"
