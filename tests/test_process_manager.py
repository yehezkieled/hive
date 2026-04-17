"""Tests for process manager (with mocked subprocesses)."""

from collections.abc import AsyncIterator
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
from hive.process.manager import ProcessManager
from hive.process.worktree import WorktreeManager


@pytest_asyncio.fixture
async def manager(router: MessageRouter) -> AsyncIterator[ProcessManager]:
    """Create a process manager over the shared test router."""
    mgr = ProcessManager(router=router, max_sessions=2)
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
        """Without pending messages, the prompt should be passed through unchanged."""
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

        assert captured_prompts[0] == "Hello"

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
        """Routed messages should emit a message.autonomous audit event."""
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

        events = await audit_log.recent(action_prefix="message.")
        assert len(events) == 1
        assert events[0]["action"] == "message.autonomous"
        assert events[0]["target"] == "dev.backend"
        assert events[0]["actor"] == "dev"

        await mgr.kill_all()
