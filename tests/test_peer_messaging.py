"""Permission matrix and routing tests for tier-aware peer messaging."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from hive.bus.actions import Action
from hive.bus.audit_log import AuditLog
from hive.bus.permissions import can_message, can_request_decision, cc_targets_for
from hive.bus.router import MessageRouter
from hive.process.manager import ProcessManager
from tests.fakes import FakeAdapter, using_adapter

# ---- can_message peer rules ----


class TestPeerMessagingPermissions:
    def test_worker_to_worker_same_team_allowed(self) -> None:
        # dev.backend.w1 -> dev.backend.w2 (same lead dev.backend)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w2") is True

    def test_worker_to_worker_cross_team_same_maestro_allowed(self) -> None:
        # dev.backend.w1 -> dev.payments.w1 (different leads, same maestro dev)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.payments.w1") is True

    def test_worker_to_worker_cross_maestro_denied(self) -> None:
        # dev.backend.w1 -> ops.deploy.w1 (different maestros)
        assert can_message("worker", "dev.backend.w1", "worker", "ops.deploy.w1") is False

    def test_lead_to_lead_same_maestro_allowed(self) -> None:
        # dev.backend -> dev.payments (same maestro dev)
        assert can_message("lead", "dev.backend", "lead", "dev.payments") is True

    def test_lead_to_lead_cross_maestro_allowed(self) -> None:
        # dev.backend -> ops.deploy (different maestros — allowed but with CC)
        assert can_message("lead", "dev.backend", "lead", "ops.deploy") is True

    def test_maestro_to_maestro_allowed(self) -> None:
        # dev -> ops (top-tier peers)
        assert can_message("maestro", "dev", "maestro", "ops") is True

    def test_existing_parent_child_still_allowed(self) -> None:
        # Regression: existing rules must keep working.
        assert can_message("worker", "dev.backend.w1", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "worker", "dev.backend.w1") is True
        assert can_message("maestro", "dev", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_worker_to_worker_self_denied(self) -> None:
        # Self-message is disallowed.
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w1") is False


# ---- cc_targets_for resolver ----


class TestCcTargetsFor:
    def test_no_cc_for_same_team_workers(self) -> None:
        assert cc_targets_for("worker", "dev.backend.w1", "worker", "dev.backend.w2") == []

    def test_cross_team_workers_cc_both_leads(self) -> None:
        result = cc_targets_for("worker", "dev.backend.w1", "worker", "dev.payments.w1")
        assert sorted(result) == ["dev.backend", "dev.payments"]

    def test_no_cc_for_same_maestro_leads(self) -> None:
        assert cc_targets_for("lead", "dev.backend", "lead", "dev.payments") == []

    def test_cross_maestro_leads_cc_both_maestros(self) -> None:
        result = cc_targets_for("lead", "dev.backend", "lead", "ops.deploy")
        assert sorted(result) == ["dev", "ops"]

    def test_no_cc_for_maestro_peers(self) -> None:
        assert cc_targets_for("maestro", "dev", "maestro", "ops") == []

    def test_no_cc_for_parent_child_routes(self) -> None:
        # Existing parent-child routes carry no CC.
        assert cc_targets_for("worker", "dev.backend.w1", "lead", "dev.backend") == []
        assert cc_targets_for("lead", "dev.backend", "maestro", "dev") == []


# ---- can_request_decision gate ----


class TestCanRequestDecision:
    def test_worker_to_own_lead_allowed(self) -> None:
        assert can_request_decision("worker", "dev.backend.w1", "dev.backend") is True

    def test_lead_to_own_maestro_allowed(self) -> None:
        assert can_request_decision("lead", "dev.backend", "dev") is True

    def test_worker_to_other_lead_denied(self) -> None:
        assert can_request_decision("worker", "dev.backend.w1", "dev.payments") is False

    def test_worker_skipping_lead_to_maestro_denied(self) -> None:
        assert can_request_decision("worker", "dev.backend.w1", "dev") is False

    def test_lead_to_other_maestro_denied(self) -> None:
        assert can_request_decision("lead", "dev.backend", "ops") is False

    def test_maestro_cannot_request_decision(self) -> None:
        # Maestros are top-tier — no parent to escalate to.
        assert can_request_decision("maestro", "dev", "user") is False
        assert can_request_decision("maestro", "dev", "ops") is False


# ---- Routing integration tests (peer message + CC) ----


@pytest_asyncio.fixture
async def manager(
    router: MessageRouter,
    audit_log: AuditLog,
) -> AsyncIterator[ProcessManager]:
    """Process manager backed by the conftest router + audit_log."""
    mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=2)
    try:
        yield mgr
    finally:
        await mgr.kill_all()


class TestPeerMessageRouting:
    async def test_same_team_worker_message_no_cc(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")
        await manager.spawn_worker("dev.backend", worker_name="w2")

        action = Action(type="message", to="dev.backend.w2", text="hello peer")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert manager.router.has_pending("dev.backend.w2")
        assert not manager.router.has_pending("dev.backend")

    async def test_cross_team_worker_message_ccs_both_leads(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.create_team("dev", "payments")
        await manager.spawn_worker("dev.backend", worker_name="w1")
        await manager.spawn_worker("dev.payments", worker_name="w1")

        action = Action(type="message", to="dev.payments.w1", text="cross-team")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert manager.router.has_pending("dev.payments.w1")
        assert manager.router.has_pending("dev.backend")
        assert manager.router.has_pending("dev.payments")

    async def test_cross_maestro_worker_blocked(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.register_maestro("ops")
        await manager.create_team("dev", "backend")
        await manager.create_team("ops", "deploy")
        await manager.spawn_worker("dev.backend", worker_name="w1")
        await manager.spawn_worker("ops.deploy", worker_name="w1")

        action = Action(type="message", to="ops.deploy.w1", text="leak")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert not manager.router.has_pending("ops.deploy.w1")


class TestAliasAndRejectionFeedback:
    """Ticket 023 (design D2): addressing aliases + rejection feedback,
    end-to-end through the real router so the note sits in the queue that
    wake-on-inbound drains.
    """

    async def test_lead_maestro_alias_delivers_to_org_root(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")

        action = Action(type="message", to="maestro", text="breakdown proposal")
        await manager._handle_actions("dev.backend", "", [action])

        assert manager.router.has_pending("dev")
        msg = await manager.router.get_next("dev", timeout=0.1)
        assert msg is not None
        assert msg.sender == "dev.backend"
        assert msg.content == "breakdown proposal"

    async def test_worker_parent_alias_delivers_to_lead(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")

        action = Action(type="message", to="parent", text="done")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert manager.router.has_pending("dev.backend")

    async def test_unknown_recipient_queues_feedback_note_for_sender(
        self, manager: ProcessManager
    ) -> None:
        """The rejection note lands in the sender's own queue — the same
        queue wake-on-inbound watches — so the sender self-corrects next
        turn instead of waiting forever (failure F2).
        """
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")

        action = Action(type="message", to="maestro.strutils", text="breakdown")
        await manager._handle_actions("dev.backend", "", [action])

        assert not manager.router.has_pending("dev")
        assert manager.router.has_pending("dev.backend")
        note = await manager.router.get_next("dev.backend", timeout=0.1)
        assert note is not None
        assert note.sender == "system"
        assert "[action rejected]" in note.content
        assert "maestro" in note.content  # the correct form

    async def test_permission_denied_queues_feedback_note_for_sender(
        self, manager: ProcessManager
    ) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")

        action = Action(type="message", to="maestro", text="skip the chain")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert not manager.router.has_pending("dev")
        assert manager.router.has_pending("dev.backend.w1")
        note = await manager.router.get_next("dev.backend.w1", timeout=0.1)
        assert note is not None
        assert note.sender == "system"
        assert "dev.backend" in note.content  # the correct form: its parent


class TestRequestDecision:
    async def test_worker_to_own_lead_allowed(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")

        action = Action(
            type="request_decision",
            to="dev.backend",
            text="JWT or sessions?",
        )
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert manager.router.has_pending("dev.backend")

    async def test_worker_skipping_to_maestro_blocked(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")

        action = Action(type="request_decision", to="dev", text="bypass attempt")
        await manager._handle_actions("dev.backend.w1", "", [action])

        assert not manager.router.has_pending("dev")

    async def test_lead_to_own_maestro_allowed(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")

        action = Action(type="request_decision", to="dev", text="add new team?")
        await manager._handle_actions("dev.backend", "", [action])

        assert manager.router.has_pending("dev")


class TestPeerDirectory:
    async def test_worker_directory_lists_peers_and_parent(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.create_team("dev", "payments")
        await manager.spawn_worker("dev.backend", worker_name="w1")
        await manager.spawn_worker("dev.backend", worker_name="w2")
        await manager.spawn_worker("dev.payments", worker_name="w1")

        directory = manager._peer_directory_for("dev.backend.w1")

        assert "dev.backend.w2" in directory
        assert "same team" in directory
        assert "dev.payments.w1" in directory
        assert "cross-team" in directory
        assert "dev.backend" in directory  # parent for request_decision

    async def test_lead_directory_lists_other_leads(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.create_team("dev", "payments")

        directory = manager._peer_directory_for("dev.backend")
        assert "dev.payments" in directory
        assert "same maestro" in directory

    async def test_maestro_directory_lists_other_maestros(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.register_maestro("ops")

        directory = manager._peer_directory_for("dev")
        assert "ops" in directory

    async def test_directory_empty_when_alone(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")
        await manager.spawn_worker("dev.backend", worker_name="w1")

        directory = manager._peer_directory_for("dev.backend.w1")
        # No other workers — should still mention parent, not bomb out.
        assert "dev.backend" in directory


async def test_send_to_entity_prepends_peer_directory(
    manager: ProcessManager,
) -> None:
    await manager.register_maestro("dev")
    await manager.create_team("dev", "backend")
    await manager.spawn_worker("dev.backend", worker_name="w1")
    await manager.spawn_worker("dev.backend", worker_name="w2")

    with using_adapter(manager, FakeAdapter("")) as adapter:
        await manager.send_to_entity("dev.backend.w1", "do work")

    captured_prompt = adapter.prompts[-1]
    assert "dev.backend.w2" in captured_prompt
    assert "Peers you can message" in captured_prompt
