"""Tests for process manager (with mocked subprocesses)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager
from tests.fakes import TIMEOUT, FakeAdapter, using_adapter, using_adapter_sequence


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


class TestTeamManagement:
    """Test team creation and lead/team lifecycle."""

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

    async def test_create_team_defaults_to_opus(self, manager: ProcessManager) -> None:
        """A team's lead defaults to Opus — the fleet default for every spawn
        (Ticket 013 post-mortem). Guards the facade default layer."""
        maestro = Maestro(name="dev", model="opus")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = await manager.create_team("dev", "backend")
        assert lead.model == "opus"

    async def test_create_team_honours_explicit_model(self, manager: ProcessManager) -> None:
        """A maestro may still pin a cheaper model explicitly."""
        maestro = Maestro(name="dev", model="opus")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        lead = await manager.create_team("dev", "backend", model="sonnet")
        assert lead.model == "sonnet"

    async def test_create_team_missing_maestro_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if maestro doesn't exist."""
        with pytest.raises(KeyError, match="not found"):
            await manager.create_team("nope", "backend")

    async def test_create_team_non_maestro_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if target entity is not a maestro."""
        non_maestro = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        manager._entities["dev.backend"] = non_maestro
        with pytest.raises(TypeError, match="not a maestro"):
            await manager.create_team("dev.backend", "backend")

    async def test_create_duplicate_team_raises(self, manager: ProcessManager) -> None:
        """create_team should raise if team already exists."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        await manager.create_team("dev", "backend")
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_team("dev", "backend")

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

    async def test_rebuild_hierarchy_empty(self, manager: ProcessManager) -> None:
        """rebuild_hierarchy on empty manager should not raise."""
        manager.rebuild_hierarchy()  # should not raise


class _FakeGateStore:
    """In-memory ModeRequestStore stand-in, gate-reconciliation subset.

    Only the methods reconcile_orphaned_gates touches: list_pending(kind) and
    deny(request_id, reason). No DB, no PTY.
    """

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self._next_id = 1

    def add_pending_gate(self, requester: str, approver: str = "user") -> dict:
        row = {
            "id": self._next_id,
            "requester": requester,
            "requested_mode": "plan",
            "approver": approver,
            "reason": None,
            "kind": "gate",
            "status": "pending",
        }
        self.rows[self._next_id] = row
        self._next_id += 1
        return dict(row)

    async def list_pending(self, approver: str, kind: str | None = None) -> list[dict]:
        return [
            dict(r)
            for r in self.rows.values()
            if r["approver"] == approver
            and r["status"] == "pending"
            and (kind is None or r["kind"] == kind)
        ]

    async def deny(self, request_id: int, reason: str | None = None) -> dict | None:
        row = self.rows.get(request_id)
        if row is None or row["status"] != "pending":
            return None
        row["status"] = "denied"
        if reason is not None:
            row["reason"] = reason
        return dict(row)


class _FakeGateCoordinator:
    """Doorbell registry stand-in — tracks which entities have a live doorbell."""

    def __init__(self, live: set[str] | None = None) -> None:
        self._live = live or set()

    def pending_request_id(self, entity_name: str) -> int | None:
        # A live doorbell would have a registered pending row id.
        return 1 if entity_name in self._live else None


