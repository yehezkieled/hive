"""Tests for the asyncpg-backed TaskStore."""

from hive.bus.task_store import TaskStore
from hive.models.task import TaskStatus


async def test_create_returns_task(task_store: TaskStore) -> None:
    task = await task_store.create(title="fix the thing", created_by="user:42")
    assert task.id > 0
    assert task.title == "fix the thing"
    assert task.status is TaskStatus.PENDING
    assert task.priority == 3
    assert task.created_by == "user:42"
    assert task.description is None
    assert task.assigned_to is None
    assert task.completed_at is None
    assert task.created_at is not None


async def test_create_with_description_and_assignment(task_store: TaskStore) -> None:
    task = await task_store.create(
        title="ship the release",
        description="cut 1.0, tag, push",
        priority=0,
        assigned_to="dev",
        created_by="user:42",
    )
    assert task.description == "cut 1.0, tag, push"
    assert task.priority == 0
    assert task.assigned_to == "dev"


async def test_get_returns_created_task(task_store: TaskStore) -> None:
    created = await task_store.create(title="task a", created_by="user:1")
    fetched = await task_store.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "task a"


async def test_get_missing_returns_none(task_store: TaskStore) -> None:
    assert await task_store.get(99999) is None


async def test_list_empty(task_store: TaskStore) -> None:
    assert await task_store.list() == []


async def test_list_all(task_store: TaskStore) -> None:
    await task_store.create(title="a", created_by="user")
    await task_store.create(title="b", created_by="user")
    await task_store.create(title="c", created_by="user")
    rows = await task_store.list()
    assert len(rows) == 3
    assert {r.title for r in rows} == {"a", "b", "c"}


async def test_list_filters_by_status(task_store: TaskStore) -> None:
    t1 = await task_store.create(title="pending one", created_by="user")
    t2 = await task_store.create(title="done one", created_by="user")
    await task_store.update_status(t2.id, TaskStatus.COMPLETED)

    pending = await task_store.list(status=TaskStatus.PENDING)
    assert [t.id for t in pending] == [t1.id]

    completed = await task_store.list(status=TaskStatus.COMPLETED)
    assert [t.id for t in completed] == [t2.id]


async def test_list_orders_by_priority(task_store: TaskStore) -> None:
    await task_store.create(title="backlog", priority=4, created_by="user")
    await task_store.create(title="urgent", priority=0, created_by="user")
    await task_store.create(title="normal", priority=2, created_by="user")

    rows = await task_store.list()
    assert [r.title for r in rows] == ["urgent", "normal", "backlog"]


async def test_list_respects_limit(task_store: TaskStore) -> None:
    for i in range(5):
        await task_store.create(title=f"t{i}", created_by="user")
    rows = await task_store.list(limit=3)
    assert len(rows) == 3


async def test_update_status_sets_completed_at(task_store: TaskStore) -> None:
    task = await task_store.create(title="finish me", created_by="user")
    assert task.completed_at is None

    await task_store.update_status(task.id, TaskStatus.COMPLETED)
    refreshed = await task_store.get(task.id)
    assert refreshed is not None
    assert refreshed.status is TaskStatus.COMPLETED
    assert refreshed.completed_at is not None


async def test_update_status_to_cancelled_leaves_completed_at_null(
    task_store: TaskStore,
) -> None:
    task = await task_store.create(title="nope", created_by="user")
    await task_store.update_status(task.id, TaskStatus.CANCELLED)

    refreshed = await task_store.get(task.id)
    assert refreshed is not None
    assert refreshed.status is TaskStatus.CANCELLED
    assert refreshed.completed_at is None


async def test_update_status_in_progress(task_store: TaskStore) -> None:
    task = await task_store.create(title="working", created_by="user")
    await task_store.update_status(task.id, TaskStatus.IN_PROGRESS)

    refreshed = await task_store.get(task.id)
    assert refreshed is not None
    assert refreshed.status is TaskStatus.IN_PROGRESS
    assert refreshed.completed_at is None
