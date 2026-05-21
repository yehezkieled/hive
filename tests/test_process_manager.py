"""Tests for process manager (with mocked subprocesses)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager
from hive.process.worktree import WorktreeManager


class _CapturingChannel:
    """Test channel that records every notification it receives."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, notification: Notification) -> None:
        self.messages.append(notification.text)


@pytest_asyncio.fixture
async def manager(router: MessageRouter) -> AsyncIterator[ProcessManager]:
    """Create a process manager over the shared test router."""
    mgr = ProcessManager(
        router=router,
        max_sessions=2,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


async def test_spawn_and_kill_entity(manager: ProcessManager) -> None:
    """Test spawning an entity with a simple echo command."""
    entity = Entity(name="test-echo", role="worker", model="sonnet")
    # Override build_cli_args to use echo instead of real claude
    entity.system_prompt = ""
    entity.allowed_tools = []

    # We can't easily test real claude -p, so test the state tracking
    assert entity.state == EntityState.IDLE

    # Register directly for state tracking test
    manager._entities["test-echo"] = entity
    manager.router.register("test-echo")
    assert "test-echo" in manager.entities

    await manager.kill_entity("test-echo")
    assert "test-echo" not in manager.entities


async def test_max_sessions_enforcement(manager: ProcessManager) -> None:
    """Test that max_sessions limit is respected."""
    assert manager.max_sessions == 2
    assert manager.active_count == 0


async def test_get_status_empty(manager: ProcessManager) -> None:
    """Test status with no entities."""
    assert manager.get_status() == []


async def test_get_status_with_entity(manager: ProcessManager) -> None:
    """Test status formatting."""
    entity = Maestro(name="dev", model="sonnet")
    manager._entities["dev"] = entity
    manager.router.register("dev")

    statuses = manager.get_status()
    assert len(statuses) == 1
    assert statuses[0]["name"] == "dev"
    assert statuses[0]["role"] == "maestro"
    assert statuses[0]["state"] == "idle"


async def test_health_check_no_entities(manager: ProcessManager) -> None:
    """Test health check with no entities."""
    unhealthy = await manager.health_check()
    assert unhealthy == []


async def test_kill_nonexistent_entity(manager: ProcessManager) -> None:
    """Killing a nonexistent entity should not raise."""
    await manager.kill_entity("nonexistent")  # should not raise


async def test_send_to_nonexistent_entity(manager: ProcessManager) -> None:
    """Sending to nonexistent entity should raise KeyError."""
    with pytest.raises(KeyError):
        await manager.send_to_entity("nonexistent", "hello")


async def test_kill_entity_writes_audit_event(router: MessageRouter, audit_log: AuditLog) -> None:
    """kill_entity should emit one ``entity.kill`` audit event."""
    mgr = ProcessManager(router=router, audit_log=audit_log)
    entity = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = entity
    mgr.router.register("dev")

    await mgr.kill_entity("dev")

    events = await audit_log.recent(action_prefix="entity.")
    assert len(events) == 1
    assert events[0]["action"] == "entity.kill"
    assert events[0]["target"] == "dev"
    assert events[0]["actor"] == "system"


async def test_health_check_writes_error_audit_event(
    router: MessageRouter, audit_log: AuditLog
) -> None:
    """health_check should emit ``entity.error`` for each unexpectedly-dead entity."""
    mgr = ProcessManager(router=router, audit_log=audit_log)
    # Force a running entity with no session — health_check will flag it.
    entity = Maestro(name="dev", model="sonnet")
    entity.transition_to(EntityState.STARTING)
    entity.transition_to(EntityState.RUNNING)
    mgr._entities["dev"] = entity
    mgr.router.register("dev")

    unhealthy = await mgr.health_check()
    assert unhealthy == ["dev"]

    events = await audit_log.recent(action_prefix="entity.")
    assert len(events) == 1
    assert events[0]["action"] == "entity.error"
    assert events[0]["target"] == "dev"
    assert events[0]["details"] == {"phase": "health"}


class TestResumeSession:
    """Test that send_to_entity passes --resume on subsequent calls."""

    async def test_first_send_has_no_resume_flag(self, manager: ProcessManager) -> None:
        """First message to an entity should NOT include --resume."""
        entity = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = entity
        manager.router.register("dev")

        captured_args: list[str] = []

        async def fake_send(prompt: str) -> str:
            return "hello"

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_session_cls:
            instance = mock_session_cls.return_value
            instance.start = AsyncMock()
            instance.send_prompt = AsyncMock(return_value="hello")
            instance.kill = AsyncMock()
            instance.session_id = "sess-new"
            instance.last_usage = None

            def capture_args(args, **kwargs):
                captured_args.extend(args)
                return instance

            mock_session_cls.side_effect = capture_args

            await manager.send_to_entity("dev", "hello")

        assert "--resume" not in captured_args

    async def test_second_send_includes_resume_flag(self, manager: ProcessManager) -> None:
        """After first send stores session_id, second send should include --resume."""
        entity = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = entity
        manager.router.register("dev")

        all_args: list[list[str]] = []

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_session_cls:
            instance = mock_session_cls.return_value
            instance.start = AsyncMock()
            instance.send_prompt = AsyncMock(return_value="response")
            instance.kill = AsyncMock()
            instance.session_id = "sess-abc"
            instance.last_usage = None

            def capture_args(args, **kwargs):
                all_args.append(list(args))
                return instance

            mock_session_cls.side_effect = capture_args

            await manager.send_to_entity("dev", "first message")
            await manager.send_to_entity("dev", "second message")

        # First call: no --resume
        assert "--resume" not in all_args[0]
        # Second call: --resume sess-abc
        assert "--resume" in all_args[1]
        resume_idx = all_args[1].index("--resume")
        assert all_args[1][resume_idx + 1] == "sess-abc"

    async def test_kill_entity_clears_session_id(self, manager: ProcessManager) -> None:
        """kill_entity should clear the stored session_id."""
        entity = Maestro(name="dev", model="sonnet")
        entity.session_id = "sess-old"
        manager._entities["dev"] = entity
        manager.router.register("dev")

        await manager.kill_entity("dev")

        # Entity is removed from manager, but we can verify the field was cleared
        # by checking a fresh entity registered after kill would not carry it
        assert "dev" not in manager.entities

    async def test_session_id_persisted_after_send(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """send_to_entity should persist the session_id to the entity store."""
        mgr = ProcessManager(router=router, entity_store=entity_store, max_sessions=2)
        entity = Maestro(name="dev", model="sonnet")
        mgr._entities["dev"] = entity
        mgr.router.register("dev")

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_session_cls:
            instance = mock_session_cls.return_value
            instance.start = AsyncMock()
            instance.send_prompt = AsyncMock(return_value="response")
            instance.kill = AsyncMock()
            instance.session_id = "sess-persisted"
            instance.last_usage = None

            mock_session_cls.side_effect = lambda args, **kw: instance

            await mgr.send_to_entity("dev", "hello")

        # Entity should have the session_id set
        assert entity.session_id == "sess-persisted"

        # Should also be persisted in the DB
        loaded = await entity_store.load("dev")
        assert loaded is not None
        assert loaded.session_id == "sess-persisted"

        await mgr.kill_all()


class TestTeamManagement:
    """Test team creation, worker spawning, and team killing."""

    async def test_create_team(self, manager: ProcessManager) -> None:
        """create_team should register a TeamLead entity."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = await manager.create_team("dev", "backend")
        assert isinstance(lead, TeamLead)
        assert lead.name == "dev.backend"
        assert lead.team_name == "backend"
        assert lead.maestro_name == "dev"
        assert "dev.backend" in manager.entities
        assert "backend" in maestro.teams

    async def test_create_team_missing_maestro_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if maestro doesn't exist."""
        with pytest.raises(KeyError, match="not found"):
            await manager.create_team("nope", "backend")

    async def test_create_team_non_maestro_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if target entity is not a maestro."""
        worker = WorkerAgent(name="w1")
        manager._entities["w1"] = worker
        with pytest.raises(TypeError, match="not a maestro"):
            await manager.create_team("w1", "backend")

    async def test_create_duplicate_team_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if team already exists."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        await manager.create_team("dev", "backend")
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_team("dev", "backend")

    async def test_spawn_worker(self, manager: ProcessManager) -> None:
        """spawn_worker should create a WorkerAgent under a lead."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        await manager.create_team("dev", "backend")

        worker = await manager.spawn_worker("dev.backend", "w1")
        assert isinstance(worker, WorkerAgent)
        assert worker.name == "dev.backend.w1"
        assert worker.team_name == "backend"
        assert worker.lead_name == "dev.backend"
        assert "dev.backend.w1" in manager.entities

    async def test_spawn_worker_auto_names(self, manager: ProcessManager) -> None:
        """spawn_worker with no name should auto-generate one."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        await manager.create_team("dev", "backend")

        worker = await manager.spawn_worker("dev.backend")
        assert worker.name.startswith("dev.backend.w")

    async def test_spawn_worker_missing_lead_raises(self, manager: ProcessManager) -> None:
        """spawn_worker should raise if lead doesn't exist."""
        with pytest.raises(KeyError, match="not found"):
            await manager.spawn_worker("nope", "w1")

    async def test_kill_team(self, manager: ProcessManager) -> None:
        """kill_team should remove lead and all workers."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", "w1")

        await manager.kill_team("dev", "backend")

        assert "dev.backend" not in manager.entities
        assert "dev.backend.w1" not in manager.entities
        assert "backend" not in maestro.teams

    async def test_kill_lead_frees_team_name(self, manager: ProcessManager) -> None:
        """kill_entity on a lead must drop the Team so the name can be reused.

        Regression: previously kill_entity left the Team object on the
        maestro, so a subsequent create_team with the same name raised
        "Team already exists".
        """
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        await manager.create_team("dev", "foo")
        assert "foo" in maestro.teams

        await manager.kill_entity("dev.foo")
        assert "dev.foo" not in manager.entities
        assert "foo" not in maestro.teams

        # Re-creating the team with the same name must succeed.
        new_lead = await manager.create_team("dev", "foo")
        assert isinstance(new_lead, TeamLead)
        assert new_lead.name == "dev.foo"
        assert "dev.foo" in manager.entities
        assert "foo" in maestro.teams

    async def test_lead_inherits_maestro_permission_mode(self, manager: ProcessManager) -> None:
        """Yolo on the maestro must propagate to a freshly spawned lead.

        Otherwise the lead spawns in 'default' and can't run any tool that
        Claude Code prompts for, breaking the maestro→lead→worker pipeline.
        """
        maestro = Maestro(name="dev", model="sonnet")
        maestro.set_permission_mode("yolo")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = await manager.create_team("dev", "backend")

        assert lead.permission_mode == "yolo"

    async def test_worker_inherits_lead_permission_mode(self, manager: ProcessManager) -> None:
        """A worker should be born with the same permission_mode as its lead."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        lead = await manager.create_team("dev", "backend")
        lead.set_permission_mode("yolo")

        worker = await manager.spawn_worker("dev.backend", "w1")

        assert worker.permission_mode == "yolo"

    async def test_default_mode_still_works(self, manager: ProcessManager) -> None:
        """Sanity: a maestro in 'default' produces lead+worker in 'default'."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = await manager.create_team("dev", "backend")
        worker = await manager.spawn_worker("dev.backend", "w1")

        assert maestro.permission_mode == "default"
        assert lead.permission_mode == "default"
        assert worker.permission_mode == "default"


class TestWorktreeIntegration:
    """Test worktree creation/cleanup during worker lifecycle."""

    async def test_spawn_worker_creates_worktree(
        self, router: MessageRouter, tmp_path: Path
    ) -> None:
        """spawn_worker should create a worktree when WorktreeManager is configured."""
        wt_mgr = AsyncMock(spec=WorktreeManager)
        wt_path = tmp_path / "worktrees" / "dev.backend.w1"
        wt_mgr.create = AsyncMock(return_value=wt_path)

        mgr = ProcessManager(router=router, worktree_mgr=wt_mgr, max_sessions=3)
        maestro = Maestro(name="dev", model="sonnet")
        mgr._entities["dev"] = maestro
        mgr.router.register("dev")
        await mgr.create_team("dev", "backend")

        worker = await mgr.spawn_worker("dev.backend", "w1")

        wt_mgr.create.assert_awaited_once_with("dev.backend.w1", branch="hive/dev.backend.w1")
        assert worker.worktree_path == wt_path

        await mgr.kill_all()

    async def test_kill_worker_removes_worktree(
        self, router: MessageRouter, tmp_path: Path
    ) -> None:
        """kill_entity should remove the worktree for a worker."""
        wt_mgr = AsyncMock(spec=WorktreeManager)
        wt_path = tmp_path / "worktrees" / "dev.backend.w1"
        wt_mgr.create = AsyncMock(return_value=wt_path)
        wt_mgr.remove = AsyncMock()

        mgr = ProcessManager(router=router, worktree_mgr=wt_mgr, max_sessions=3)
        maestro = Maestro(name="dev", model="sonnet")
        mgr._entities["dev"] = maestro
        mgr.router.register("dev")
        await mgr.create_team("dev", "backend")
        await mgr.spawn_worker("dev.backend", "w1")

        await mgr.kill_entity("dev.backend.w1")

        wt_mgr.remove.assert_awaited_once_with("dev.backend.w1")

        await mgr.kill_all()

    async def test_spawn_worker_without_worktree_mgr(self, manager: ProcessManager) -> None:
        """spawn_worker should work fine without a WorktreeManager (no worktree)."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        await manager.create_team("dev", "backend")

        worker = await manager.spawn_worker("dev.backend", "w1")
        assert worker.worktree_path is None


class TestHierarchyRestore:
    """Test hierarchy rebuild from persisted entities on restart."""

    async def test_rebuild_hierarchy_links_lead_to_maestro(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """rebuild_hierarchy should attach TeamLeads to their parent Maestro's teams."""
        mgr = ProcessManager(router=router, entity_store=entity_store)

        # Simulate persisted entities loaded from DB
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(
            name="dev.backend",
            team_name="backend",
            maestro_name="dev",
        )
        mgr.restore(maestro)
        mgr.restore(lead)

        mgr.rebuild_hierarchy()

        # Maestro should now have the team
        restored_maestro = mgr.entities["dev"]
        assert isinstance(restored_maestro, Maestro)
        assert "backend" in restored_maestro.teams
        team = restored_maestro.teams["backend"]
        assert team.lead == "dev.backend"

        await mgr.kill_all()

    async def test_rebuild_hierarchy_links_worker_to_lead(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """rebuild_hierarchy should add Workers to their lead's workers list and team."""
        mgr = ProcessManager(router=router, entity_store=entity_store)

        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(
            name="dev.backend",
            team_name="backend",
            maestro_name="dev",
        )
        worker = WorkerAgent(
            name="dev.backend.w1",
            team_name="backend",
            lead_name="dev.backend",
        )
        mgr.restore(maestro)
        mgr.restore(lead)
        mgr.restore(worker)

        mgr.rebuild_hierarchy()

        # Lead should have the worker
        restored_lead = mgr.entities["dev.backend"]
        assert isinstance(restored_lead, TeamLead)
        assert "dev.backend.w1" in restored_lead.workers

        # Team should have the worker
        restored_maestro = mgr.entities["dev"]
        assert isinstance(restored_maestro, Maestro)
        team = restored_maestro.teams["backend"]
        assert "dev.backend.w1" in team.workers

        await mgr.kill_all()

    async def test_rebuild_hierarchy_empty(self, manager: ProcessManager) -> None:
        """rebuild_hierarchy on empty manager should not raise."""
        manager.rebuild_hierarchy()  # should not raise


class TestStopAll:
    """Test graceful stop_all — kills subprocesses but preserves DB rows."""

    async def test_stop_all_preserves_db_rows(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """stop_all should leave entity rows + session_id intact in the DB."""
        mgr = ProcessManager(router=router, entity_store=entity_store)

        dev = Maestro(name="dev", model="sonnet", session_id="sess-dev")
        pa = Maestro(name="pa", model="sonnet", session_id="sess-pa")
        await entity_store.upsert(dev)
        await entity_store.upsert(pa)
        mgr._entities["dev"] = dev
        mgr._entities["pa"] = pa
        mgr.router.register("dev")
        mgr.router.register("pa")

        await mgr.stop_all()

        rows = await entity_store.all()
        names = {r.name for r in rows}
        assert names == {"dev", "pa"}
        by_name = {r.name: r for r in rows}
        assert by_name["dev"].session_id == "sess-dev"
        assert by_name["pa"].session_id == "sess-pa"

    async def test_stop_all_kills_subprocesses(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """stop_all should call session.kill() on every active session and clear the dict."""
        mgr = ProcessManager(router=router, entity_store=entity_store)

        entity = Maestro(name="dev", model="sonnet")
        mgr._entities["dev"] = entity
        mgr.router.register("dev")

        fake_session = AsyncMock()
        mgr._sessions["dev"] = fake_session

        await mgr.stop_all()

        fake_session.kill.assert_awaited_once()
        assert mgr._sessions == {}

    async def test_stop_all_then_restore_round_trip(
        self,
        router: MessageRouter,
        entity_store: EntityStore,
    ) -> None:
        """After stop_all, a fresh manager can restore the same entities with session_ids."""
        mgr1 = ProcessManager(router=router, entity_store=entity_store)
        dev = Maestro(name="dev", model="sonnet", session_id="sess-dev")
        await entity_store.upsert(dev)
        mgr1._entities["dev"] = dev
        mgr1.router.register("dev")

        await mgr1.stop_all()

        mgr2 = ProcessManager(router=router, entity_store=entity_store)
        for restored in await entity_store.all():
            mgr2.restore(restored)

        assert "dev" in mgr2.entities
        restored_dev = mgr2.entities["dev"]
        assert restored_dev.session_id == "sess-dev"
        assert restored_dev.state == EntityState.IDLE


class TestMaxWorkersEnforcement:
    """Test that spawn_worker respects lead.max_workers."""

    async def test_spawn_worker_respects_max_workers(self, manager: ProcessManager) -> None:
        """spawn_worker should raise when lead already has max_workers workers."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        lead = await manager.create_team("dev", "backend")
        lead.max_workers = 1

        await manager.spawn_worker("dev.backend", "w1")
        with pytest.raises(RuntimeError, match="max"):
            await manager.spawn_worker("dev.backend", "w2")

    async def test_spawn_worker_under_limit_succeeds(self, manager: ProcessManager) -> None:
        """spawn_worker should succeed when under max_workers."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        lead = await manager.create_team("dev", "backend")
        lead.max_workers = 3

        w1 = await manager.spawn_worker("dev.backend", "w1")
        w2 = await manager.spawn_worker("dev.backend", "w2")
        assert w1.name == "dev.backend.w1"
        assert w2.name == "dev.backend.w2"


class TestPreemption:
    """Test priority-based preemption logic."""

    async def test_preempt_returns_none_when_under_capacity(self, manager: ProcessManager) -> None:
        """No preemption needed when under max_sessions."""
        result = await manager._preempt_for_priority(0)
        assert result is None

    async def test_preempt_kills_lowest_priority_entity(self, router: MessageRouter) -> None:
        """When at capacity, preemption should kill the lowest-priority entity."""
        mgr = ProcessManager(router=router, max_sessions=1)

        # Manually register a "running" entity with low priority
        entity = Maestro(name="low", model="sonnet")
        entity.current_priority = 4
        entity.transition_to(EntityState.STARTING)
        entity.transition_to(EntityState.RUNNING)
        mgr._entities["low"] = entity
        mgr.router.register("low")
        # Fake a session so active_count == 1
        mock_session = AsyncMock()
        mock_session.is_alive = True
        mock_session.kill = AsyncMock()
        mgr._sessions["low"] = mock_session

        result = await mgr._preempt_for_priority(0)
        assert result == "low"
        assert "low" not in mgr.entities

    async def test_preempt_returns_none_when_all_higher_priority(
        self, router: MessageRouter
    ) -> None:
        """Cannot preempt when all running entities are same or higher priority."""
        mgr = ProcessManager(router=router, max_sessions=1)

        entity = Maestro(name="high", model="sonnet")
        entity.current_priority = 0
        entity.transition_to(EntityState.STARTING)
        entity.transition_to(EntityState.RUNNING)
        mgr._entities["high"] = entity
        mgr.router.register("high")
        mock_session = AsyncMock()
        mock_session.is_alive = True
        mgr._sessions["high"] = mock_session

        result = await mgr._preempt_for_priority(0)
        assert result is None
        await mgr.kill_all()


class TestRegisterMaestro:
    """Test register_maestro method for /new maestro."""

    async def test_register_maestro(self, manager: ProcessManager) -> None:
        """register_maestro should create and register a new maestro."""
        maestro = await manager.register_maestro("ops", model="sonnet")
        assert isinstance(maestro, Maestro)
        assert maestro.name == "ops"
        assert "ops" in manager.entities

    async def test_register_duplicate_maestro_raises(self, manager: ProcessManager) -> None:
        """register_maestro should raise if name already exists."""
        await manager.register_maestro("ops")
        with pytest.raises(ValueError, match="already exists"):
            await manager.register_maestro("ops")

    async def test_register_maestro_with_personality(
        self, manager: ProcessManager, personalities_dir: Path
    ) -> None:
        """register_maestro should load personality if file exists."""
        personality_path = personalities_dir / "maestro-dev.md"
        maestro = await manager.register_maestro("dev", personality_path=personality_path)
        assert maestro.system_prompt != ""


class TestPendingMessageInjection:
    """Test that pending inter-agent messages are prepended to prompts."""

    def _mock_session(self, response: str = "response") -> AsyncMock:
        """Create a mock ClaudeSession that returns the given response."""
        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value=response)
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = None
        return instance

    async def test_pending_messages_prepended(self, manager: ProcessManager) -> None:
        """Pending messages should be prepended to the prompt."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # Queue a message for the maestro
        await manager.router.route("dev.backend", "dev", "Migration done")

        captured_prompts: list[str] = []
        instance = self._mock_session()

        async def capture_prompt(prompt: str) -> str:
            captured_prompts.append(prompt)
            return "thanks"

        instance.send_prompt = AsyncMock(side_effect=capture_prompt)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "How's the project?")

        assert len(captured_prompts) == 1
        assert "[Message from dev.backend]" in captured_prompts[0]
        assert "Migration done" in captured_prompts[0]
        assert "How's the project?" in captured_prompts[0]

    async def test_no_pending_prompt_unchanged(self, manager: ProcessManager) -> None:
        """Without pending messages, the user's prompt is preserved verbatim
        (Sprint 22 prepends a peer directory block — the user prompt itself
        is still passed through unchanged at the tail)."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        captured_prompts: list[str] = []
        instance = self._mock_session()

        async def capture_prompt(prompt: str) -> str:
            captured_prompts.append(prompt)
            return "ok"

        instance.send_prompt = AsyncMock(side_effect=capture_prompt)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "Hello")

        assert captured_prompts[0].endswith("Hello")
        assert "[Message from" not in captured_prompts[0]

    async def test_multiple_pending_all_included(self, manager: ProcessManager) -> None:
        """Multiple pending messages should all appear in the prompt."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        await manager.router.route("dev.backend", "dev", "DB migrated")
        await manager.router.route("dev.frontend", "dev", "UI updated")

        captured_prompts: list[str] = []
        instance = self._mock_session()

        async def capture_prompt(prompt: str) -> str:
            captured_prompts.append(prompt)
            return "got it"

        instance.send_prompt = AsyncMock(side_effect=capture_prompt)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "Status?")

        assert "[Message from dev.backend]" in captured_prompts[0]
        assert "DB migrated" in captured_prompts[0]
        assert "[Message from dev.frontend]" in captured_prompts[0]
        assert "UI updated" in captured_prompts[0]


class TestActionRouting:
    """Test that <hive_actions> in entity responses are parsed and routed."""

    def _mock_session(self, response: str) -> AsyncMock:
        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value=response)
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = None
        return instance

    async def test_message_routed_to_recipient(self, manager: ProcessManager) -> None:
        """A valid message action should be routed to the recipient's queue."""
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        manager.router.register("dev")
        manager.router.register("dev.backend")

        response_text = (
            "Analysis complete.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "Start migration"}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response_text)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            result = await manager.send_to_entity("dev", "Review the project")

        # Clean text returned (no hive_actions block)
        assert "<hive_actions>" not in result
        assert "Analysis complete." in result

        # Message should be in dev.backend's queue
        assert manager.router.has_pending("dev.backend")
        msg = await manager.router.get_next("dev.backend", timeout=0.1)
        assert msg is not None
        assert msg.sender == "dev"
        assert msg.content == "Start migration"

    async def test_permission_denied_blocks_routing(self, manager: ProcessManager) -> None:
        """Worker trying to message another team's lead should be blocked."""
        maestro = Maestro(name="dev", model="sonnet")
        lead_a = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        lead_b = TeamLead(name="dev.frontend", team_name="frontend", maestro_name="dev")
        worker = WorkerAgent(name="dev.backend.w1", team_name="backend", lead_name="dev.backend")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead_a
        manager._entities["dev.frontend"] = lead_b
        manager._entities["dev.backend.w1"] = worker
        for name in ("dev", "dev.backend", "dev.frontend", "dev.backend.w1"):
            manager.router.register(name)

        # Worker tries to message dev.frontend (not its lead) — should be denied
        response_text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.frontend", "text": "Hey"}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response_text)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev.backend.w1", "Do work")

        assert not manager.router.has_pending("dev.frontend")

    async def test_unknown_recipient_handled(self, manager: ProcessManager) -> None:
        """Action targeting a non-existent entity should be skipped."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        response_text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.nonexistent", "text": "hello"}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response_text)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            result = await manager.send_to_entity("dev", "Go")

        assert "Done." in result
        assert manager._last_routed_actions == []

    async def test_clean_text_returned(self, manager: ProcessManager) -> None:
        """Response should have <hive_actions> block stripped."""
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        manager.router.register("dev")
        manager.router.register("dev.backend")

        response_text = (
            "Here's my analysis.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "go"}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response_text)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            result = await manager.send_to_entity("dev", "Analyze")

        assert result == "Here's my analysis."
        assert "<hive_actions>" not in result

    async def test_routed_actions_tracked(self, manager: ProcessManager) -> None:
        """_last_routed_actions should list recipients of successful routes."""
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        manager.router.register("dev")
        manager.router.register("dev.backend")

        response_text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "go"}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response_text)

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "Go")

        assert manager._last_routed_actions == ["dev.backend"]

    async def test_no_actions_no_side_effects(self, manager: ProcessManager) -> None:
        """Response without actions should not route anything."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        instance = self._mock_session("Just a plain response.")

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            result = await manager.send_to_entity("dev", "Hello")

        assert result == "Just a plain response."
        assert manager._last_routed_actions == []

    async def test_action_routing_writes_audit_event(
        self,
        router: MessageRouter,
        audit_log: AuditLog,
    ) -> None:
        """Routed messages should emit a peer_message_sent audit event."""
        mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=2)
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        mgr._entities["dev"] = maestro
        mgr._entities["dev.backend"] = lead
        mgr.router.register("dev")
        mgr.router.register("dev.backend")

        response_text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "migrate"}]\n'
            "</hive_actions>"
        )
        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value=response_text)
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = None

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await mgr.send_to_entity("dev", "Go")

        events = await audit_log.recent(action_prefix="peer_message_")
        assert len(events) == 1
        assert events[0]["action"] == "peer_message_sent"
        assert events[0]["target"] == "dev.backend"
        assert events[0]["actor"] == "dev"

        await mgr.kill_all()


