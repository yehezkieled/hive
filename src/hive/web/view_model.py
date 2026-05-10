"""View-model builder for the Hive landing page.

Assembles the dict shape consumed by ``templates/landing.html`` and its
htmx fragment partials from live data in the running orchestrator's stores.
Each store is optional — when missing, the corresponding section degrades
to an empty/zero placeholder so the page still renders during cold starts.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.store import MessageStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.models.entity import Entity
    from hive.observability.health_monitor import HealthMonitor
    from hive.process.manager import ProcessManager


def _priority_label(p: int) -> str:
    """Map an int priority to its design-spec label, clamping into [P0, P4]."""
    return f"P{max(0, min(4, p))}"


def _relative_time(when: datetime | None) -> str:
    """Render a UTC datetime as ``Ns ago`` / ``Nm ago`` / ``Nh ago`` / ``Nd ago``."""
    if when is None:
        return "—"
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = int((datetime.now(UTC) - when).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


_ACTIVE_RECENCY_WINDOW = timedelta(minutes=10)


def _display_state(entity: Entity) -> str:
    """Collapse the 6-state Hive lifecycle into the design's 3-state view.

    "Active" tracks recent user attention, not just subprocess execution:
    EntityState.RUNNING is transient (~seconds while a claude -p call
    runs), so on its own it makes the Active list flicker. Treat any
    entity whose last_activity_at falls inside the recency window as
    active too — that way messaging hive_dev keeps it on the Active
    strip until the user moves on.
    """
    if entity.state == EntityState.RUNNING:
        return "active"
    if entity.last_activity_at is not None:
        age = datetime.now(UTC) - entity.last_activity_at
        if age < _ACTIVE_RECENCY_WINDOW:
            return "active"
    if entity.state in (EntityState.IDLE, EntityState.STARTING, EntityState.COMPLETED):
        return "idle"
    return "dormant"


async def _open_tasks_for(name: str, task_store: TaskStore | None) -> list[dict]:
    """Tasks pending/in-progress assigned to ``name`` or anything below it.

    Hive's naming convention is ``maestro.team[.worker]`` (see ``Team``), so
    a maestro card surfaces work assigned to the maestro itself *and* to any
    lead/worker in its org tree. Without this, a task delegated to
    ``dev.backend.w1`` would never appear on the ``dev`` maestro card.
    """
    if task_store is None:
        return []
    rows = await task_store.list(status=TaskStatus.PENDING, limit=200)
    rows += await task_store.list(status=TaskStatus.IN_PROGRESS, limit=200)
    prefix = f"{name}."
    return [
        {"priority": _priority_label(t.priority)}
        for t in rows
        if t.assigned_to == name or (t.assigned_to or "").startswith(prefix)
    ]


async def _entity_to_card(entity: Entity, *, task_store: TaskStore | None) -> dict:
    """Build the card dict consumed by ``_macros.html`` ``maestro_card``."""
    leads = workers = 0
    if isinstance(entity, Maestro):
        leads = len(entity.teams)
        workers = sum(len(team.workers) for team in entity.teams.values())

    tasks = await _open_tasks_for(entity.name, task_store)
    status_word = entity.state.value
    plural = "" if len(tasks) == 1 else "s"
    summary = f"{entity.role} · {status_word}. {len(tasks)} open task{plural}."

    return {
        "name": entity.name,
        "role": entity.role,
        "state": _display_state(entity),
        "summary": summary,
        "updated": _relative_time(entity.last_activity_at or entity.started_at),
        "leads": leads,
        "workers": workers,
        "tasks": tasks,
        "mode": entity.permission_mode,
        "model": entity.model,
    }


def _list_dormant(personalities_dir: Path, registered: set[str]) -> list[dict]:
    """Personality files in ``personalities/`` not yet registered as entities.

    Concrete maestro personalities are bare ``<name>.md`` (e.g. ``dev.md``,
    ``pa.md``). ``role-*.md`` files (``role-lead``, ``role-maestro``,
    ``role-worker``, ``role-vault``) are role-definition templates, not
    specific entities, so they are skipped — same with the ``_template.md``
    skeleton.
    """
    if not personalities_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(personalities_dir.glob("*.md")):
        if path.stem.startswith("_") or path.stem.startswith("role-"):
            continue
        name = path.stem
        if name not in registered:
            out.append({"name": name})
    return out


_PA_STUB: dict = {
    "name": "pa",
    "role": "personal assistant",
    "state": "dormant",
    "summary": ("PA maestro not yet spawned. Run /m:pa hello in Telegram to register."),
    "updated": "—",
    "leads": 0,
    "workers": 0,
    "tasks": [],
    "mode": "—",
    "model": "—",
}


async def build_landing_view_model(
    *,
    process_manager: ProcessManager,
    task_store: TaskStore | None = None,
    token_store: TokenStore | None = None,
    vault_store: VaultStore | None = None,
    mode_request_store: ModeRequestStore | None = None,
    personalities_dir: Path | None = None,
    default_maestro: str = "pa",
    message_store: MessageStore | None = None,
) -> dict:
    """Assemble the landing-page view-model dict from live Hive state."""
    entities = process_manager.entities
    maestros = [e for e in entities.values() if isinstance(e, Maestro)]

    active_maestros: list[Maestro] = []
    idle_maestros: list[Maestro] = []
    for m in maestros:
        (active_maestros if _display_state(m) == "active" else idle_maestros).append(m)

    active_cards = [await _entity_to_card(m, task_store=task_store) for m in active_maestros]
    active_cards.sort(key=lambda c: -sum(1 for t in c["tasks"] if t["priority"] in ("P0", "P1")))

    idle_list = [
        {
            "name": m.name,
            "role": m.role,
            "state": "idle",
            "last_active": _relative_time(m.last_activity_at),
        }
        for m in idle_maestros
    ]

    dormant_list: list[dict] = []
    if personalities_dir is not None:
        dormant_list = _list_dormant(personalities_dir, set(entities.keys()))

    pa_entity = entities.get("pa")
    pa_card = (
        dict(_PA_STUB)
        if pa_entity is None
        else await _entity_to_card(pa_entity, task_store=task_store)
    )

    vault_pending: list[dict] = []
    vault_recent: list[dict] = []
    if vault_store is not None:
        vault_pending = await vault_store.pending("vault")
        vault_recent = await vault_store.log("vault", limit=3)

    mode_pending: list[dict] = []
    if mode_request_store is not None:
        mode_pending = await mode_request_store.list_pending(default_maestro)

    pending_total = len(vault_pending) + len(mode_pending)

    highest: dict | None = None
    if vault_pending:
        v = vault_pending[0]
        highest = {"priority": "P2", "desc": v.get("description", "—")}
    elif mode_pending:
        m = mode_pending[0]
        highest = {
            "priority": "P1",
            "desc": f"{m['requester']} → {m['requested_mode']}",
        }

    recent_rows = [
        {
            "priority": "P2",
            "desc": r.get("description", "—"),
            "amount": "",
        }
        for r in vault_recent
    ]

    chat_messages: list[dict] = []
    chat_participants: list[str] = []
    if message_store is not None:
        rows = await message_store.get_recent(limit=20)
        # Distinct non-user counterparties, ordered by most-recent contact
        # (rows arrive newest → oldest from the store).
        seen: set[str] = set()
        for r in rows:
            sender = r["sender"]
            recipient = r.get("recipient")
            counterparty = recipient if sender == "user" else sender
            if counterparty and counterparty != "user" and counterparty in entities:
                if counterparty not in seen:
                    seen.add(counterparty)
                    chat_participants.append(f"/m:{counterparty}")
        for r in reversed(rows):  # store returns DESC; UI reads top→bottom oldest→newest
            chat_messages.append(
                {
                    "from": "user" if r["sender"] == "user" else r["sender"],
                    "text": r["content"],
                }
            )

    if not chat_participants and default_maestro in entities:
        chat_participants = [f"/m:{default_maestro}"]

    active_count = len(active_maestros)
    idle_count = len(idle_maestros)
    dormant_count = len(dormant_list)

    if active_count > 0:
        mood = "buzzing"
    elif idle_count > 0:
        mood = "quiet"
    else:
        mood = "asleep"

    return {
        "approvals_count": pending_total,
        "health": {"state": "active", "label": "all systems ok"},
        "hero": {
            "mood": mood,
            "active_count": active_count,
            "idle_count": idle_count,
            "dormant_count": dormant_count,
        },
        "chat": {
            "participants": chat_participants,
            "messages": chat_messages,
        },
        "pa": pa_card,
        "vault": {
            "pending_approvals": pending_total,
            "highest": highest,
            "recent": recent_rows,
        },
        "active": active_cards,
        "idle": idle_list,
        "dormant": dormant_list,
        "terminal": {
            "unlocked": False,
            "hint": "available via SSH on Tailscale (port 7777)",
        },
    }


# ─── Sprint 20: Dashboard view-model ──────────────────────────────────────────


_BURN_RANGES: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 60),
    "24h": (timedelta(hours=24), 48),
    "7d": (timedelta(days=7), 28),
    "30d": (timedelta(days=30), 30),
}

_AUDIT_NAMESPACES = ("command", "entity", "task", "git", "vault")


async def build_dashboard_view_model(
    *,
    token_store: TokenStore | None = None,
    audit_log: AuditLog | None = None,
    task_store: TaskStore | None = None,
    process_manager: ProcessManager | None = None,
    health_monitor: HealthMonitor | None = None,
) -> dict:
    """Assemble the ``window.HIVE_DASH`` payload for ``templates/dashboard.html``.

    Six widgets are wired to real Postgres/in-memory telemetry (cost
    ribbon, system health, token burn, bubble matrix, cache hit,
    audit log). Two are mocked with ``# TODO Sprint 21:`` markers
    (workload CFD anomalies, failure scatter) until the upstream
    instrumentation lands.
    """
    last_24h = datetime.now(UTC) - timedelta(hours=24)

    cost30 = await _build_cost30(token_store)
    health = health_monitor.snapshot() if health_monitor is not None else _empty_health()
    cfd, sankey, p0p1_backlog = await _build_cfd(task_store)
    burn = await _build_burn(token_store)
    burn_events = {key: [] for key in _BURN_RANGES}
    matrix = await _build_matrix(token_store, last_24h, process_manager)
    cache_rows, cache_overall = await _build_cache(token_store, last_24h)
    histogram = await _build_histogram(audit_log)
    audit_feed = await _build_audit_feed(audit_log)

    entities_y = _entity_names(process_manager)
    failures, failures_summary = await _build_failures(task_store, entities_y)

    return {
        "cost30": cost30,
        "health": health,
        "sankey": sankey,
        "p0p1Backlog": p0p1_backlog,
        "cfd": cfd,
        "burn": burn,
        "burnEvents": burn_events,
        "matrix": matrix,
        "cacheRows": cache_rows,
        "cacheOverall": cache_overall,
        "histogram": histogram,
        "auditFeed": audit_feed,
        "failures": failures,
        "failuresSummary": failures_summary,
        "entitiesY": entities_y,
        "lastUpdated": "just now",
    }


async def _build_cost30(token_store: TokenStore | None) -> list[dict]:
    """30-day daily cost with per-DOW median/stdev for anomaly envelope."""
    if token_store is None:
        return []
    rows = await token_store.daily_cost(30)
    by_dow: dict[int, list[float]] = {}
    for r in rows:
        by_dow.setdefault(r["dow"], []).append(r["cost"])
    dow_stats: dict[int, dict[str, float]] = {}
    for dow, costs in by_dow.items():
        if len(costs) >= 2:
            dow_stats[dow] = {
                "median": statistics.median(costs),
                "stdev": statistics.pstdev(costs),
            }
        else:
            dow_stats[dow] = {"median": costs[0] if costs else 0.0, "stdev": 0.0}

    out: list[dict] = []
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d")
        stat = dow_stats.get(r["dow"], {"median": 0.0, "stdev": 0.0})
        out.append(
            {
                "ts": r["date"],
                "day": d.strftime("%a"),
                "cost": round(r["cost"], 2),
                "median": round(stat["median"], 2),
                "stdev": round(stat["stdev"], 2),
            }
        )
    return out


def _empty_health() -> list[dict]:
    """W2 cold-start fallback when no HealthMonitor is wired in (tests, CLI mode)."""
    bars_ok = ["ok"] * 60
    return [
        {"name": "orchestrator", "summary": "—", "bars": list(bars_ok), "lit": 0},
        {"name": "postgres", "summary": "—", "bars": list(bars_ok), "lit": 0},
        {"name": "claude api", "summary": "—", "bars": list(bars_ok), "lit": 0},
        {"name": "heartbeat", "summary": "—", "bars": list(bars_ok), "lit": 0},
        {"name": "disk", "summary": "—", "bars": list(bars_ok), "lit": 0},
    ]


async def _build_cfd(task_store: TaskStore | None) -> tuple[dict, dict, dict]:
    """W3 + sankey: per-bucket cumulative counts with anomaly heuristic."""
    n = 42
    day_boundaries = [(d + 1) * 6 - 1 for d in range(7)]
    empty_points = [
        {
            "i": i,
            "day": i // 6,
            "hour": (i % 6) * 4,
            "label": f"D{i // 6 + 1} {(i % 6) * 4:02d}:00",
            "completed": 0,
            "inProgress": 0,
            "pending": 0,
            "total": 0,
        }
        for i in range(n)
    ]
    sankey_zero = {
        "pending_inProgress": {"count": 0, "byPriority": {}},
        "pending_cancelled": {"count": 0, "byPriority": {}},
        "inProgress_completed": {"count": 0, "byPriority": {}},
        "inProgress_cancelled": {"count": 0, "byPriority": {}},
        "pending_now": 0,
        "inProgress_now": 0,
        "completed_now": 0,
        "cancelled_now": 0,
    }
    p0p1_zero = {"count": 0, "deltaYesterday": 0}

    if task_store is None:
        return (
            {
                "points": empty_points,
                "cancelled7d": 0,
                "anomalies": [],
                "dayBoundaries": day_boundaries,
            },
            sankey_zero,
            p0p1_zero,
        )

    buckets = await task_store.cfd_buckets(buckets=n, hours_per_bucket=4)
    points: list[dict] = []
    for bucket in buckets:
        i = bucket["i"]
        points.append(
            {
                "i": i,
                "day": i // 6,
                "hour": (i % 6) * 4,
                "label": f"D{i // 6 + 1} {(i % 6) * 4:02d}:00",
                "completed": bucket["completed"],
                "inProgress": bucket["inProgress"],
                "pending": bucket["pending"],
                "total": bucket["total"],
            }
        )
    anomalies = _cfd_anomalies(points)

    pending = await task_store.list(status=TaskStatus.PENDING, limit=1000)
    in_progress = await task_store.list(status=TaskStatus.IN_PROGRESS, limit=1000)
    completed = await task_store.list(status=TaskStatus.COMPLETED, limit=1000)
    cancelled = await task_store.list(status=TaskStatus.CANCELLED, limit=1000)

    fp, fi, fc = len(pending), len(in_progress), len(completed)
    p0p1_count = sum(1 for t in pending + in_progress if t.priority <= 1)
    p0p1_yesterday = await _p0p1_24h_ago(task_store)
    p0p1_delta = p0p1_count - p0p1_yesterday
    sankey_now = {
        **sankey_zero,
        "pending_now": fp,
        "inProgress_now": fi,
        "completed_now": fc,
        "cancelled_now": len(cancelled),
    }
    p0p1 = {"count": p0p1_count, "deltaYesterday": p0p1_delta}

    return (
        {
            "points": points,
            "cancelled7d": len(cancelled),
            "anomalies": anomalies,
            "dayBoundaries": day_boundaries,
        },
        sankey_now,
        p0p1,
    )


def _cfd_anomalies(points: list[dict]) -> list[dict]:
    """Flag buckets where the completion rate dips far below the median.

    Computes per-bucket completion-rate (delta of cumulative ``completed``)
    over the window. If a bucket's rate < median - 1.5*stdev, mark it as
    an anomaly. ``severity = "crit"`` if rate is exactly zero amid steady
    flow, otherwise ``"warn"``.
    """
    if len(points) < 4:
        return []
    rates = [
        max(0, points[i]["completed"] - points[i - 1]["completed"]) for i in range(1, len(points))
    ]
    if not rates or max(rates) == 0:
        return []
    median = statistics.median(rates)
    stdev = statistics.pstdev(rates)
    if stdev == 0:
        return []
    threshold = median - 1.5 * stdev
    anomalies: list[dict] = []
    for offset, rate in enumerate(rates):
        if rate < threshold and rate < median:
            i = offset + 1
            severity = "crit" if rate == 0 and median > 0 else "warn"
            anomalies.append({"i": i, "type": "drop", "severity": severity})
    return anomalies


async def _p0p1_24h_ago(task_store: TaskStore) -> int:
    """Count of P0+P1 tasks that were pending or in-progress 24h ago.

    A task qualifies if it was created before the cutoff and either
    hadn't completed by then or completed after it.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    row = await task_store.pool.fetchrow(
        """
        SELECT COUNT(*) AS c FROM tasks
        WHERE priority <= 1
          AND created_at <= $1
          AND (completed_at IS NULL OR completed_at > $1)
        """,
        cutoff,
    )
    return int(row["c"]) if row else 0


