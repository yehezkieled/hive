"""View-model builder for the Hive landing page.

Assembles the dict shape consumed by ``templates/dashboard.html`` and its
htmx fragment partials from live data in the running orchestrator's stores.
Each store is optional — when missing, the corresponding section degrades
to an empty/zero placeholder so the page still renders during cold starts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus

if TYPE_CHECKING:
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.models.entity import Entity
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


def _display_state(entity: Entity) -> str:
    """Collapse the 6-state Hive lifecycle into the design's 3-state view."""
    if entity.state == EntityState.RUNNING:
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

    Filenames follow ``<role>-<name>.md``; underscore-prefixed files (the
    ``_template.md`` skeleton) are ignored.
    """
    if not personalities_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(personalities_dir.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        parts = path.stem.split("-", 1)
        name = parts[1] if len(parts) == 2 else path.stem
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
    default_maestro: str = "dev",
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
            "participants": ([f"/m:{default_maestro}"] if default_maestro in entities else []),
            "messages": [
                {
                    "from": "system",
                    "text": ("Chat from web is read-only in v1. Use Telegram to send."),
                },
            ],
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
