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
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hive.models.entity import EntityState
from hive.models.vault import Vault
from hive.process.lifecycle_manager import (
    LifecycleManager,
    _adapter_config_from_entity,
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
        self.harness_detector = object()  # HarnessRuntime only stores it until a turn
        self.personalities_dir = Path("personalities")

        self.audit_calls: list[tuple[str, str | None, dict | None]] = []
        self.notify_calls: list[str] = []
        self.persisted: list[object] = []
        self.sent: list[tuple[str, str]] = []
        self.sent_seed_goal: list[bool] = []
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

    async def send_to_entity(self, name: str, prompt: str, *, seed_goal: bool = False) -> str:
        self.sent.append((name, prompt))
        self.sent_seed_goal.append(seed_goal)
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


def test_adapter_config_maps_entity_fields() -> None:
    """_adapter_config_from_entity carries the entity's model + name across."""
    maestro = Vault(name="dev", model="opus")
    config = _adapter_config_from_entity(maestro)
    assert config.model == "opus"
    assert config.name == "dev"
    assert config.role == "vault"


# ---------------------------------------------------------------------------
# register_maestro / register_entity
# ---------------------------------------------------------------------------


async def test_register_entity_idle_no_spawn(lifecycle: LifecycleManager, mgr: StubManager) -> None:
    """register_entity adds a pre-built entity without spawning an adapter."""
    lead = Vault(name="dev.backend")
    await lifecycle.register_entity(lead)
    assert mgr._entities["dev.backend"] is lead
    assert "dev.backend" in mgr.router.registered
    assert "dev.backend" not in mgr._adapters


async def test_register_entity_rejects_duplicate(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    lead = Vault(name="dev.backend")
    await lifecycle.register_entity(lead)
    with pytest.raises(ValueError, match="already exists"):
        await lifecycle.register_entity(lead)


# ---------------------------------------------------------------------------
# create_team
# ---------------------------------------------------------------------------


class _FixedWorktreeManager:
    """Worktree manager whose ``create`` hands back one fixed on-disk path.

    Lets a test point ``create_team`` at a real git worktree (or a real
    non-git dir) so the ``is_git_repo`` branch of the lead-mode default is
    exercised end to end.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.created: list[tuple[str, str | None]] = []
        self.removed: list[str] = []

    async def create(self, name: str, branch: str | None = None) -> Path:
        self.created.append((name, branch))
        return self.path

    async def remove(self, name: str) -> None:
        self.removed.append(name)


# ---------------------------------------------------------------------------
# kill_entity / kill_all / kill_team / stop_all
# ---------------------------------------------------------------------------


async def test_kill_entity_stops_adapter_and_unregisters(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """kill_entity stops + drops the adapter, removes the entity, audits."""
    maestro = Vault(name="dev", model="sonnet")
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
    mgr._entities["a"] = Vault(name="a", model="sonnet")
    mgr._entities["b"] = Vault(name="b", model="sonnet")
    await lifecycle.kill_all()
    assert set(mgr.killed) == {"a", "b"}


async def test_stop_all_clears_adapters_keeps_entities(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """stop_all stops adapters and clears the registry but keeps entities."""
    maestro = Vault(name="dev", model="sonnet")
    adapter = FakeAdapter()
    mgr._entities["dev"] = maestro
    mgr._adapters["dev"] = adapter

    await lifecycle.stop_all()

    assert adapter.stopped
    assert mgr._adapters == {}
    # Entities survive — restore() rebuilds adapters on next boot.
    assert "dev" in mgr._entities


# ---------------------------------------------------------------------------
# compact_entity / kill_idle_entities
# ---------------------------------------------------------------------------


async def test_compact_entity_requires_session_id(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """Compacting an entity with no session_id raises ValueError."""
    maestro = Vault(name="dev", model="sonnet")
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
    maestro = Vault(name="dev", model="sonnet")
    maestro.session_id = "sess-abc"
    mgr._entities["dev"] = maestro

    summary = await lifecycle.compact_entity("dev")
    assert summary == "summary text"
    assert "dev" in mgr.killed
    # Re-registered IDLE, then reseeded.
    assert mgr._entities["dev"] is maestro
    assert maestro.state == EntityState.IDLE
    assert len(mgr.sent) == 2  # summarize prompt + reseed prompt
    # T007: neither the summarize prompt nor the reseed is a genuine task, so
    # compact must NEVER request /goal seeding — otherwise the reseed (sent on
    # a freshly-cleared session_id) would be wrapped as the entity's loop goal.
    assert mgr.sent_seed_goal == [False, False]
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.compact" in actions


async def test_kill_idle_skips_gated_and_exempt(
    lifecycle: LifecycleManager, mgr: StubManager
) -> None:
    """GATED and exempt entities are never reaped; stale ones are."""
    from datetime import UTC, datetime, timedelta

    stale = datetime.now(UTC) - timedelta(minutes=120)

    idle = Vault(name="idle", model="sonnet")
    idle.last_activity_at = stale
    gated = Vault(name="gated", model="sonnet")
    gated.state = EntityState.GATED
    gated.last_activity_at = stale
    exempt = Vault(name="exempt", model="sonnet")
    exempt.last_activity_at = stale
    mgr._entities.update({"idle": idle, "gated": gated, "exempt": exempt})

    killed = await lifecycle.kill_idle_entities(timeout_minutes=30, exempt_names={"exempt"})

    assert killed == ["idle"]
    assert "gated" not in mgr.killed
    assert "exempt" not in mgr.killed


# ---------------------------------------------------------------------------
# Facade wiring — re-exports + delegation are real bound methods
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Ticket 025 — reconcile_worktrees (crash-recovery: re-adopt + orphan sweep)
# ---------------------------------------------------------------------------

EMPTY_REPORT = {"readopted": [], "pruned": [], "removed": [], "quarantined": []}


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit, so worktrees can be added."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@hive.local"],
        ["git", "config", "user.name", "Hive Test"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return repo


def _lead(name: str) -> Vault:
    maestro, _, team = name.partition(".")
    return Vault(name=name)


def _audited(mgr: StubManager, action: str, target: str) -> bool:
    return any(a[0] == action and a[1] == target for a in mgr.audit_calls)