async def _build_burn(token_store: TokenStore | None) -> dict[str, list[dict]]:
    """W4: token burn pre-built for all 4 ranges. Empty list per range when no store."""
    if token_store is None:
        return {key: [] for key in _BURN_RANGES}

    out: dict[str, list[dict]] = {}
    for key, (window, buckets) in _BURN_RANGES.items():
        rows = await token_store.token_burn(window=window, buckets=buckets)
        out[key] = [
            {
                "label": _burn_label(key, r["i"], buckets),
                "input": int(r["input_tokens"]),
                "output": int(r["output_tokens"]),
                "cacheRead": int(r["cache_read_input_tokens"]),
                "cacheCreate": int(r["cache_creation_input_tokens"]),
                "cost": round(r["cost"], 4),
            }
            for r in rows
        ]
    return out


def _burn_label(key: str, i: int, buckets: int) -> str:
    """Render a per-range x-axis label matching the design's tick style."""
    if key == "1h":
        # 60 1-min buckets → MM:SS-ish (each bucket is 1 min)
        return f"{i:02d}m"
    if key == "24h":
        # 48 30-min buckets → HH:MM
        h, m = divmod(i * 30, 60)
        return f"{h:02d}:{m:02d}"
    if key == "7d":
        # 28 6-hour buckets → "M¼" style
        days = "MTWTFSS"
        suffix = ["", "¼", "½", "¾"]
        return f"{days[(i // 4) % 7]}{suffix[i % 4]}"
    if key == "30d":
        # 30 daily buckets, oldest→newest
        return f"d{buckets - i}"
    return str(i)