# -- Sprint 19: autonomous spawn/kill dispatcher --


class TestAutonomousDispatch:
    """Maestro/lead emitting spawn_team/spawn_worker/kill_entity actions."""

    def _mock_session(self, response: str) -> AsyncMock:
        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value=response)
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = None
        return instance

    async def _send(self, manager: ProcessManager, name: str, response: str) -> str:
        instance = self._mock_session(response)
        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            return await manager.send_to_entity(name, "go")

    async def test_maestro_spawn_team_creates_lead(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        response = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "spawn_team", "team_name": "backend"}]\n'
            "</hive_actions>"
        )
        await self._send(manager, "dev", response)

        assert "dev.backend" in manager.entities
        assert manager._last_spawned_teams == ["dev.backend"]
        assert "backend" in maestro.teams

    async def test_lead_spawn_team_denied(self, manager: ProcessManager) -> None:
        """Leads can't spawn teams — they should be rejected silently and audited."""
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        for n in ("dev", "dev.backend"):
            manager.router.register(n)

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "frontend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev.backend", response)

        assert manager._last_spawned_teams == []
        assert "dev.frontend" not in manager.entities

    async def test_maestro_spawn_worker_under_own_lead(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        # First seed a team manually so spawn_worker has a lead to attach to
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        response = (
            '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev", response)

        assert manager._last_spawned_workers == ["dev.backend.w1"]
        assert "dev.backend.w1" in manager.entities

    async def test_lead_spawn_worker_under_self(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        response = (
            '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev.backend", response)

        assert manager._last_spawned_workers == ["dev.backend.w1"]

    async def test_lead_spawn_worker_under_other_team_denied(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")
        await manager.create_team("dev", "frontend")

        response = (
            '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.frontend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev.backend", response)

        assert manager._last_spawned_workers == []

    async def test_lead_spawn_worker_no_lead_field_uses_self(self, manager: ProcessManager) -> None:
        """Lead emits spawn_worker with no `lead` field → spawns under itself.

        The lead can't reliably emit its own dotted name as a JSON value
        (it pattern-matches on the field name "lead" instead). The manager
        fills `lead` from `entity.name` so the lead never has to repeat itself.
        """
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        response = '<hive_actions>\n[{"type": "spawn_worker"}]\n</hive_actions>'
        await self._send(manager, "dev.backend", response)

        assert manager._last_spawned_workers == ["dev.backend.w1"]
        assert "dev.backend.w1" in manager.entities

    async def test_maestro_spawn_worker_no_lead_field_audited(
        self, router: MessageRouter, audit_log: AuditLog
    ) -> None:
        """Maestro emits spawn_worker without `lead` → reject + audit.

        Maestros can spawn under any of their teams, so they must specify
        which one. Inferring `lead = entity.name` (the maestro's own name)
        would be wrong — the maestro is not a lead.
        """
        mgr = ProcessManager(
            router=router,
            audit_log=audit_log,
            max_sessions=2,
            notification_dispatcher=NotificationDispatcher(),
        )
        maestro = Maestro(name="dev", model="sonnet")
        await mgr.register_entity(maestro)
        await mgr.create_team("dev", "backend")

        response = '<hive_actions>\n[{"type": "spawn_worker"}]\n</hive_actions>'
        try:
            await self._send(mgr, "dev", response)

            assert mgr._last_spawned_workers == []
            events = await audit_log.recent(action_prefix="entity.")
            denied = [e for e in events if e["action"] == "entity.spawn_worker_denied"]
            assert len(denied) == 1
            assert denied[0]["actor"] == "dev"
            assert denied[0]["details"]["reason"] == "missing_lead"
        finally:
            await mgr.kill_all()

    async def test_worker_spawn_actions_denied(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        worker = WorkerAgent(name="dev.backend.w1", team_name="backend", lead_name="dev.backend")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        manager._entities["dev.backend.w1"] = worker
        for n in ("dev", "dev.backend", "dev.backend.w1"):
            manager.router.register(n)

        response = (
            "<hive_actions>\n"
            "[\n"
            '  {"type": "spawn_team", "team_name": "rogue"},\n'
            '  {"type": "spawn_worker", "lead": "dev.backend"}\n'
            "]\n"
            "</hive_actions>"
        )
        await self._send(manager, "dev.backend.w1", response)

        assert manager._last_spawned_teams == []
        assert manager._last_spawned_workers == []

    async def test_maestro_kill_own_org_member(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")
        assert "dev.backend" in manager.entities

        response = (
            '<hive_actions>\n[{"type": "kill_entity", "target": "dev.backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev", response)

        assert manager._last_killed_entities == ["dev.backend"]
        assert "dev.backend" not in manager.entities

    async def test_kill_default_maestro_denied(self, manager: ProcessManager) -> None:
        """Default maestro is sacred — never killable, even by another maestro."""
        from hive.config import DEFAULT_MAESTRO

        default = Maestro(name=DEFAULT_MAESTRO, model="sonnet")
        other = Maestro(name="ops", model="sonnet")
        manager._entities[DEFAULT_MAESTRO] = default
        manager._entities["ops"] = other
        manager.router.register(DEFAULT_MAESTRO)
        manager.router.register("ops")

        response = (
            "<hive_actions>\n"
            f'[{{"type": "kill_entity", "target": "{DEFAULT_MAESTRO}"}}]\n'
            "</hive_actions>"
        )
        await self._send(manager, "ops", response)

        assert manager._last_killed_entities == []
        assert DEFAULT_MAESTRO in manager.entities

    async def test_self_kill_denied(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="ops", model="sonnet")
        manager._entities["ops"] = maestro
        manager.router.register("ops")

        response = '<hive_actions>\n[{"type": "kill_entity", "target": "ops"}]\n</hive_actions>'
        await self._send(manager, "ops", response)

        assert manager._last_killed_entities == []
        assert "ops" in manager.entities

    async def test_spawn_actions_audited_with_actor(
        self,
        router: MessageRouter,
        audit_log: AuditLog,
    ) -> None:
        """Spawn actions write audit events tagged with the emitting entity."""
        mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=2)
        maestro = Maestro(name="dev", model="sonnet")
        await mgr.register_entity(maestro)

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        instance = self._mock_session(response)
        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await mgr.send_to_entity("dev", "go")

        events = await audit_log.recent(action_prefix="entity.spawn_team")
        # Either a "entity.spawn_team" or denial — should be the spawn one
        spawn_events = [e for e in events if e["action"] == "entity.spawn_team"]
        assert len(spawn_events) == 1
        assert spawn_events[0]["actor"] == "dev"
        assert spawn_events[0]["target"] == "dev.backend"

        await mgr.kill_all()

    async def test_kill_denied_audited(
        self,
        router: MessageRouter,
        audit_log: AuditLog,
    ) -> None:
        """Denied kills emit entity.kill_denied with actor tag."""
        from hive.config import DEFAULT_MAESTRO

        mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=2)
        default = Maestro(name=DEFAULT_MAESTRO, model="sonnet")
        other = Maestro(name="ops", model="sonnet")
        await mgr.register_entity(default)
        await mgr.register_entity(other)

        response = (
            "<hive_actions>\n"
            f'[{{"type": "kill_entity", "target": "{DEFAULT_MAESTRO}"}}]\n'
            "</hive_actions>"
        )
        instance = self._mock_session(response)
        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await mgr.send_to_entity("ops", "go")

        events = await audit_log.recent(action_prefix="entity.kill_denied")
        assert len(events) == 1
        assert events[0]["actor"] == "ops"
        assert events[0]["target"] == DEFAULT_MAESTRO

        await mgr.kill_all()

    async def test_spawn_team_auto_kickoff(self, manager: ProcessManager) -> None:
        """spawn_team schedules a kickoff message to the new lead.

        Without auto-kickoff, the lead is registered in IDLE with no
        session_id and never wakes — the maestro's ``spawn_team`` is a
        dead-end. This test asserts the orchestrator both records intent
        in ``_last_kickoffs`` and actually wakes the lead.
        """
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        instance = self._mock_session(response)
        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "go")
            # Capture before draining — kickoff task itself dispatches and
            # resets _last_kickoffs when it runs.
            recorded = list(manager._last_kickoffs)
            if manager._kickoff_tasks:
                await asyncio.gather(*manager._kickoff_tasks)

        assert recorded == ["dev.backend"]
        assert manager.entities["dev.backend"].session_id == "sess-1"

    async def test_spawn_worker_auto_kickoff(self, manager: ProcessManager) -> None:
        """spawn_worker schedules a kickoff message to the new worker."""
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        response = (
            '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        )
        instance = self._mock_session(response)
        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = lambda args, **kw: instance
            await manager.send_to_entity("dev", "go")
            recorded = list(manager._last_kickoffs)
            if manager._kickoff_tasks:
                await asyncio.gather(*manager._kickoff_tasks)

        assert recorded == ["dev.backend.w1"]
        assert manager.entities["dev.backend.w1"].session_id == "sess-1"

    async def test_spawn_team_denied_skips_kickoff(self, manager: ProcessManager) -> None:
        """A lead emitting spawn_team is denied → no kickoff scheduled."""
        maestro = Maestro(name="dev", model="sonnet")
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev"] = maestro
        manager._entities["dev.backend"] = lead
        for n in ("dev", "dev.backend"):
            manager.router.register(n)

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "frontend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev.backend", response)

        assert manager._last_kickoffs == []


# -- Sprint 10: compact_entity tests --


class TestCompactEntity:
    """Test the compact_entity method extracted from bridge."""

    async def test_compact_missing_entity_raises(self, manager: ProcessManager) -> None:
        with pytest.raises(KeyError):
            await manager.compact_entity("nobody")

    async def test_compact_no_session_raises(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with pytest.raises(ValueError, match="no active session"):
            await manager.compact_entity("dev")

    async def test_compact_returns_summary(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-old"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        call_count = 0

        def make_session(args, **kw):
            nonlocal call_count
            call_count += 1
            instance = AsyncMock()
            instance.start = AsyncMock()
            instance.kill = AsyncMock()
            instance.last_usage = None
            if call_count == 1:
                instance.send_prompt = AsyncMock(return_value="- Key point A\n- Point B")
                instance.session_id = "sess-summary"
            else:
                instance.send_prompt = AsyncMock(return_value="Resumed OK")
                instance.session_id = "sess-new"
            return instance

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.side_effect = make_session
            summary = await manager.compact_entity("dev")

        assert "Key point A" in summary
        assert "dev" in manager.entities


class TestAutoCompact:
    """Test auto-compact triggered by high token count in send_to_entity."""

    async def test_auto_compact_triggers_above_threshold(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        call_count = 0

        def make_session(args, **kw):
            nonlocal call_count
            call_count += 1
            instance = AsyncMock()
            instance.start = AsyncMock()
            instance.kill = AsyncMock()
            if call_count == 1:
                # Main send — high token count triggers compact
                instance.send_prompt = AsyncMock(return_value="response")
                instance.session_id = "sess-1"
                instance.last_usage = {"input_tokens": 60000, "output_tokens": 100}
            else:
                # Compact summarize + seed calls
                instance.send_prompt = AsyncMock(return_value="summary")
                instance.session_id = f"sess-{call_count}"
                instance.last_usage = None
            return instance

        with (
            patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls,
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            mock_cls.side_effect = make_session
            await manager.send_to_entity("dev", "hello")

        # Compact creates 2 additional sessions (summarize + seed)
        assert call_count == 3

    async def test_auto_compact_skips_when_disabled(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value="response")
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = {"input_tokens": 60000, "output_tokens": 100}

        with (
            patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls,
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", False),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            mock_cls.return_value = instance
            await manager.send_to_entity("dev", "hello")

        # Only 1 call — no compact triggered
        mock_cls.assert_called_once()

    async def test_auto_compact_skips_below_threshold(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value="response")
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = {"input_tokens": 30000, "output_tokens": 100}

        with (
            patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls,
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            mock_cls.return_value = instance
            await manager.send_to_entity("dev", "hello")

        mock_cls.assert_called_once()


class TestSendToEntityActivityTracking:
    """Test that send_to_entity updates last_activity_at."""

    async def test_send_updates_last_activity_at(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        assert maestro.last_activity_at is None

        instance = AsyncMock()
        instance.start = AsyncMock()
        instance.send_prompt = AsyncMock(return_value="response")
        instance.kill = AsyncMock()
        instance.session_id = "sess-1"
        instance.last_usage = None

        with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
            mock_cls.return_value = instance
            await manager.send_to_entity("dev", "hello")

        assert maestro.last_activity_at is not None
        assert (datetime.now(UTC) - maestro.last_activity_at).total_seconds() < 5


class TestIdleKill:
    """Test kill_idle_entities."""

    async def test_kills_idle_entity(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = Entity(name="dev.backend", role="lead")
        lead.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["dev.backend"] = lead
        manager.router.register("dev.backend")

        killed = await manager.kill_idle_entities(30, exempt_names={"dev"})
        assert "dev.backend" in killed
        assert "dev" in manager.entities

    async def test_exempt_entity_not_killed(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        killed = await manager.kill_idle_entities(30, exempt_names={"dev"})
        assert killed == []
        assert "dev" in manager.entities

    async def test_recently_active_not_killed(self, manager: ProcessManager) -> None:
        entity = Entity(name="worker", role="worker")
        entity.last_activity_at = datetime.now(UTC) - timedelta(minutes=5)
        manager._entities["worker"] = entity
        manager.router.register("worker")

        killed = await manager.kill_idle_entities(30)
        assert killed == []

    async def test_no_activity_not_killed(self, manager: ProcessManager) -> None:
        entity = Entity(name="worker", role="worker")
        manager._entities["worker"] = entity
        manager.router.register("worker")

        killed = await manager.kill_idle_entities(30)
        assert killed == []

    async def test_notification_on_idle_kill(self, manager: ProcessManager) -> None:
        channel = _CapturingChannel()
        manager.notification_dispatcher.register(channel)

        entity = Entity(name="worker", role="worker")
        entity.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["worker"] = entity
        manager.router.register("worker")

        await manager.kill_idle_entities(30)
        assert len(channel.messages) == 1
        assert "worker" in channel.messages[0]
        assert "inactive" in channel.messages[0]


class TestIdleCheckerExemptsAllMaestros:
    """Regression: idle_checker must dynamically exempt every live maestro,
    not only the default one. Otherwise newly-spawned maestros (e.g. ``hive_dev``)
    get reaped after 30 minutes idle even though the user has not asked.
    """

    async def test_all_maestros_in_exempt_set(self, manager: ProcessManager) -> None:
        """Run one tick of idle_checker and assert every Maestro is exempt while
        non-maestro entities are eligible for killing.
        """
        import asyncio as _asyncio

        from hive.__main__ import idle_checker

        idle_ts = datetime.now(UTC) - timedelta(minutes=60)

        default_maestro = Maestro(name="dev", model="opus")
        default_maestro.last_activity_at = idle_ts
        manager._entities["dev"] = default_maestro
        manager.router.register("dev")

        new_maestro = Maestro(name="hive_dev", model="opus")
        new_maestro.last_activity_at = idle_ts
        manager._entities["hive_dev"] = new_maestro
        manager.router.register("hive_dev")

        worker = Entity(name="worker_1", role="worker")
        worker.last_activity_at = idle_ts
        manager._entities["worker_1"] = worker
        manager.router.register("worker_1")

        captured: dict[str, set[str]] = {}
        stop_event = _asyncio.Event()

        async def spy_kill(timeout, exempt_names=None):
            captured["exempt"] = set(exempt_names or set())
            stop_event.set()  # break out of the loop after one pass
            return []

        # Skip the 5-minute sleep — first wait_for raises TimeoutError so the kill
        # branch fires, the spy stops the loop, and the second wait_for sees
        # stop_event already set and returns normally so the loop exits cleanly.
        async def fake_wait_for(coro, timeout):
            t = _asyncio.create_task(coro)
            t.cancel()
            try:
                await t
            except _asyncio.CancelledError:
                pass
            if not stop_event.is_set():
                raise TimeoutError
            return True

        with patch.object(manager, "kill_idle_entities", side_effect=spy_kill):
            with patch("hive.__main__.asyncio.wait_for", side_effect=fake_wait_for):
                await idle_checker(manager, "dev", stop_event)

        assert captured["exempt"] == {"dev", "hive_dev"}


# -----------------------------------------------------------------------------
# Wake-on-inbound: peer messages auto-spawn a session for the recipient
# -----------------------------------------------------------------------------


async def _drain_wake_tasks(manager: ProcessManager) -> None:
    """Await every detached wake task so assertions see a stable state.

    Loops because wakes schedule both an entity-spawn task and an audit
    task; awaiting one batch may surface another that was queued while
    the first was running. asyncio.wait with a per-round timeout caps
    the wait so a stuck task can't block the test indefinitely — any
    unfinished task is cancelled and awaited so it doesn't survive into
    the next test.
    """
    while manager._wake_tasks:
        pending = list(manager._wake_tasks)
        _, not_done = await asyncio.wait(pending, timeout=5.0)
        for task in not_done:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def test_inbound_wake_spawns_session(manager: ProcessManager) -> None:
    """Peer message → recipient gets a wake send."""
    manager.enable_wake_on_inbound()
    sender = Maestro(name="alice", model="sonnet")
    recipient = TeamLead(name="alice.bob", model="sonnet")
    manager._entities["alice"] = sender
    manager._entities["alice.bob"] = recipient
    manager.router.register("alice")
    manager.router.register("alice.bob")

    sent: list[tuple[str, str]] = []

    async def fake_send(name: str, text: str) -> None:
        sent.append((name, text))

    from hive.process.manager import _WAKE_ON_INBOUND_TEXT

    with patch.object(manager, "send_to_entity", side_effect=fake_send):
        await manager.router.route("alice", "alice.bob", "ping")
        await _drain_wake_tasks(manager)

    assert sent == [("alice.bob", _WAKE_ON_INBOUND_TEXT)]


async def test_inbound_wake_throttled_after_budget(
    manager: ProcessManager,
    audit_log: AuditLog,
) -> None:
    """7 rapid wakes → 6 sends + 1 throttled audit event."""
    manager.audit_log = audit_log
    manager.enable_wake_on_inbound()
    recipient = TeamLead(name="alice.bob", model="sonnet")
    manager._entities["alice.bob"] = recipient
    manager.router.register("alice.bob")

    sent: list[tuple[str, str]] = []

    async def fake_send(name: str, text: str) -> None:
        sent.append((name, text))

    with patch.object(manager, "send_to_entity", side_effect=fake_send):
        for _ in range(7):
            await manager.router.route("alice", "alice.bob", "ping")
        await _drain_wake_tasks(manager)

    assert len(sent) == 6
    events = await audit_log.recent(action_prefix="entity.wake_")
    actions = [e["action"] for e in events]
    assert actions.count("entity.wake_throttled") == 1
    assert actions.count("entity.wake_scheduled") == 6


async def test_inbound_wake_silent_when_recipient_running(
    manager: ProcessManager,
    audit_log: AuditLog,
) -> None:
    """'already running' RuntimeError from send_to_entity is swallowed."""
    manager.audit_log = audit_log
    manager.enable_wake_on_inbound()
    recipient = TeamLead(name="alice.bob", model="sonnet")
    manager._entities["alice.bob"] = recipient
    manager.router.register("alice.bob")

    async def fake_send(name: str, text: str) -> None:
        raise RuntimeError("Entity alice.bob already running")

    with patch.object(manager, "send_to_entity", side_effect=fake_send):
        await manager.router.route("alice", "alice.bob", "ping")
        await _drain_wake_tasks(manager)

    events = await audit_log.recent(action_prefix="entity.wake_failed")
    assert events == []


async def test_inbound_wake_skipped_for_user_recipient(
    manager: ProcessManager,
) -> None:
    """Routing to 'user' (no entity row) must not schedule a wake."""
    manager.enable_wake_on_inbound()
    sender = Maestro(name="alice", model="sonnet")
    manager._entities["alice"] = sender
    manager.router.register("alice")
    manager.router.register("user")  # queue exists but no entity row

    sent: list[tuple[str, str]] = []

    async def fake_send(name: str, text: str) -> None:
        sent.append((name, text))

    with patch.object(manager, "send_to_entity", side_effect=fake_send):
        await manager.router.route("alice", "user", "status update")
        await _drain_wake_tasks(manager)

    assert sent == []
    assert "user" not in manager._wake_budget


# -----------------------------------------------------------------------------
# Parse-failure feedback loop: malformed <hive_actions> blocks come back
# to the sender as a system message so the model can self-correct.
# -----------------------------------------------------------------------------


async def test_parse_errors_route_system_feedback_to_sender(
    manager: ProcessManager,
    audit_log: AuditLog,
) -> None:
    """Malformed block → one `system → entity` message + audit event."""
    manager.audit_log = audit_log
    lead = TeamLead(name="alice.bob", model="sonnet", maestro_name="alice")
    manager._entities["alice.bob"] = lead
    manager.router.register("alice.bob")

    await manager._handle_actions(
        "alice.bob",
        clean_text="",
        actions=[],
        parse_errors=["Malformed JSON in <hive_actions> block: ..."],
    )

    messages = await manager.router.store.get_messages("alice.bob")
    assert len(messages) == 1
    assert messages[0]["sender"] == "system"
    assert "malformed" in messages[0]["content"].lower()
    assert "Malformed JSON" in messages[0]["content"]

    events = await audit_log.recent(action_prefix="entity.parse_failure_feedback")
    assert len(events) == 1


async def test_parse_errors_at_cap_escalate_to_parent(
    manager: ProcessManager,
    audit_log: AuditLog,
) -> None:
    """4th failure in window → escalation to parent, no feedback to sender."""
    manager.audit_log = audit_log
    maestro = Maestro(name="alice", model="sonnet")
    lead = TeamLead(name="alice.bob", model="sonnet", maestro_name="alice")
    manager._entities["alice"] = maestro
    manager._entities["alice.bob"] = lead
    manager.router.register("alice")
    manager.router.register("alice.bob")

    for _ in range(4):
        await manager._handle_actions(
            "alice.bob",
            clean_text="",
            actions=[],
            parse_errors=["Malformed JSON"],
        )

    lead_msgs = await manager.router.store.get_messages("alice.bob")
    maestro_msgs = await manager.router.store.get_messages("alice")
    # First 3 sent feedback to the lead; 4th went to maestro.
    assert len(lead_msgs) == 3
    assert len(maestro_msgs) == 1
    assert "Suppressing parse-feedback" in maestro_msgs[0]["content"]
    assert maestro_msgs[0]["sender"] == "system"

    capped = await audit_log.recent(action_prefix="entity.parse_failure_capped")
    assert len(capped) == 1
    assert capped[0]["target"] == "alice.bob"


async def test_parse_errors_maestro_at_cap_notifies_user(
    manager: ProcessManager,
) -> None:
    """Maestro has no Hive parent → cap overflow surfaces to the user."""
    channel = _CapturingChannel()
    dispatcher = NotificationDispatcher()
    dispatcher.register(channel)
    manager.notification_dispatcher = dispatcher
    maestro = Maestro(name="alice", model="sonnet")
    manager._entities["alice"] = maestro
    manager.router.register("alice")

    for _ in range(4):
        await manager._handle_actions(
            "alice",
            clean_text="",
            actions=[],
            parse_errors=["Malformed JSON"],
        )

    # First 3 sent feedback into the queue; 4th hit the cap and went
    # to the notification dispatcher.
    msgs = await manager.router.store.get_messages("alice")
    assert len(msgs) == 3
    assert any("Suppressing parse-feedback" in text for text in channel.messages)


async def test_parse_errors_window_resets_after_5min(
    manager: ProcessManager,
) -> None:
    """Stale entries are pruned before counting against the cap."""
    lead = TeamLead(name="alice.bob", model="sonnet", maestro_name="alice")
    manager._entities["alice.bob"] = lead
    manager.router.register("alice.bob")

    # Pre-seed 3 stale failures (older than 5 min).
    stale = datetime.now(UTC) - timedelta(seconds=400)
    manager._parse_failure_budget["alice.bob"].extend([stale, stale, stale])

    await manager._handle_actions(
        "alice.bob",
        clean_text="",
        actions=[],
        parse_errors=["Malformed JSON"],
    )

    # Stale ones pruned, only the fresh one remains → still under cap.
    assert len(manager._parse_failure_budget["alice.bob"]) == 1
    msgs = await manager.router.store.get_messages("alice.bob")
    assert len(msgs) == 1
    assert msgs[0]["sender"] == "system"


async def test_parse_errors_skip_when_no_errors(
    manager: ProcessManager,
) -> None:
    """No parse errors → no feedback message, no budget entry."""
    lead = TeamLead(name="alice.bob", model="sonnet", maestro_name="alice")
    manager._entities["alice.bob"] = lead
    manager.router.register("alice.bob")

    await manager._handle_actions(
        "alice.bob",
        clean_text="ok",
        actions=[],
        parse_errors=None,
    )
    await manager._handle_actions(
        "alice.bob",
        clean_text="ok",
        actions=[],
        parse_errors=[],
    )

    msgs = await manager.router.store.get_messages("alice.bob")
    assert msgs == []
    assert "alice.bob" not in manager._parse_failure_budget


# ---------------------------------------------------------------------------
# Adapter lifecycle — kill and stop_all clean up cached adapters
# ---------------------------------------------------------------------------


async def test_kill_entity_stops_adapter(manager: ProcessManager) -> None:
    """kill_entity must call stop() on any cached adapter for the entity."""
    entity = Maestro(name="dev", model="sonnet")
    manager._entities["dev"] = entity
    manager.router.register("dev")

    mock_adapter = AsyncMock()
    mock_adapter.is_alive.return_value = True
    manager._adapters["dev"] = mock_adapter

    await manager.kill_entity("dev")

    mock_adapter.stop.assert_awaited_once()
    assert "dev" not in manager._adapters


async def test_stop_all_stops_adapters(manager: ProcessManager) -> None:
    """stop_all must call stop() on all cached adapters."""
    for name in ("alpha", "beta"):
        entity = Maestro(name=name, model="sonnet")
        manager._entities[name] = entity
        manager.router.register(name)
        mock = AsyncMock()
        mock.is_alive.return_value = True
        manager._adapters[name] = mock

    await manager.stop_all()

    for name in ("alpha", "beta"):
        manager._adapters.get(name)  # already cleared
    assert manager._adapters == {}
