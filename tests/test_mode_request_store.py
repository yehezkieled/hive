"""Tests for ModeRequestStore — yolo/yotree approval flow persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hive.bus.mode_request_store import ModeRequestStore


async def test_create_pending_request(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="lead-backend",
        requested_mode="yotree",
        approver="dev",
        reason="refactor session manager",
    )
    assert req["id"] is not None
    assert req["status"] == "pending"
    assert req["requester"] == "lead-backend"
    assert req["requested_mode"] == "yotree"
    assert req["approver"] == "dev"
    assert req["reason"] == "refactor session manager"
    assert req["resolved_at"] is None


async def test_get_returns_row(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="w1", requested_mode="yolo", approver="lead-backend"
    )
    fetched = await mode_request_store.get(req["id"])
    assert fetched is not None
    assert fetched["id"] == req["id"]


async def test_get_missing_returns_none(mode_request_store: ModeRequestStore) -> None:
    assert await mode_request_store.get(99999) is None


async def test_list_pending_scopes_to_approver(mode_request_store: ModeRequestStore) -> None:
    await mode_request_store.create(requester="w1", requested_mode="yolo", approver="lead-a")
    await mode_request_store.create(requester="w2", requested_mode="yotree", approver="lead-a")
    await mode_request_store.create(requester="w3", requested_mode="yolo", approver="lead-b")

    pending_a = await mode_request_store.list_pending("lead-a")
    assert len(pending_a) == 2
    assert {r["requester"] for r in pending_a} == {"w1", "w2"}

    pending_b = await mode_request_store.list_pending("lead-b")
    assert len(pending_b) == 1


async def test_list_pending_excludes_resolved(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="lead-a", requested_mode="yotree", approver="dev"
    )
    await mode_request_store.approve(req["id"])

    pending = await mode_request_store.list_pending("dev")
    assert pending == []


async def test_approve_flips_status(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="lead-a", requested_mode="yotree", approver="dev"
    )
    result = await mode_request_store.approve(req["id"])
    assert result is not None
    assert result["status"] == "approved"
    assert result["resolved_at"] is not None


async def test_approve_nonexistent_returns_none(mode_request_store: ModeRequestStore) -> None:
    assert await mode_request_store.approve(99999) is None


async def test_cannot_approve_already_resolved(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="lead-a", requested_mode="yotree", approver="dev"
    )
    await mode_request_store.approve(req["id"])
    # Second approve should be a no-op (row already resolved)
    assert await mode_request_store.approve(req["id"]) is None


async def test_deny_with_reason(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(
        requester="w1", requested_mode="yolo", approver="lead-a", reason="format readme"
    )
    result = await mode_request_store.deny(req["id"], reason="use edit instead")
    assert result is not None
    assert result["status"] == "denied"
    assert result["reason"] == "use edit instead"


async def test_deny_preserves_existing_reason_when_none_given(
    mode_request_store: ModeRequestStore,
) -> None:
    req = await mode_request_store.create(
        requester="w1",
        requested_mode="yolo",
        approver="lead-a",
        reason="original reason",
    )
    result = await mode_request_store.deny(req["id"])
    assert result is not None
    assert result["status"] == "denied"
    assert result["reason"] == "original reason"


async def test_deny_nonexistent_returns_none(mode_request_store: ModeRequestStore) -> None:
    assert await mode_request_store.deny(99999) is None


async def test_expire_older_than(mode_request_store: ModeRequestStore) -> None:
    old = await mode_request_store.create(requester="w1", requested_mode="yolo", approver="lead-a")
    # Back-date the row so it's older than cutoff
    async with mode_request_store.pool.acquire() as conn:
        await conn.execute(
            "UPDATE mode_requests SET created_at = NOW() - INTERVAL '1 hour' WHERE id = $1",
            old["id"],
        )
    fresh = await mode_request_store.create(
        requester="w2", requested_mode="yolo", approver="lead-a"
    )

    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    expired = await mode_request_store.expire_older_than(cutoff)
    assert len(expired) == 1
    assert expired[0]["id"] == old["id"]
    assert expired[0]["status"] == "expired"

    # Fresh one still pending
    pending = await mode_request_store.list_pending("lead-a")
    assert len(pending) == 1
    assert pending[0]["id"] == fresh["id"]


async def test_expire_leaves_resolved_rows_alone(mode_request_store: ModeRequestStore) -> None:
    req = await mode_request_store.create(requester="w1", requested_mode="yolo", approver="lead-a")
    await mode_request_store.approve(req["id"])
    async with mode_request_store.pool.acquire() as conn:
        await conn.execute(
            "UPDATE mode_requests SET created_at = NOW() - INTERVAL '1 hour' WHERE id = $1",
            req["id"],
        )

    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    expired = await mode_request_store.expire_older_than(cutoff)
    assert expired == []

    # Still approved, not expired
    row = await mode_request_store.get(req["id"])
    assert row is not None
    assert row["status"] == "approved"


async def test_recent_returns_mixed_statuses(mode_request_store: ModeRequestStore) -> None:
    r1 = await mode_request_store.create(requester="w1", requested_mode="yolo", approver="lead-a")
    r2 = await mode_request_store.create(requester="w2", requested_mode="yotree", approver="lead-a")
    r3 = await mode_request_store.create(requester="w3", requested_mode="yolo", approver="lead-b")
    await mode_request_store.approve(r1["id"])
    await mode_request_store.deny(r2["id"], reason="nope")

    rows = await mode_request_store.recent(limit=10)
    assert len(rows) == 3
    # Sorted by created_at DESC — most recent first
    assert rows[0]["id"] == r3["id"]
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses[r1["id"]] == "approved"
    assert statuses[r2["id"]] == "denied"
    assert statuses[r3["id"]] == "pending"