async def _build_matrix(
    token_store: TokenStore | None,
    since: datetime,
    process_manager: ProcessManager | None,
) -> dict:
    """W5: bubble matrix ``{entities, models, cells}`` since ``since``."""
    if token_store is None:
        return {"entities": [], "models": [], "cells": {}}
    cells = await token_store.cost_by_entity_model(since)
    entities = sorted(cells.keys())
    if process_manager is not None:
        # Surface registered entities even if they have no spend yet so the
        # matrix shows the full org rather than only big spenders.
        registered = sorted(process_manager.entities.keys())
        for name in registered:
            if name not in entities:
                entities.append(name)
                cells[name] = {}
    models: list[str] = []
    for entity_cells in cells.values():
        for model in entity_cells:
            if model not in models:
                models.append(model)
    models.sort()
    return {"entities": entities, "models": models, "cells": cells}


async def _build_cache(token_store: TokenStore | None, since: datetime) -> tuple[list[dict], dict]:
    """W6: per-entity cache hit + overall sparkline."""
    if token_store is None:
        return [], {"hit": 0, "sparkline": [0.0] * 7}
    rows = await token_store.cache_stats(since)
    names = [r["name"] for r in rows]
    baseline = await token_store.cache_baseline_7d(names) if names else {}
    cache_rows = [
        {
            "name": r["name"],
            "hit": r["hit_pct"],
            "baseline": baseline.get(r["name"], r["hit_pct"]),
            "tokens": {
                "cached": int(r["cached_tokens"]),
                "fresh": int(r["fresh_tokens"]),
            },
        }
        for r in rows
    ]
    sparkline = await token_store.cache_overall_daily(7)
    overall_hit = round(sparkline[-1], 1) if sparkline else 0.0
    return cache_rows, {"hit": overall_hit, "sparkline": sparkline}


