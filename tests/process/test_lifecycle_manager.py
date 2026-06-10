"""Unit tests for the ``LifecycleManager`` collaborator (Ticket 004 slice 4).

These exercise ``LifecycleManager`` in isolation against a *stub* manager —
no real ProcessManager, no Postgres, no Claude subprocess. The stub exposes
only the surface the lifecycle code reaches through ``self._mgr``: the
``_entities`` / ``_adapters`` registries, the single ``_state_lock``, the
router, the optional stores/worktree manager, and the ``_persist`` /
``_audit`` / ``_notify`` recorders.

The big DB-backed suites (``test_process_manager``, ``test_advisor_mcp``)
still cover the same flows end-to-end through the facade; these add fast,
hermetic unit coverage of the moved code and prove the composition pattern
(collaborator reaching shared state via ``self._mgr``).

A focused lock-discipline check at the bottom asserts there is no ``await``
inside any ``async with self._mgr._state_lock`` block — the load-bearing
invariant of this lock-heavy slice (the lock is non-reentrant; awaiting
inside it risks deadlock).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import Worker
from hive.process.lifecycle_manager import (
    LifecycleManager,
    _adapter_config_from_entity,
    _render_auto_personality,
)
from tests.fakes import FakeAdapter

# ---------------------------------------------------------------------------
# Stub manager + fakes
# ---------------------------------------------------------------------------


class FakeRouter:
    """Records register/unregister so tests can assert routing side effects."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregistered: list[str] = []
        self.wake_callback = None

    def register(self, name: str) -> None:
        self.registered.append(name)

    def unregister(self, name: str) -> None:
        self.unregistered.append(name)


class FakeWorktreeManager:
    """Records create/remove calls and hands back deterministic paths."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.created: list[tuple[str, str | None]] = []
        self.removed: list[str] = []

    async def create(self, name: str, branch: str | None = None) -> Path:
        self.created.append((name, branch))
        return self.base / name

    async def remove(self, name: str) -> None:
        self.removed.append(name)


class StubManager:
    """Minimal stand-in for ProcessManager's lifecycle-facing surface.

    Mirrors exactly the facade-owned state ``LifecycleManager`` mutates via
    ``self._mgr``: the registries, the single ``_state_lock``, the router,
    the stores/worktree manager, and the ``_persist`` / ``_audit`` /
    ``_notify`` recorders. ``send_to_entity`` and ``kill_entity`` default to
    recorders so compact/idle paths can be driven without the real facade.
    """

    def __init__(self, *, max_sessions: int = 3) -> None:
        self._entities: dict[str, object] = {}
        self._adapters: dict[str, object] = {}
        self._state_lock = asyncio.Lock()
        self.max_sessions = max_sessions
        self.router = FakeRouter()
        self.worktree_mgr = None
        self.entity_store = None
        self.scheduler = None
        self.quota_monitor = None
        self.gate_coordinator = None
        self._on_gate_state = None
        self.personalities_dir = Path("personalities")

        self.audit_calls: list[tuple[str, str | None, dict | None]] = []
        self.notify_calls: list[str] = []
        self.persisted: list[object] = []
        self.sent: list[tuple[str, str]] = []
        self.killed: list[str] = []

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._adapters.values() if a.is_alive())

    async def _persist(self, entity: object) -> None:
        self.persisted.append(entity)

    async def _audit(
        self,
        action: str,
        target: str | None = None,
        details: dict | None = None,
        actor: str = "system",
    ) -> None:
        self.audit_calls.append((action, target, details))

    async def _notify(self, message: str, kind: str = "info", data: dict | None = None) -> None:
        self.notify_calls.append(message)

    async def send_to_entity(self, name: str, prompt: str) -> str:
        self.sent.append((name, prompt))
        return "summary text"

    async def kill_entity(self, name: str) -> None:
        # Default behaviour for cross-method calls (kill_team, kill_idle):
        # drop the entity + adapter like the real facade does.
        self.killed.append(name)
        self._entities.pop(name, None)
        self._adapters.pop(name, None)


@pytest.fixture
def mgr() -> StubManager:
    m = StubManager()
    m.lifecycle = LifecycleManager(m)  # type: ignore[attr-defined]
    return m


@pytest.fixture
def lifecycle(mgr: StubManager) -> LifecycleManager:
    return mgr.lifecycle  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Module-level helpers (re-exported from manager.py)
# ---------------------------------------------------------------------------


def test_render_auto_personality_has_knowledge_section() -> None:
    """The template teaches agents about the search_knowledge MCP tool."""
    body = _render_auto_personality(
        entity_name="dev",
        role="maestro",
        model="sonnet",
        display_name="Dev",
        personality="You build things.",
    )
    assert "auto_generated: true" in body
    assert "Knowledge search" in body
    assert "search_knowledge" in body


def test_render_auto_personality_no_tools_section_for_coordinators() -> None:
    """Auto-personalities carry no ``## Tools`` section, even for lead/maestro.

    The role tool guard moved from markdown into code (Ticket 015,
    ADR 0010) — ``role_tool_denylist`` merges it at every spawn, asserted
    in ``test_tool_policy.py``. Markdown ``## Tools`` survives only in
    hand-written personality files, as a per-Entity override.
    """
    for role in ("lead", "maestro"):
        body = _render_auto_personality(
            entity_name="dev.backend",
            role=role,
            model="sonnet",
            display_name="Backend",
            personality="Lead the team.",
        )
        assert "## Tools" not in body
        assert "disallowedTools" not in body


def test_render_auto_personality_no_tools_section_for_worker() -> None:
    """Workers inherit the default toolkit — no Tools section is emitted."""
    body = _render_auto_personality(
        entity_name="dev.backend.w1",
        role="worker",
        model="sonnet",
        display_name="W1",
        personality="Do the task.",
    )
    assert "allowedTools" not in body


def test_adapter_config_maps_entity_fields() -> None:
    """_adapter_config_from_entity carries the entity's model + name across."""
    maestro = Maestro(name="dev", model="opus")
    config = _adapter_config_from_entity(maestro)
    assert config.model == "opus"
    assert config.name == "dev"
    assert config.role == "maestro"


