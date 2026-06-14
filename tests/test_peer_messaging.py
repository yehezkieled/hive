"""Permission matrix and routing tests for tier-aware peer messaging."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from hive.bus.actions import Action
from hive.bus.audit_log import AuditLog
from hive.bus.permissions import can_message, can_request_decision, cc_targets_for
from hive.bus.router import MessageRouter
from hive.notifications.dispatcher import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager
from tests.fakes import FakeAdapter, using_adapter


class _CapturingChannel:
    """Notification channel that records every notification's text."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, notification: Notification) -> None:
        self.messages.append(notification.text)


# ---- can_message peer rules ----


class TestPeerMessagingPermissions:
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
        assert can_message("maestro", "dev", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_lead_to_lead_self_denied(self) -> None:
        # Self-message is disallowed.
        assert can_message("lead", "dev.backend", "lead", "dev.backend") is False


# ---- cc_targets_for resolver ----


class TestCcTargetsFor:
    def test_no_cc_for_same_maestro_leads(self) -> None:
        assert cc_targets_for("lead", "dev.backend", "lead", "dev.payments") == []

    def test_cross_maestro_leads_cc_both_maestros(self) -> None:
        result = cc_targets_for("lead", "dev.backend", "lead", "ops.deploy")
        assert sorted(result) == ["dev", "ops"]

    def test_no_cc_for_maestro_peers(self) -> None:
        assert cc_targets_for("maestro", "dev", "maestro", "ops") == []

    def test_no_cc_for_parent_child_routes(self) -> None:
        # Existing parent-child routes carry no CC.
        assert cc_targets_for("lead", "dev.backend", "maestro", "dev") == []


# ---- can_request_decision gate ----


class TestCanRequestDecision:
    def test_lead_to_own_maestro_allowed(self) -> None:
        assert can_request_decision("lead", "dev.backend", "dev") is True

    def test_lead_to_other_maestro_denied(self) -> None:
        assert can_request_decision("lead", "dev.backend", "ops") is False

    def test_maestro_to_user_allowed(self) -> None:
        # Ticket 029: a maestro escalates a decision to the user (the top rung),
        # the conversational decision channel. This is the only request_decision
        # target a maestro may use.
        assert can_request_decision("maestro", "dev", "user") is True

    def test_maestro_to_other_entity_denied(self) -> None:
        # A maestro may not request_decision from a peer entity — only the user.
        assert can_request_decision("maestro", "dev", "ops") is False

    def test_lead_to_user_denied(self) -> None:
        # A lead escalates to its parent maestro, never directly to the user.
        assert can_request_decision("lead", "dev.backend", "user") is False


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


class TestRequestDecision:
    async def test_lead_to_own_maestro_allowed(self, manager: ProcessManager) -> None:
        await manager.register_maestro("dev")
        await manager.create_team("dev", "backend")

        action = Action(type="request_decision", to="dev", text="add new team?")
        await manager._handle_actions("dev.backend", "", [action])

        assert manager.router.has_pending("dev")

    # ---- Ticket 029: maestro→user conversational decision channel ----

    async def test_maestro_to_user_notifies_and_parks(self, manager: ProcessManager) -> None:
        """request_decision{to:user} delivers to the user (Telegram) and parks
        the maestro on the durable awaiting_decision flag."""
        await manager.register_maestro("dev")
        channel = _CapturingChannel()
        dispatcher = NotificationDispatcher()
        dispatcher.register(channel)
        manager.notification_dispatcher = dispatcher

        action = Action(type="request_decision", to="user", text="approve the plan?")
        await manager._handle_actions("dev", "", [action])

        assert any("approve the plan?" in m for m in channel.messages)
        assert manager._entities["dev"].awaiting_decision is True
        assert "user" in manager._last_routed_actions

    async def test_request_decision_to_user_truncates_trailing_actions(
        self, manager: ProcessManager
    ) -> None:
        """After asking the user, the maestro ends its turn — trailing actions
        in the same block do not run (no ask-then-act)."""
        await manager.register_maestro("dev")
        channel = _CapturingChannel()
        dispatcher = NotificationDispatcher()
        dispatcher.register(channel)
        manager.notification_dispatcher = dispatcher

        actions = [
            Action(type="request_decision", to="user", text="FIRST question"),
            Action(type="request_decision", to="user", text="SECOND question"),
        ]
        await manager._handle_actions("dev", "", actions)

        assert any("FIRST" in m for m in channel.messages)
        assert not any("SECOND" in m for m in channel.messages)

    async def test_request_decision_to_user_unreachable_rejects(
        self, manager: ProcessManager
    ) -> None:
        """With no notification path configured, the user can't be reached:
        the maestro is NOT parked and gets a failure note (no fictional
        delivery)."""
        await manager.register_maestro("dev")
        # manager fixture leaves notification_dispatcher = None

        action = Action(type="request_decision", to="user", text="approve?")
        await manager._handle_actions("dev", "", [action])

        assert manager._entities["dev"].awaiting_decision is False
        assert manager.router.has_pending("dev")
        note = await manager.router.get_next("dev", timeout=0.1)
        assert note is not None
        assert note.sender == "system"
        assert "[action rejected]" in note.content


class TestPeerDirectory:
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


async def test_send_to_entity_prepends_peer_directory(
    manager: ProcessManager,
) -> None:
    await manager.register_maestro("dev")
    await manager.create_team("dev", "backend")
    await manager.create_team("dev", "payments")

    with using_adapter(manager, FakeAdapter("")) as adapter:
        await manager.send_to_entity("dev.backend", "do work")

    captured_prompt = adapter.prompts[-1]
    assert "dev.payments" in captured_prompt
    assert "Peers you can message" in captured_prompt