async def _build_histogram(audit_log: AuditLog | None) -> list[dict]:
    """W7 timeline: 60 1-min buckets with per-namespace counts."""
    if audit_log is None:
        return [
            {"i": i, "command": 0, "entity": 0, "task": 0, "git": 0, "vault": 0} for i in range(60)
        ]
    return await audit_log.histogram(window_minutes=60)


async def _build_audit_feed(audit_log: AuditLog | None) -> list[dict]:
    """W7 feed: most recent events in the 4 surfaced namespaces."""
    if audit_log is None:
        return []
    rows = await audit_log.recent(limit=100)
    out: list[dict] = []
    for r in rows:
        ns = r["action"].split(".", 1)[0] if r.get("action") else ""
        if ns not in _AUDIT_NAMESPACES:
            continue
        ts = r["timestamp"]
        out.append(
            {
                "ts": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
                "ns": ns,
                "detail": _audit_detail(r),
                "payload": r.get("details") or {},
            }
        )
        if len(out) >= 50:
            break
    return out


def _audit_detail(row: dict[str, Any]) -> str:
    """Render a one-line summary of an audit row for the feed."""
    parts = [row.get("action", "—")]
    if row.get("target"):
        parts.append(f"· {row['target']}")
    if row.get("actor"):
        parts.append(f"· by {row['actor']}")
    return " ".join(parts)