def test_worker_config_denies_thinking_and_prototype_skills() -> None:
    """A worker's spawn args carry both the thinking + prototype deny tokens.

    Exercises the full seam: entity -> _adapter_config_from_entity ->
    ClaudeAdapter._build_pty_extra_args -> --disallowedTools (Ticket 012).
    """
    from hive.runtime.claude_adapter import ClaudeAdapter

    worker = Worker(name="dev.backend.w1", lead_name="dev.backend")
    config = _adapter_config_from_entity(worker)
    args = ClaudeAdapter(config)._build_pty_extra_args()

    assert "--disallowedTools" in args
    assert "Skill(grill-me)" in args
    assert "Skill(prototype)" in args


def test_maestro_config_denies_prototype_but_keeps_thinking_skills() -> None:
    """A maestro keeps the thinking skills; only Skill(prototype) is denied.

    Pre-existing disallowed tokens (Agent/Task) survive the merge.
    """
    from hive.runtime.claude_adapter import ClaudeAdapter

    maestro = Maestro(name="dev", model="opus", disallowed_tools=["Agent", "Task"])
    config = _adapter_config_from_entity(maestro)
    args = ClaudeAdapter(config)._build_pty_extra_args()

    assert "Skill(prototype)" in args
    assert "Skill(grill-me)" not in args
    # Pre-existing tokens are preserved alongside the skill tokens.
    assert "Agent" in args
    assert "Task" in args


def test_adapter_config_merges_entity_role_and_skill_denylists() -> None:
    """disallowed_tools = entity tokens + role policy + skill denylist.

    Three sources merge in that order (Ticket 015, ADR 0010), de-duplicated
    keeping the first-seen position — an entity token that also appears in
    the role policy (here ``Agent``) is not repeated later.
    """
    from hive.process.skill_curation import skill_denylist_for
    from hive.process.tool_policy import role_tool_denylist

    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        disallowed_tools=["CustomTool", "Agent"],
    )
    config = _adapter_config_from_entity(lead)

    role_deny = role_tool_denylist("lead")
    skill_deny = skill_denylist_for("lead")
    expected = ["CustomTool", "Agent"]
    expected += [t for t in role_deny if t not in expected]
    expected += [t for t in skill_deny if t not in expected]
    assert config.disallowed_tools == expected
    # First-seen wins: the entity's own ``Agent`` is the only occurrence.
    assert config.disallowed_tools.count("Agent") == 1


