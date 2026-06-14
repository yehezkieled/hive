"""/project command surface (Ticket 024, Slice A).

Exercises parsing/routing + messaging against an in-memory fake store; the
registry's rejection logic itself is covered in test_project_store.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hive.commands.dispatch import CommandDispatcher
from hive.models.project import Project, ProjectOwnershipError


class FakeProjectStore:
    def __init__(self) -> None:
        self.projects: list[Project] = []
        self.upserts: list[Project] = []
        self.assigns: list[tuple[str, str]] = []
        self.assign_error: str | None = None

    async def all(self) -> list[Project]:
        return list(self.projects)

    async def upsert(self, project: Project) -> None:
        self.upserts.append(project)

    async def assign(self, name: str, maestro: str) -> None:
        if self.assign_error:
            raise ProjectOwnershipError(self.assign_error)
        self.assigns.append((name, maestro))


def _dispatcher(store: object) -> CommandDispatcher:
    return CommandDispatcher(process_manager=SimpleNamespace(project_store=store))


async def test_project_list_empty() -> None:
    result = await _dispatcher(FakeProjectStore()).dispatch("/project list")
    assert "No projects registered" in result.text


async def test_project_list_shows_owner_and_ownerless() -> None:
    store = FakeProjectStore()
    store.projects = [
        Project(name="acme", root_path=Path("/p/acme"), owning_maestro="acme-lead"),
        Project(name="scratch", root_path=Path("/p/scratch")),
    ]
    text = (await _dispatcher(store).dispatch("/project list")).text
    assert "owner: acme-lead" in text
    assert "ownerless" in text


async def test_project_new_ownerless_upserts() -> None:
    store = FakeProjectStore()
    result = await _dispatcher(store).dispatch("/project new acme /p/acme")
    assert [p.name for p in store.upserts] == ["acme"]
    assert store.upserts[0].root_path == Path("/p/acme")
    assert store.assigns == []
    assert "ownerless" in result.text


async def test_project_new_with_maestro_assigns() -> None:
    store = FakeProjectStore()
    result = await _dispatcher(store).dispatch("/project new acme /p/acme acme-lead")
    assert store.assigns == [("acme", "acme-lead")]
    assert "acme-lead" in result.text


async def test_project_assign_rejects_second_maestro() -> None:
    store = FakeProjectStore()
    store.assign_error = "project 'acme' is already owned by 'm1'"
    result = await _dispatcher(store).dispatch("/project assign acme m2")
    assert "Error:" in result.text
    assert "already owned" in result.text


async def test_project_new_usage_on_missing_args() -> None:
    result = await _dispatcher(FakeProjectStore()).dispatch("/project new acme")
    assert "Usage:" in result.text


async def test_project_unavailable_without_store() -> None:
    result = await _dispatcher(None).dispatch("/project list")
    assert "unavailable" in result.text
