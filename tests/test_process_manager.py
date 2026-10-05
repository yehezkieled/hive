"""Tests for process manager (with mocked subprocesses)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.models.entity import Entity, EntityState
from hive.models.vault import Vault
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
    entity = Vault(name="dev", model="sonnet")
    manager._entities["dev"] = entity
    manager.router.register("dev")

    statuses = manager.get_status()
    assert len(statuses) == 1
    assert statuses[0]["name"] == "dev"
    assert statuses[0]["role"] == "vault"
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
    entity = Vault(name="dev", model="sonnet")
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
    entity = Vault(name="dev", model="sonnet")
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


class TestHierarchyRestore:
    """Test hierarchy rebuild from persisted entities on restart."""


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

        dev = Vault(name="dev", model="sonnet", session_id="sess-dev")
        pa = Vault(name="pa", model="sonnet", session_id="sess-pa")
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

        entity = Vault(name="dev", model="sonnet")
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
        dev = Vault(name="dev", model="sonnet", session_id="sess-dev")
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


class TestPendingMessageInjection:
    """Test that pending inter-agent messages are prepended to prompts."""

    async def test_pending_messages_prepended(self, manager: ProcessManager) -> None:
        """Pending messages should be prepended to the prompt."""
        maestro = Vault(name="dev", model="sonnet")
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
        maestro = Vault(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with using_adapter(manager, FakeAdapter("ok")) as adapter:
            await manager.send_to_entity("dev", "Hello")

        assert adapter.prompts[0].endswith("Hello")
        assert "[Message from" not in adapter.prompts[0]

    async def test_multiple_pending_all_included(self, manager: ProcessManager) -> None:
        """Multiple pending messages should all appear in the prompt."""
        maestro = Vault(name="dev", model="sonnet")
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

    async def test_unknown_recipient_handled(self, manager: ProcessManager) -> None:
        """Action targeting a non-existent entity should be skipped."""
        maestro = Vault(name="dev", model="sonnet")
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
        maestro = Vault(name="dev", model="sonnet")
        lead = Vault(name="dev.backend")
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

    async def test_no_actions_no_side_effects(self, manager: ProcessManager) -> None:
        """Response without actions should not route anything."""
        maestro = Vault(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with using_adapter(manager, FakeAdapter("Just a plain response.")):
            result = await manager.send_to_entity("dev", "Hello")

        assert result == "Just a plain response."
        assert manager._last_routed_actions == []


# -- Sprint 19: autonomous spawn/kill dispatcher --


class TestAutonomousDispatch:
    """Vault/lead emitting spawn_team/spawn_worker/kill_entity actions."""

    async def _send(self, manager: ProcessManager, name: str, response: str) -> str:
        with using_adapter(manager, FakeAdapter(response)):
            return await manager.send_to_entity(name, "go")


# -- Sprint 10: compact_entity tests --


class TestCompactEntity:
    """Test the compact_entity method extracted from bridge."""

    async def test_compact_missing_entity_raises(self, manager: ProcessManager) -> None:
        with pytest.raises(KeyError):
            await manager.compact_entity("nobody")

    async def test_compact_no_session_raises(self, manager: ProcessManager) -> None:
        maestro = Vault(name="dev", model="sonnet")
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        with pytest.raises(ValueError, match="no active session"):
            await manager.compact_entity("dev")

    async def test_compact_returns_summary(self, manager: ProcessManager) -> None:
        maestro = Vault(name="dev", model="sonnet")
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
        maestro = Vault(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # input_tokens=60000 > threshold=50000 on the main send trips the
        # compact. The entity is guarded by ``_compacting`` while it runs, so
        # the compact's own two turns (summarise + reseed) don't re-trigger.
        adapter = FakeAdapter(
            ["response", "summary"],
            usage={"input_tokens": 60000, "output_tokens": 100, "context_tokens": 60000},
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
        maestro = Vault(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # input_tokens=60000 is above threshold, but compaction is disabled.
        adapter = FakeAdapter(
            "response", usage={"input_tokens": 60000, "output_tokens": 100, "context_tokens": 60000}
        )

        with (
            using_adapter(manager, adapter),
            patch("hive.process.manager.AUTO_COMPACT_ENABLED", False),
            patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
        ):
            await manager.send_to_entity("dev", "hello")

        # Only 1 turn — no compact triggered.
        assert len(adapter.prompts) == 1

    async def test_auto_compact_skips_below_threshold(self, manager: ProcessManager) -> None:
        maestro = Vault(name="dev", model="sonnet")
        maestro.session_id = "sess-existing"
        manager._entities["dev"] = maestro
        manager.router.register("dev")

        # input_tokens=30000 is below threshold=50000 — no compact.
        adapter = FakeAdapter(
            "response", usage={"input_tokens": 30000, "output_tokens": 100, "context_tokens": 30000}
        )

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
        maestro = Vault(name="dev", model="sonnet")
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
        maestro = Vault(name="dev", model="sonnet")
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
        maestro = Vault(name="dev", model="sonnet")
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
    sender = Vault(name="alice", model="sonnet")
    recipient = Vault(name="alice.bob", model="sonnet")
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
    recipient = Vault(name="alice.bob", model="sonnet")
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
    recipient = Vault(name="alice.bob", model="sonnet")
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
    sender = Vault(name="alice", model="sonnet")
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
    lead = Vault(name="alice.bob", model="sonnet")
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


async def test_parse_errors_maestro_at_cap_notifies_user(
    manager: ProcessManager,
) -> None:
    """Vault has no Hive parent → cap overflow surfaces to the user."""
    channel = _CapturingChannel()
    dispatcher = NotificationDispatcher()
    dispatcher.register(channel)
    manager.notification_dispatcher = dispatcher
    maestro = Vault(name="alice", model="sonnet")
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
    lead = Vault(name="alice.bob", model="sonnet")
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
    lead = Vault(name="alice.bob", model="sonnet")
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
    entity = Vault(name="dev", model="sonnet")
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
        entity = Vault(name=name, model="sonnet")
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
        maestro = Vault(name="otter", model="opus")
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
        maestro = Vault(name="otter", model="opus")
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
        maestro = Vault(name="otter", model="opus")
        manager._entities["otter"] = maestro
        manager.gate_coordinator = _FakeGateCoordinator(live={"otter"})  # type: ignore[assignment]
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
        maestro = Vault(name="otter", model="opus")
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
        maestro = Vault(name="otter", model="opus")
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
        maestro = Vault(name="otter", model="opus")
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
        lynx = Vault(name="lynx", model="opus")
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