def test_bare_lead_config_gets_role_guard_without_sync_wait_verbs() -> None:
    """A lead with no personality file still gets the role guard (hole A).

    The guard used to be written into auto-personality markdown — skipped
    entirely when a lead was spawned without ``display_name``/``personality``.
    Now it comes from ``role_tool_denylist`` on every spawn: ``Agent``/``Task``
    stay denied, while the Workflow sync-wait verbs (``TaskOutput``/
    ``TaskStop``) are allowed (ADR 0010).
    """
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    assert lead.disallowed_tools == []  # nothing from a personality file

    config = _adapter_config_from_entity(lead)

    assert "Agent" in config.disallowed_tools
    assert "Task" in config.disallowed_tools
    assert "TaskOutput" not in config.disallowed_tools
    assert "TaskStop" not in config.disallowed_tools


def test_maestro_config_denies_workflow() -> None:
    """A maestro's adapter config denies ``Workflow`` (ADR 0010).

    Fan-out belongs to leads — a Maestro running Workflow itself would
    bypass the Lead layer, so the chain stays Maestro → Lead → Workflow.
    """
    maestro = Maestro(name="dev", model="opus")
    config = _adapter_config_from_entity(maestro)
    assert "Workflow" in config.disallowed_tools


def test_personality_tools_override_survives_merge_first_seen() -> None:
    """Per-Entity ``## Tools`` markdown still reaches the adapter config.

    ``models/entity.py`` parses a hand-written personality's ``## Tools``
    section into ``entity.disallowed_tools``; this test sets that field
    directly and asserts the tokens lead the merged list (first-seen) —
    including ``TaskOutput``, which the lead *role* policy no longer
    denies, proving the per-Entity override can tighten the role guard.
    """
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        disallowed_tools=["WebFetch", "TaskOutput"],
    )
    config = _adapter_config_from_entity(lead)

    # Override tokens come first, in the entity's own order.
    assert config.disallowed_tools[:2] == ["WebFetch", "TaskOutput"]
    # The role guard is still appended after them.
    assert "Agent" in config.disallowed_tools
    assert config.disallowed_tools.count("WebFetch") == 1
    assert config.disallowed_tools.count("TaskOutput") == 1


# ---------------------------------------------------------------------------
# register_maestro / register_entity
# ---------------------------------------------------------------------------