def _entity_names(process_manager: ProcessManager | None) -> list[str]:
    """Stable ordered entity list for failure-scatter Y axis."""
    if process_manager is None:
        return []
    return sorted(process_manager.entities.keys())


async def _build_failures(
    task_store: TaskStore | None,
    entities_y: list[str],
) -> tuple[list[dict], dict]:
    """W8: failure scatter + summary counters.

    Pulls tasks with a non-null ``failure_reason`` from the last hour
    for the scatter and the last 24 hours for the ``lastHour`` /
    ``pendingEscalations`` summary tiles. The classifier maps each
    free-text reason to a coarse category that drives the dot colour.
    """
    empty_summary = {"lastHour": 0, "pendingEscalations": 0, "longestStreak": None}
    if task_store is None:
        return [], empty_summary

    from hive.observability.failure_classifier import classify

    now = datetime.now(UTC)
    window_start = now - timedelta(hours=1)
    rows = await task_store.pool.fetch(
        """
        SELECT id, assigned_to, failure_reason, retry_count, max_retries,
               status, completed_at, created_at
        FROM tasks
        WHERE failure_reason IS NOT NULL
          AND COALESCE(completed_at, created_at) >= $1
        ORDER BY COALESCE(completed_at, created_at) DESC
        LIMIT 500
        """,
        window_start,
    )
    entity_index = {name: i for i, name in enumerate(entities_y)}
    scatter: list[dict] = []
    for r in rows:
        ts = r["completed_at"] or r["created_at"]
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        minutes_ago = max(0, int((now - ts).total_seconds() // 60))
        scatter.append(
            {
                "x": entity_index.get(r["assigned_to"] or "", -1),
                "y": minutes_ago,
                "category": classify(r["failure_reason"]),
                "task_id": r["id"],
            }
        )

    last_hour = await task_store.pool.fetchval(
        """
        SELECT COUNT(*) FROM tasks
        WHERE failure_reason IS NOT NULL
          AND COALESCE(completed_at, created_at) >= $1
        """,
        window_start,
    )
    pending_escalations = await task_store.pool.fetchval(
        """
        SELECT COUNT(*) FROM tasks
        WHERE failure_reason IS NOT NULL
          AND retry_count >= max_retries
          AND status != 'completed'
        """
    )
    streak_row = await task_store.pool.fetchrow(
        """
        SELECT MAX(streak_len) AS longest FROM (
            SELECT COUNT(*) AS streak_len
            FROM (
                SELECT
                    assigned_to,
                    failure_reason IS NOT NULL AS is_fail,
                    SUM(CASE WHEN failure_reason IS NULL THEN 1 ELSE 0 END)
                        OVER (PARTITION BY assigned_to ORDER BY created_at) AS grp
                FROM tasks
                WHERE assigned_to IS NOT NULL
            ) labelled
            WHERE is_fail
            GROUP BY assigned_to, grp
        ) streaks
        """
    )
    longest = streak_row["longest"] if streak_row else None
    return scatter, {
        "lastHour": int(last_hour or 0),
        "pendingEscalations": int(pending_escalations or 0),
        "longestStreak": int(longest) if longest else None,
    }
