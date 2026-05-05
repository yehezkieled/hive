"""Tests for the Sprint 24 W3 CFD bucket math + anomaly heuristic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from hive.web.view_model import _cfd_anomalies, build_dashboard_view_model


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {"dev": object()}
    return pm


async def test_cfd_buckets_count_lifecycle(task_store) -> None:
    """Buckets reflect cumulative status counts as tasks transition over time."""
    pool = task_store.pool
    now = datetime.now(UTC)

    # Task A — created 10h ago, started 8h ago, completed 4h ago.
    await pool.execute(
        """
        INSERT INTO tasks (title, status, priority, assigned_to, created_by,
                           created_at, started_at, completed_at)
        VALUES ('a', 'completed', 2, 'dev', 'system', $1, $2, $3)
        """,
        now - timedelta(hours=10),
        now - timedelta(hours=8),
        now - timedelta(hours=4),
    )
    # Task B — created 6h ago, still pending.
    await pool.execute(
        """
        INSERT INTO tasks (title, status, priority, created_by, created_at)
        VALUES ('b', 'pending', 2, 'system', $1)
        """,
        now - timedelta(hours=6),
    )

    buckets = await task_store.cfd_buckets(buckets=12, hours_per_bucket=2)
    # Newest bucket = right now: 1 completed, 0 in-progress, 1 pending.
    latest = buckets[-1]
    assert latest["completed"] == 1
    assert latest["inProgress"] == 0
    assert latest["pending"] == 1
    # 8h-ago bucket (idx ~7): a is in-progress, b not yet created.
    eight_ago = buckets[8]
    assert eight_ago["inProgress"] == 1
    assert eight_ago["completed"] == 0


def test_cfd_anomalies_flag_zero_drop_amid_steady_flow() -> None:
    """A bucket with zero completion rate inside a steady window is flagged crit."""
    points = []
    completed = 0
    for i in range(20):
        if i == 10:
            pass  # no growth — flat bucket
        else:
            completed += 5
        points.append(
            {
                "i": i,
                "completed": completed,
                "inProgress": 0,
                "pending": 0,
                "total": completed,
            }
        )
    anomalies = _cfd_anomalies(points)
    assert any(a["i"] == 10 and a["severity"] == "crit" for a in anomalies)


def test_cfd_anomalies_empty_when_no_completion() -> None:
    points = [
        {"i": i, "completed": 0, "inProgress": 0, "pending": 0, "total": 0}
        for i in range(20)
    ]
    assert _cfd_anomalies(points) == []


def test_cfd_anomalies_empty_when_perfectly_uniform() -> None:
    points = [
        {"i": i, "completed": i * 5, "inProgress": 0, "pending": 0, "total": i * 5}
        for i in range(20)
    ]
    assert _cfd_anomalies(points) == []


async def test_p0p1_delta_yesterday(task_store) -> None:
    """P0/P1 backlog delta compares now vs 24h ago, not raw current count."""
    pool = task_store.pool
    now = datetime.now(UTC)

    # 5 P0 tasks created 48h ago, all completed before the 24h cutoff.
    # → They don't count toward "yesterday" backlog.
    for _ in range(5):
        await pool.execute(
            """
            INSERT INTO tasks (title, status, priority, created_by,
                               created_at, completed_at)
            VALUES ('old', 'completed', 0, 'system', $1, $2)
            """,
            now - timedelta(hours=48),
            now - timedelta(hours=30),
        )
    # 3 P0 tasks created 1h ago, still pending.
    # → Count toward "now" but not "yesterday".
    for _ in range(3):
        await pool.execute(
            """
            INSERT INTO tasks (title, status, priority, created_by, created_at)
            VALUES ('new', 'pending', 0, 'system', $1)
            """,
            now - timedelta(hours=1),
        )

    view = await build_dashboard_view_model(
        task_store=task_store, process_manager=_bare_pm()
    )
    assert view["p0p1Backlog"]["count"] == 3
    assert view["p0p1Backlog"]["deltaYesterday"] == 3


async def test_claim_next_sets_started_at(task_store) -> None:
    """Claiming a task records started_at so CFD buckets see real transition time."""
    pool = task_store.pool
    await pool.execute(
        "INSERT INTO tasks (title, status, priority, created_by) "
        "VALUES ('claim-me', 'pending', 2, 'system')"
    )
    claimed = await task_store.claim_next("dev")
    assert claimed is not None
    row = await pool.fetchrow("SELECT started_at FROM tasks WHERE id = $1", claimed.id)
    assert row["started_at"] is not None