async def test_register_maestro_inserts_and_registers(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """register_maestro adds the entity, registers it, persists, and audits."""
    maestro = await lifecycle.register_maestro("dev", model="opus")
    assert mgr._entities["dev"] is maestro
    assert maestro.permission_mode == "yolo"
    assert "dev" in mgr.router.registered
    assert maestro in mgr.persisted
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.register" in actions


async def test_register_maestro_rejects_duplicate(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """Re-registering an existing name raises ValueError."""
    await lifecycle.register_maestro("dev")
    with pytest.raises(ValueError, match="already exists"):
        await lifecycle.register_maestro("dev")


async def test_register_entity_idle_no_spawn(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """register_entity adds a pre-built entity without spawning an adapter."""
    worker = Worker(name="dev.team.w1", lead_name="dev.team")
    await lifecycle.register_entity(worker)
    assert mgr._entities["dev.team.w1"] is worker
    assert "dev.team.w1" in mgr.router.registered
    assert "dev.team.w1" not in mgr._adapters


async def test_register_entity_rejects_duplicate(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    worker = Worker(name="dev.team.w1", lead_name="dev.team")
    await lifecycle.register_entity(worker)
    with pytest.raises(ValueError, match="already exists"):
        await lifecycle.register_entity(worker)


# ---------------------------------------------------------------------------
# create_team / spawn_worker
# ---------------------------------------------------------------------------


async def test_create_team_registers_lead(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """create_team adds a TeamLead named maestro.team and registers it."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro

    lead = await lifecycle.create_team("dev", "backend", model="sonnet")
    assert lead.name == "dev.backend"
    assert mgr._entities["dev.backend"] is lead
    assert "dev.backend" in mgr.router.registered
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.create_team" in actions


async def test_create_team_unknown_maestro_raises(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    with pytest.raises(KeyError, match="not found"):
        await lifecycle.create_team("ghost", "backend")


async def test_spawn_worker_auto_names(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """Without an explicit name, workers auto-number w1, w2, ..."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro
    lead = await lifecycle.create_team("dev", "backend", model="sonnet")

    w1 = await lifecycle.spawn_worker(lead.name)
    w2 = await lifecycle.spawn_worker(lead.name)
    assert w1.name == "dev.backend.w1"
    assert w2.name == "dev.backend.w2"
    assert lead.workers == ["dev.backend.w1", "dev.backend.w2"]


async def test_spawn_worker_enforces_max(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """At max_workers, spawning another worker raises RuntimeError."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro
    lead = await lifecycle.create_team("dev", "backend", model="sonnet")
    for _ in range(lead.max_workers):
        await lifecycle.spawn_worker(lead.name)

    with pytest.raises(RuntimeError, match="max"):
        await lifecycle.spawn_worker(lead.name)


# ---------------------------------------------------------------------------
# kill_entity / kill_all / kill_team / stop_all
# ---------------------------------------------------------------------------


async def test_kill_entity_stops_adapter_and_unregisters(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """kill_entity stops + drops the adapter, removes the entity, audits."""
    maestro = Maestro(name="dev", model="sonnet")
    maestro.state = EntityState.RUNNING
    adapter = FakeAdapter()
    mgr._entities["dev"] = maestro
    mgr._adapters["dev"] = adapter

    await lifecycle.kill_entity("dev")

    assert adapter.stopped
    assert "dev" not in mgr._adapters
    assert "dev" not in mgr._entities
    assert maestro.session_id is None
    assert "dev" in mgr.router.unregistered
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.kill" in actions


async def test_kill_all_kills_every_entity(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """kill_all routes through the facade kill_entity for every entity."""
    mgr._entities["a"] = Maestro(name="a", model="sonnet")
    mgr._entities["b"] = Maestro(name="b", model="sonnet")
    await lifecycle.kill_all()
    assert set(mgr.killed) == {"a", "b"}


async def test_stop_all_clears_adapters_keeps_entities(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """stop_all stops adapters and clears the registry but keeps entities."""
    maestro = Maestro(name="dev", model="sonnet")
    adapter = FakeAdapter()
    mgr._entities["dev"] = maestro
    mgr._adapters["dev"] = adapter

    await lifecycle.stop_all()

    assert adapter.stopped
    assert mgr._adapters == {}
    # Entities survive — restore() rebuilds adapters on next boot.
    assert "dev" in mgr._entities


async def test_kill_team_kills_workers_then_lead(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """kill_team kills each worker, then the lead, then removes the Team."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro
    lead = await lifecycle.create_team("dev", "backend", model="sonnet")
    await lifecycle.spawn_worker(lead.name, "w1")

    await lifecycle.kill_team("dev", "backend")

    assert "dev.backend.w1" in mgr.killed
    assert "dev.backend" in mgr.killed
    # Workers are killed before the lead.
    assert mgr.killed.index("dev.backend.w1") < mgr.killed.index("dev.backend")


# ---------------------------------------------------------------------------
# compact_entity / kill_idle_entities
# ---------------------------------------------------------------------------


async def test_compact_entity_requires_session_id(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """Compacting an entity with no session_id raises ValueError."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro
    with pytest.raises(ValueError, match="no active session"):
        await lifecycle.compact_entity("dev")


async def test_compact_entity_unknown_raises(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    with pytest.raises(KeyError, match="not found"):
        await lifecycle.compact_entity("ghost")


async def test_compact_entity_summarizes_kills_reseeds(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """compact_entity sends a summary prompt, kills, re-registers IDLE, reseeds."""
    maestro = Maestro(name="dev", model="sonnet")
    maestro.session_id = "sess-abc"
    mgr._entities["dev"] = maestro

    summary = await lifecycle.compact_entity("dev")
    assert summary == "summary text"
    assert "dev" in mgr.killed
    # Re-registered IDLE, then reseeded.
    assert mgr._entities["dev"] is maestro
    assert maestro.state == EntityState.IDLE
    assert len(mgr.sent) == 2  # summarize prompt + reseed prompt
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.compact" in actions


async def test_kill_idle_skips_gated_and_exempt(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """GATED and exempt entities are never reaped; stale ones are."""
    from datetime import UTC, datetime, timedelta

    stale = datetime.now(UTC) - timedelta(minutes=120)

    idle = Maestro(name="idle", model="sonnet")
    idle.last_activity_at = stale
    gated = Maestro(name="gated", model="sonnet")
    gated.state = EntityState.GATED
    gated.last_activity_at = stale
    exempt = Maestro(name="exempt", model="sonnet")
    exempt.last_activity_at = stale
    mgr._entities.update({"idle": idle, "gated": gated, "exempt": exempt})

    killed = await lifecycle.kill_idle_entities(timeout_minutes=30, exempt_names={"exempt"})

    assert killed == ["idle"]
    assert "gated" not in mgr.killed
    assert "exempt" not in mgr.killed


# ---------------------------------------------------------------------------
# Worktree floor — every Lead gets its own worktree cwd (Ticket 015, ADR 0010)
# ---------------------------------------------------------------------------


def test_team_lead_carries_worktree_path() -> None:
    """TeamLead carries an optional worktree path, mirroring Worker.

    A Lead spawned with ``cwd=None`` inherits the service's working
    directory — the live checkout the deployed service imports from. The
    worktree floor (ADR 0010) hangs off this field.
    """
    bare = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    assert bare.worktree_path is None

    housed = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        worktree_path=Path("/tmp/worktrees/dev.backend"),
    )
    assert housed.worktree_path == Path("/tmp/worktrees/dev.backend")


async def test_create_team_provisions_lead_worktree(
    lifecycle: LifecycleManager, mgr: StubManager, tmp_path: Path
) -> None:
    """With a worktree manager configured, the lead gets its own worktree.

    Mirrors the Worker pattern: named after the lead, on branch
    ``hive/<lead_name>``, path stored on the entity.
    """
    mgr.worktree_mgr = FakeWorktreeManager(tmp_path / "worktrees")
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    lead = await lifecycle.create_team("dev", "backend", model="sonnet")

    assert mgr.worktree_mgr.created == [("dev.backend", "hive/dev.backend")]
    assert lead.worktree_path == tmp_path / "worktrees" / "dev.backend"


async def test_create_team_without_worktree_mgr_leaves_path_none(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """No worktree manager configured → no worktree, nothing breaks."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    lead = await lifecycle.create_team("dev", "backend", model="sonnet")

    assert lead.worktree_path is None


class _RecordingAdapter:
    """Stand-in for ``ClaudeAdapter`` that records the cwd it was given."""

    def __init__(
        self,
        config: object,
        *,
        cwd: Path | None = None,
        gate_coordinator: object = None,
        entity_name: str | None = None,
        on_gate_state: object = None,
    ) -> None:
        self.config = config
        self.cwd = cwd
        self.started = False

    async def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.started


async def test_adapter_for_lead_uses_worktree_cwd(
    lifecycle: LifecycleManager,
    mgr: StubManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lead with a worktree path spawns its adapter inside that worktree.

    Today the cwd selection is Worker-only — a lead falls through to
    ``cwd=None`` and inherits the service's WorkingDirectory, the live
    checkout (ADR 0010 context #2).
    """
    monkeypatch.setattr("hive.process.lifecycle_manager.ClaudeAdapter", _RecordingAdapter)
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        worktree_path=tmp_path / "worktrees" / "dev.backend",
    )
    mgr._entities["dev.backend"] = lead

    adapter = await lifecycle._get_or_create_adapter(lead)

    assert adapter.cwd == tmp_path / "worktrees" / "dev.backend"


async def test_adapter_without_worktree_path_keeps_cwd_none(
    lifecycle: LifecycleManager,
    mgr: StubManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entities without a worktree path behave as before — cwd stays None."""
    monkeypatch.setattr("hive.process.lifecycle_manager.ClaudeAdapter", _RecordingAdapter)
    maestro = Maestro(name="dev", model="sonnet")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities.update({"dev": maestro, "dev.backend": lead})

    assert (await lifecycle._get_or_create_adapter(maestro)).cwd is None
    # No worktree manager configured → nothing to lazily provision either.
    assert (await lifecycle._get_or_create_adapter(lead)).cwd is None


async def test_kill_lead_removes_worktree(
    lifecycle: LifecycleManager, mgr: StubManager, tmp_path: Path
) -> None:
    """kill_entity on a lead removes its worktree, mirroring Worker cleanup."""
    mgr.worktree_mgr = FakeWorktreeManager(tmp_path / "worktrees")
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        worktree_path=tmp_path / "worktrees" / "dev.backend",
    )
    mgr._entities["dev.backend"] = lead

    await lifecycle.kill_entity("dev.backend")

    assert mgr.worktree_mgr.removed == ["dev.backend"]


async def test_restored_lead_lazily_regains_worktree_cwd(
    lifecycle: LifecycleManager,
    mgr: StubManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lead restored from persistence still spawns inside a worktree.

    ``entity_store`` round-trips ``worktree_path`` for Workers only, so a
    restored lead comes back with ``worktree_path=None``. The floor must
    hold across restarts: the adapter path lazily (re-)provisions the
    worktree — ``WorktreeManager.create`` is idempotent, returning the
    existing path when the worktree survived the restart.
    """
    monkeypatch.setattr("hive.process.lifecycle_manager.ClaudeAdapter", _RecordingAdapter)
    mgr.worktree_mgr = FakeWorktreeManager(tmp_path / "worktrees")
    # As _row_to_entity rebuilds a lead: hierarchy fields, no worktree_path.
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev.backend"] = lead

    adapter = await lifecycle._get_or_create_adapter(lead)

    assert mgr.worktree_mgr.created == [("dev.backend", "hive/dev.backend")]
    assert lead.worktree_path == tmp_path / "worktrees" / "dev.backend"
    assert adapter.cwd == tmp_path / "worktrees" / "dev.backend"


# ---------------------------------------------------------------------------
# Lock discipline — no await inside any _state_lock critical section
# ---------------------------------------------------------------------------


def test_no_await_inside_state_lock_blocks() -> None:
    """Static check: no ``await`` lives inside an ``async with _state_lock``.

    The single ``_state_lock`` is non-reentrant; awaiting inside it (calling
    back into the manager) risks deadlock. Every critical section guards only
    synchronous dict mutations. This walks the AST of every
    ``async with self._mgr._state_lock`` block and fails if it contains an
    ``await`` expression.
    """
    source = Path("src/hive/process/lifecycle_manager.py").read_text()
    tree = ast.parse(source)

    def is_state_lock_with(node: ast.AsyncWith) -> bool:
        for item in node.items:
            ctx = item.context_expr
            # Match ``self._mgr._state_lock``.
            if (
                isinstance(ctx, ast.Attribute)
                and ctx.attr == "_state_lock"
                and isinstance(ctx.value, ast.Attribute)
                and ctx.value.attr == "_mgr"
            ):
                return True
        return False

    lock_blocks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncWith) and is_state_lock_with(node)
    ]
    # Sanity: the slice owns multiple lock sections. (Ticket 007 removed
    # spawn_entity's entity+session insert block, dropping the count from 8.)
    assert len(lock_blocks) >= 7, f"expected the lock-heavy slice's blocks, got {len(lock_blocks)}"

    for block in lock_blocks:
        for inner in ast.walk(block):
            # ``ast.walk`` includes the block itself; skip the with-items,
            # only the body matters. An Await anywhere in the body fails.
            if isinstance(inner, ast.Await):
                pytest.fail(
                    f"await found inside a _state_lock block at line {inner.lineno} "
                    "— the lock is non-reentrant; never await while holding it"
                )


# ---------------------------------------------------------------------------
# Facade wiring — re-exports + delegation are real bound methods
# ---------------------------------------------------------------------------


def test_manager_reexports_personality_helpers() -> None:
    """``from hive.process.manager import _render_auto_personality`` still works."""
    from hive.process.manager import (
        _adapter_config_from_entity as mgr_cfg,
    )
    from hive.process.manager import (
        _render_auto_personality as mgr_render,
    )

    assert mgr_render is _render_auto_personality
    assert mgr_cfg is _adapter_config_from_entity


def test_facade_delegations_are_bound_methods() -> None:
    """The facade exposes lifecycle methods as real (monkeypatchable) methods."""
    from hive.process.manager import ProcessManager

    pm = ProcessManager(router=SimpleNamespace(register=lambda n: None))
    # Private + public delegated names resolve on the instance.
    assert callable(pm.kill_entity)
    assert callable(pm._get_or_create_adapter)
    # The collaborator is wired and back-references the facade.
    assert isinstance(pm.lifecycle, LifecycleManager)
    assert pm.lifecycle._mgr is pm