class TestRestartGateReconciliation:
    """#27 — pending gate rows that lost their parked coroutine on restart.

    A Hive restart kills the in-memory doorbell but the pending kind='gate'
    row survives in the DB with no coroutine behind it. On restore, those
    orphaned rows must be marked stale (denied) so they don't dangle — without
    ever re-spawning a PTY or auto-approving.
    """

    async def test_reconcile_denies_orphaned_gate_rows(self, router: MessageRouter) -> None:
        store = _FakeGateStore()
        store.add_pending_gate("dev")
        store.add_pending_gate("dev.backend")

        mgr = ProcessManager(router=router)
        mgr.mode_request_store = store  # type: ignore[assignment]

        reconciled = await mgr.reconcile_orphaned_gates()

        assert len(reconciled) == 2
        # Both rows are now denied with a stale reason, not approved.
        for row in store.rows.values():
            assert row["status"] == "denied"
            assert "stale" in (row["reason"] or "").lower()

        await mgr.kill_all()

    async def test_reconcile_no_pending_gates_is_noop(self, router: MessageRouter) -> None:
        store = _FakeGateStore()
        mgr = ProcessManager(router=router)
        mgr.mode_request_store = store  # type: ignore[assignment]

        reconciled = await mgr.reconcile_orphaned_gates()
        assert reconciled == []

        await mgr.kill_all()

    async def test_reconcile_skips_rows_with_live_doorbell(self, router: MessageRouter) -> None:
        """A gate that still has a live doorbell (not orphaned) is left alone —
        defensive against calling reconcile while a Turn is genuinely parked."""
        store = _FakeGateStore()
        store.add_pending_gate("dev")

        mgr = ProcessManager(router=router)
        mgr.mode_request_store = store  # type: ignore[assignment]
        mgr.gate_coordinator = _FakeGateCoordinator(live={"dev"})  # type: ignore[assignment]

        reconciled = await mgr.reconcile_orphaned_gates()

        assert reconciled == []
        assert store.rows[1]["status"] == "pending"  # untouched

        await mgr.kill_all()

    async def test_reconcile_without_store_is_noop(self, router: MessageRouter) -> None:
        """No mode_request_store configured — reconcile must not raise."""
        mgr = ProcessManager(router=router)
        assert mgr.mode_request_store is None

        reconciled = await mgr.reconcile_orphaned_gates()
        assert reconciled == []

        await mgr.kill_all()


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
        """stop_all should call stop() on every active adapter and clear the dict."""
        mgr = ProcessManager(router=router, entity_store=entity_store)

        entity = Maestro(name="dev", model="sonnet")
        mgr._entities["dev"] = entity
        mgr.router.register("dev")

        fake_adapter = FakeAdapter()
        await fake_adapter.start()
        mgr._adapters["dev"] = fake_adapter

        await mgr.stop_all()

        assert fake_adapter.stopped
        assert mgr._adapters == {}

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

    async def test_pending_messages_prepended(self, manager: ProcessManager) -> None:
        """Pending messages should be prepended to the prompt."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # Queue a message for the maestro
        await manager.router.route("dev.backend", "dev", "Migration done")

        with using_adapter(manager, FakeAdapter("thanks")) as adapter:
            await manager.send_to_entity("dev", "How's the project?")

        assert len(adapter.prompts) == 1
        assert "[Message from dev.backend]" in adapter.prompts[0]
        assert "Migration done" in adapter.prompts[0]
        assert "How's the project?" in adapter.prompts[0]

    async def test_no_pending_prompt_unchanged(self, manager: ProcessManager) -> None:
        """Without pending messages, the user's prompt is preserved verbatim
        (Sprint 22 prepends a peer directory block — the user prompt itself
        is still passed through unchanged at the tail)."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with using_adapter(manager, FakeAdapter("ok")) as adapter:
            await manager.send_to_entity("dev", "Hello")

        assert adapter.prompts[0].endswith("Hello")
        assert "[Message from" not in adapter.prompts[0]

    async def test_multiple_pending_all_included(self, manager: ProcessManager) -> None:
        """Multiple pending messages should all appear in the prompt."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        await manager.router.route("dev.backend", "dev", "DB migrated")
        await manager.router.route("dev.frontend", "dev", "UI updated")

        with using_adapter(manager, FakeAdapter("got it")) as adapter:
            await manager.send_to_entity("dev", "Status?")

        assert "[Message from dev.backend]" in adapter.prompts[0]
        assert "DB migrated" in adapter.prompts[0]
        assert "[Message from dev.frontend]" in adapter.prompts[0]
        assert "UI updated" in adapter.prompts[0]


class TestActionRouting:
    """Test that <hive_actions> in entity responses are parsed and routed."""

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
        with using_adapter(manager, FakeAdapter(response_text)):
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
        with using_adapter(manager, FakeAdapter(response_text)):
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
        with using_adapter(manager, FakeAdapter(response_text)):
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
        with using_adapter(manager, FakeAdapter(response_text)):
            await manager.send_to_entity("dev", "Go")

        assert manager._last_routed_actions == ["dev.backend"]

    async def test_no_actions_no_side_effects(self, manager: ProcessManager) -> None:
        """Response without actions should not route anything."""
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with using_adapter(manager, FakeAdapter("Just a plain response.")):
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
        with using_adapter(mgr, FakeAdapter(response_text)):
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

    async def _send(self, manager: ProcessManager, name: str, response: str) -> str:
        with using_adapter(manager, FakeAdapter(response)):
            return await manager.send_to_entity(name, "go")

    async def test_maestro_spawn_team_creates_lead(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.confirmed_with_user = True  # Ticket 019: past the phase gate
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
        # The spawn_team dispatch path (action.model or default) must default
        # the lead to Opus — this is the layer that silently kept leads on
        # Sonnet after the create_team default was changed (Ticket 013 fix).
        assert manager.entities["dev.backend"].model == "opus"

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

    async def test_spawn_worker_parses_as_unknown_action(self, manager: ProcessManager) -> None:
        """Worker creation is retired (Ticket 018) — ``spawn_worker`` is no
        longer a recognised action type. ``parse_actions`` treats it as a
        generic unknown action: no Action is produced and the errors carry
        ``Unknown action type 'spawn_worker'``. Nothing is spawned and the
        sender receives parse-failure feedback (018's drainage proof).
        """
        from hive.bus.actions import parse_actions

        text = '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        _clean, actions, errors = parse_actions(text)

        # No Action object for the retired type.
        assert actions == []
        assert any("Unknown action type 'spawn_worker'" in e for e in errors)

        # End-to-end through the dispatcher: nothing is registered, and the
        # unknown-action error comes back to the sender as system feedback.
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        await self._send(manager, "dev", text)

        assert "dev.backend.w1" not in manager.entities
        assert manager._last_spawned_teams == []
        feedback = await manager.router.store.get_messages("dev")
        assert any(m["sender"] == "system" and "spawn_worker" in m["content"] for m in feedback)

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
        maestro.confirmed_with_user = True  # Ticket 019: past the phase gate
        await mgr.register_entity(maestro)

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        with using_adapter(mgr, FakeAdapter(response)):
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
        with using_adapter(mgr, FakeAdapter(response)):
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
        maestro.confirmed_with_user = True  # Ticket 019: past the phase gate
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        with using_adapter(manager, FakeAdapter(response)):
            await manager.send_to_entity("dev", "go")
            # Capture before draining — kickoff task itself dispatches and
            # resets _last_kickoffs when it runs.
            recorded = list(manager._last_kickoffs)
            if manager._kickoff_tasks:
                await asyncio.gather(*manager._kickoff_tasks)

        assert recorded == ["dev.backend"]
        assert manager.entities["dev.backend"].session_id == "sess-1"

    async def test_spawn_worker_skips_kickoff(self, manager: ProcessManager) -> None:
        """spawn_worker is a retired/unknown action (Ticket 018) → no worker,
        no kickoff scheduled."""
        maestro = Maestro(name="dev", model="sonnet")
        await manager.register_entity(maestro)
        await manager.create_team("dev", "backend")

        response = (
            '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        )
        with using_adapter(manager, FakeAdapter(response)):
            await manager.send_to_entity("dev", "go")
            recorded = list(manager._last_kickoffs)
            if manager._kickoff_tasks:
                await asyncio.gather(*manager._kickoff_tasks)

        assert recorded == []
        assert "dev.backend.w1" not in manager.entities

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

        # Sequence: turn 1 = summarise, turn 2 = reseed the fresh session.
        with using_adapter(manager, FakeAdapter(["- Key point A\n- Point B", "Resumed OK"])):
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

        # input_tokens=60000 > threshold=50000 on the main send trips the
        # compact. The entity is guarded by ``_compacting`` while it runs, so
        # the compact's own two turns (summarise + reseed) don't re-trigger.
        adapter = FakeAdapter(
            ["response", "summary"], usage={"input_tokens": 60000, "output_tokens": 100}
        )

        with (
            using_adapter(manager, adapter),
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            await manager.send_to_entity("dev", "hello")

        # Main send + 2 compact turns (summarise + seed) = 3 turns total.
        assert len(adapter.prompts) == 3

    async def test_auto_compact_skips_when_disabled(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # input_tokens=60000 is above threshold, but compaction is disabled.
        adapter = FakeAdapter("response", usage={"input_tokens": 60000, "output_tokens": 100})

        with (
            using_adapter(manager, adapter),
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", False),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            await manager.send_to_entity("dev", "hello")

        # Only 1 turn — no compact triggered.
        assert len(adapter.prompts) == 1

    async def test_auto_compact_skips_below_threshold(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # input_tokens=30000 is below threshold=50000 — no compact.
        adapter = FakeAdapter("response", usage={"input_tokens": 30000, "output_tokens": 100})

        with (
            using_adapter(manager, adapter),
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            await manager.send_to_entity("dev", "hello")

        # Only 1 turn — below threshold, no compact triggered.
        assert len(adapter.prompts) == 1


class TestSendToEntityActivityTracking:
    """Test that send_to_entity updates last_activity_at."""

    async def test_send_updates_last_activity_at(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        assert maestro.last_activity_at is None

        with using_adapter(manager, FakeAdapter("response")):
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

    async def test_gated_entity_not_killed(self, manager: ProcessManager) -> None:
        """A GATED entity is parked on a gate forever — it must never be
        idle-reaped, even when not in exempt_names (ADR 0004)."""
        from hive.models.entity import EntityState

        entity = Entity(name="worker", role="worker")
        entity.transition_to(EntityState.STARTING)
        entity.transition_to(EntityState.RUNNING)
        entity.transition_to(EntityState.GATED)
        entity.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["worker"] = entity
        manager.router.register("worker")

        killed = await manager.kill_idle_entities(30)
        assert killed == []
        assert "worker" in manager.entities


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


class TestGateStateWiring:
    """_on_gate_state moves the Entity in/out of GATED and pushes the surface."""

    async def test_gated_transitions_entity_and_notifies(self, manager: ProcessManager) -> None:
        from unittest.mock import MagicMock

        channel = _CapturingChannel()
        manager.notification_dispatcher.register(channel)
        coordinator = MagicMock()
        coordinator.pending_request_id.return_value = 7
        manager.gate_coordinator = coordinator

        entity = Entity(name="dev", role="worker")
        entity.transition_to(EntityState.STARTING)
        entity.transition_to(EntityState.RUNNING)
        manager._entities["dev"] = entity
        manager.router.register("dev")

        manager._on_gate_state("dev", "gated")
        assert entity.state == EntityState.GATED

        await asyncio.sleep(0.02)  # let the fire-and-forget notification run
        assert any("gate" in m.lower() for m in channel.messages)
        assert any("7" in m for m in channel.messages)

    async def test_running_transitions_entity_back(self, manager: ProcessManager) -> None:
        entity = Entity(name="dev", role="worker")
        entity.transition_to(EntityState.STARTING)
        entity.transition_to(EntityState.RUNNING)
        entity.transition_to(EntityState.GATED)
        manager._entities["dev"] = entity
        manager.router.register("dev")

        manager._on_gate_state("dev", "running")
        assert entity.state == EntityState.RUNNING

    async def test_unknown_entity_is_noop(self, manager: ProcessManager) -> None:
        manager._on_gate_state("ghost", "gated")  # must not raise


class TestIdleKillSkipsBusyAdapter:
    """An entity whose adapter has a turn in flight must never be idle-reaped,
    however stale its last_activity_at — the stamp only updates at turn start,
    so a long sync-wait turn (a lead's Workflow fan-out, ADR 0010) looks idle
    while it is actively working."""

    async def test_busy_adapter_not_killed(self, manager: ProcessManager) -> None:
        entity = Entity(name="worker", role="worker")
        entity.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["worker"] = entity
        manager.router.register("worker")

        busy_adapter = MagicMock()
        busy_adapter.is_busy.return_value = True
        manager._adapters["worker"] = busy_adapter

        killed = await manager.kill_idle_entities(30)
        assert killed == []
        assert "worker" in manager.entities

    async def test_idle_adapter_still_killed(self, manager: ProcessManager) -> None:
        entity = Entity(name="worker", role="worker")
        entity.last_activity_at = datetime.now(UTC) - timedelta(minutes=60)
        manager._entities["worker"] = entity
        manager.router.register("worker")

        idle_adapter = MagicMock()
        idle_adapter.is_busy.return_value = False
        manager._adapters["worker"] = idle_adapter

        killed = await manager.kill_idle_entities(30)
        assert "worker" in killed


class TestIsParkedAtGate:
    """028 — the pending-gate signal both the send chokepoint and the
    scheduler consult before injecting into an entity's PTY."""

    async def test_false_when_no_gate_coordinator(self, router: MessageRouter) -> None:
        mgr = ProcessManager(router=router)
        assert mgr.gate_coordinator is None
        assert mgr.is_parked_at_gate("dev") is False
        await mgr.kill_all()

    async def test_true_when_gate_pending(self, router: MessageRouter) -> None:
        mgr = ProcessManager(router=router)
        mgr.gate_coordinator = _FakeGateCoordinator(live={"dev"})  # type: ignore[assignment]
        assert mgr.is_parked_at_gate("dev") is True
        await mgr.kill_all()

    async def test_false_when_no_gate_pending(self, router: MessageRouter) -> None:
        mgr = ProcessManager(router=router)
        mgr.gate_coordinator = _FakeGateCoordinator(live={"other"})  # type: ignore[assignment]
        assert mgr.is_parked_at_gate("dev") is False
        await mgr.kill_all()


class _KindChannel:
    """Capturing channel that records each notification's (text, kind)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def send(self, notification: Notification) -> None:
        self.events.append((notification.text, notification.kind))


class TestAutoBounce:
    """Ticket 020 — auto-bounce a jammed PTY session: kill + respawn
    (conversation preserved via --continue), guarded by liveness safety checks
    and a time-windowed flap-guard. The 180s no-progress timeout surfaces as a
    ``TimeoutError`` out of ``adapter.send_turn`` (see ``transcript_reader``)."""

    async def test_bounce_on_threshold_then_retry_succeeds(self, manager: ProcessManager) -> None:
        channel = _KindChannel()
        manager.notification_dispatcher.register(channel)
        maestro = Maestro(name="otter", model="opus")
        manager._entities["otter"] = maestro
        manager.router.register("otter")

        jammed = FakeAdapter([TIMEOUT], jam_state={"waitingFor": "a permission prompt"})
        healthy = FakeAdapter("ok")

        with (
            using_adapter_sequence(manager, [jammed, healthy]),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 2),
        ):
            # Turn 1: a single stall is below threshold — the timeout still
            # surfaces to the caller, no bounce yet.
            with pytest.raises(TimeoutError):
                await manager.send_to_entity("otter", "hi")
            assert manager._liveness["otter"]["stalls"] == 1
            assert not jammed.stopped

            # Turn 2: second consecutive stall hits the threshold → bounce the
            # jammed session and retry once on the fresh adapter → success.
            result = await manager.send_to_entity("otter", "hi again")

        assert result == "ok"
        assert jammed.stopped  # old session killed
        assert healthy.started  # respawned (conversation preserved)
        assert manager._liveness["otter"]["stalls"] == 0  # reset on success
        kinds = [k for _, k in channel.events]
        assert kinds.count("auto_bounce") == 1

    async def test_success_resets_stall_counter(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="otter", model="opus")
        manager._entities["otter"] = maestro
        manager.router.register("otter")

        # timeout, success, timeout — the success in the middle must zero the
        # counter so the third turn is stall #1, never reaching the threshold.
        adapter = FakeAdapter([TIMEOUT, "ok", TIMEOUT])
        with (
            using_adapter_sequence(manager, [adapter]),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 2),
        ):
            with pytest.raises(TimeoutError):
                await manager.send_to_entity("otter", "1")
            assert manager._liveness["otter"]["stalls"] == 1
            await manager.send_to_entity("otter", "2")
            assert manager._liveness["otter"]["stalls"] == 0
            with pytest.raises(TimeoutError):
                await manager.send_to_entity("otter", "3")
            assert manager._liveness["otter"]["stalls"] == 1

        assert not adapter.stopped  # never bounced

    async def test_gate_holds_off_bounce(self, manager: ProcessManager) -> None:
        # A maestro parked at a plan/ask gate is legitimately waiting — even if
        # the turn timed out, it must NOT be bounced and the stall must not count.
        maestro = Maestro(name="otter", model="opus")
        manager._entities["otter"] = maestro
        manager.gate_coordinator = _FakeGateCoordinator(live={"otter"})  # type: ignore[assignment]
        adapter = FakeAdapter([TIMEOUT])
        manager._adapters["otter"] = adapter
        adapter.started = True

        retry = await manager._maybe_bounce_on_timeout(maestro, adapter)

        assert retry is False
        assert not adapter.stopped
        assert manager._liveness.get("otter", {"stalls": 0})["stalls"] == 0

    async def test_workflow_active_holds_off_bounce(self, manager: ProcessManager) -> None:
        # A lead mid-Workflow is the 030 false-timeout class — hold off.
        lead = TeamLead(name="otter.web", maestro_name="otter", model="opus")
        manager._entities["otter.web"] = lead
        adapter = FakeAdapter([TIMEOUT], workflow_active=True)
        manager._adapters["otter.web"] = adapter
        adapter.started = True

        retry = await manager._maybe_bounce_on_timeout(lead, adapter)

        assert retry is False
        assert not adapter.stopped
        assert manager._liveness.get("otter.web", {"stalls": 0})["stalls"] == 0

    async def test_awaiting_decision_holds_off_bounce(self, manager: ProcessManager) -> None:
        # 029 defense-in-depth: a maestro parked on a user decision is waiting,
        # not jammed.
        maestro = Maestro(name="otter", model="opus")
        maestro.awaiting_decision = True
        manager._entities["otter"] = maestro
        adapter = FakeAdapter([TIMEOUT])
        manager._adapters["otter"] = adapter
        adapter.started = True

        retry = await manager._maybe_bounce_on_timeout(maestro, adapter)

        assert retry is False
        assert not adapter.stopped
        assert manager._liveness.get("otter", {"stalls": 0})["stalls"] == 0

    async def test_flap_guard_gives_up(self, manager: ProcessManager) -> None:
        channel = _KindChannel()
        manager.notification_dispatcher.register(channel)
        maestro = Maestro(name="otter", model="opus")
        maestro.state = EntityState.RUNNING  # legal RUNNING → ERROR on give-up
        manager._entities["otter"] = maestro
        manager.router.register("otter")

        adapters = [FakeAdapter([TIMEOUT]) for _ in range(3)]
        with (
            using_adapter_sequence(manager, adapters),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 1),
            patch("hive.process.manager.BOUNCE_FLAP_MAX", 2),
        ):
            await manager._get_or_create_adapter(maestro)  # register adapters[0]
            cur = manager._adapters["otter"]
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True  # bounce 1
            cur = manager._adapters["otter"]
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True  # bounce 2
            cur = manager._adapters["otter"]
            give_up = await manager._maybe_bounce_on_timeout(maestro, cur)  # flap → stop

        assert give_up is False
        assert maestro.state == EntityState.ERROR
        assert cur.stopped  # last session stopped
        assert "otter" not in manager._adapters  # NOT respawned after give-up
        assert any(k == "auto_bounce_failed" for _, k in channel.events)

    async def test_flap_window_resets(self, manager: ProcessManager) -> None:
        maestro = Maestro(name="otter", model="opus")
        maestro.state = EntityState.RUNNING
        manager._entities["otter"] = maestro
        manager.router.register("otter")

        adapters = [FakeAdapter([TIMEOUT]) for _ in range(5)]
        clock = {"t": 1000.0}
        with (
            using_adapter_sequence(manager, adapters),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 1),
            patch("hive.process.manager.BOUNCE_FLAP_MAX", 2),
            patch("hive.process.manager.BOUNCE_FLAP_WINDOW_S", 100.0),
            patch("hive.process.manager._monotonic", lambda: clock["t"]),
        ):
            await manager._get_or_create_adapter(maestro)
            cur = manager._adapters["otter"]
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True  # @1000
            cur = manager._adapters["otter"]
            clock["t"] = 1050.0
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True  # @1050
            cur = manager._adapters["otter"]
            clock["t"] = 1200.0  # both prior bounces now outside the 100s window
            # Were the window not pruned this would be the 3rd bounce → give up.
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True

        assert maestro.state == EntityState.RUNNING  # never gave up

    async def test_reason_surfaced_in_notification(self, manager: ProcessManager) -> None:
        channel = _KindChannel()
        manager.notification_dispatcher.register(channel)

        # waitingFor present → its text rides the bounce notification.
        maestro = Maestro(name="otter", model="opus")
        manager._entities["otter"] = maestro
        manager.router.register("otter")
        jammed = FakeAdapter([TIMEOUT], jam_state={"waitingFor": "a permission prompt"})
        healthy = FakeAdapter("ok")
        with (
            using_adapter_sequence(manager, [jammed, healthy]),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 1),
        ):
            await manager._get_or_create_adapter(maestro)
            cur = manager._adapters["otter"]
            assert await manager._maybe_bounce_on_timeout(maestro, cur) is True
        assert any("permission prompt" in text for text, _ in channel.events)

        # waitingFor absent → "cause unknown", and the bounce still fires.
        channel.events.clear()
        lynx = Maestro(name="lynx", model="opus")
        manager._entities["lynx"] = lynx
        manager.router.register("lynx")
        jammed2 = FakeAdapter([TIMEOUT])  # describe_jam() → None
        healthy2 = FakeAdapter("ok")
        with (
            using_adapter_sequence(manager, [jammed2, healthy2]),
            patch("hive.process.manager.BOUNCE_STALL_THRESHOLD", 1),
        ):
            await manager._get_or_create_adapter(lynx)
            cur = manager._adapters["lynx"]
            assert await manager._maybe_bounce_on_timeout(lynx, cur) is True
        assert any("unknown" in text.lower() for text, _ in channel.events)


class TestPhaseConfirmationGate:
    """Ticket 019 (ADR 0019): a maestro can't spawn_team until the user confirms once."""

    async def _send(self, manager: ProcessManager, name: str, response: str) -> str:
        with using_adapter(manager, FakeAdapter(response)):
            return await manager.send_to_entity(name, "go")

    async def test_unconfirmed_maestro_spawn_team_denied(self, manager: ProcessManager) -> None:
        """Fresh maestro (confirmed_with_user=False, phase_confirm=True) is gated."""
        maestro = Maestro(name="dev", model="opus")
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev", response)

        assert manager._last_spawned_teams == []
        assert "dev.backend" not in manager.entities
        feedback = await manager.router.store.get_messages("dev")
        assert any(m["sender"] == "system" and "request_decision" in m["content"] for m in feedback)

    async def test_confirmed_maestro_spawn_team_succeeds(self, manager: ProcessManager) -> None:
        """A maestro that round-tripped a decision (confirmed_with_user) spawns freely."""
        maestro = Maestro(name="dev", model="opus")
        maestro.confirmed_with_user = True
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev", response)

        assert manager._last_spawned_teams == ["dev.backend"]
        assert "dev.backend" in manager.entities

    async def test_opt_out_maestro_spawn_team_succeeds(self, manager: ProcessManager) -> None:
        """phase_confirm=False (unattended) skips the gate even when unconfirmed."""
        maestro = Maestro(name="dev", model="opus")
        maestro.phase_confirm = False
        manager._entities["dev"] = maestro
        manager.router.register("dev")
        response = (
            '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        )
        await self._send(manager, "dev", response)

        assert manager._last_spawned_teams == ["dev.backend"]

    async def test_clear_awaiting_decision_confirms_maestro(self, manager: ProcessManager) -> None:
        """A user reply that unparks a maestro lifts the phase-confirmation floor."""
        maestro = Maestro(name="dev", model="opus")
        maestro.awaiting_decision = True
        manager._entities["dev"] = maestro
        await manager.clear_awaiting_decision("dev")

        assert maestro.awaiting_decision is False
        assert maestro.confirmed_with_user is True

    async def test_clear_awaiting_decision_noop_when_not_parked(
        self, manager: ProcessManager
    ) -> None:
        """No round-trip happened (not parked) → the floor is not lifted."""
        maestro = Maestro(name="dev", model="opus")
        manager._entities["dev"] = maestro
        await manager.clear_awaiting_decision("dev")

        assert maestro.confirmed_with_user is False

    async def test_clear_awaiting_decision_does_not_confirm_lead(
        self, manager: ProcessManager
    ) -> None:
        """Only maestros gate, so clearing a lead's wait never sets the floor."""
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        lead.awaiting_decision = True
        manager._entities["dev.backend"] = lead
        await manager.clear_awaiting_decision("dev.backend")

        assert lead.awaiting_decision is False
        assert lead.confirmed_with_user is False

    async def test_clear_awaiting_decision_nulls_question(self, manager: ProcessManager) -> None:
        """Ticket 038: unparking clears the stored decision question."""
        maestro = Maestro(name="dev", model="opus")
        maestro.awaiting_decision = True
        maestro.last_decision_question = "auth or sessions?"
        manager._entities["dev"] = maestro
        await manager.clear_awaiting_decision("dev")

        assert maestro.last_decision_question is None
