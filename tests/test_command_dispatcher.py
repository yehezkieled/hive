"""Tests for :class:`hive.commands.CommandDispatcher`.

Bridge integration tests cover most command behavior already; these tests
exist to verify the dispatcher works *without* a TelegramBridge — i.e.
that future surfaces (web endpoints, MCP tools) can use it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.commands import KNOWN_COMMANDS, CommandDispatcher, CommandResult
from hive.knowledge.blueprints import BlueprintStore
from hive.models.vault import Vault
from hive.process.manager import ProcessManager
from tests.fakes import FakeAdapter, using_adapter


@pytest_asyncio.fixture
async def manager(router: MessageRouter) -> AsyncIterator[ProcessManager]:
    mgr = ProcessManager(router=router)
    try:
        yield mgr
    finally:
        await mgr.kill_all()


@pytest_asyncio.fixture
async def dispatcher(
    manager: ProcessManager,
    token_store: TokenStore,
    task_store: TaskStore,
    audit_log: AuditLog,
    vault_store: VaultStore,
    mode_request_store: ModeRequestStore,
    blueprint_store: BlueprintStore,
    attachment_store: AttachmentStore,
    tmp_path: Path,
) -> CommandDispatcher:
    return CommandDispatcher(
        process_manager=manager,
        token_store=token_store,
        task_store=task_store,
        audit_log=audit_log,
        vault_store=vault_store,
        mode_request_store=mode_request_store,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
    )


# ---------------------------------------------------------------------------
# Module surface — KNOWN_COMMANDS frozenset
# ---------------------------------------------------------------------------


def test_known_commands_is_frozenset() -> None:
    assert isinstance(KNOWN_COMMANDS, frozenset)
    # Spot-check a few commands across categories
    assert {"status", "help", "task"} <= KNOWN_COMMANDS
    # Heartbeat is bridge-only — must NOT be in the dispatcher's surface
    assert "heartbeat" not in KNOWN_COMMANDS


def test_bridge_commands_extends_known_commands() -> None:
    """The Telegram bridge re-exports KNOWN_COMMANDS plus its surface-only commands."""
    from hive.telegram.bridge import BRIDGE_COMMANDS

    assert KNOWN_COMMANDS <= BRIDGE_COMMANDS
    assert "heartbeat" in BRIDGE_COMMANDS


def test_registry_is_single_source_of_truth(dispatcher: CommandDispatcher) -> None:
    """The name→handler registry drives routing; KNOWN_COMMANDS derives from it.

    Guards the old failure mode where a new command needed an arm in the
    if-chain AND a separate KNOWN_COMMANDS entry that could drift apart.
    `empty` is a parser artifact (not a user command), so it is excluded.
    """
    assert set(dispatcher._registry) - {"empty"} == set(KNOWN_COMMANDS)
    assert all(callable(handler) for handler in dispatcher._registry.values())


# ---------------------------------------------------------------------------
# dispatch() — text-based entry point used by the web write surface
# ---------------------------------------------------------------------------


class TestGateApprovalDispatch:
    """`/approve gate <id>` and `/deny gate <id>` ring the doorbell."""

    async def test_approve_gate_resolves_and_rings(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        mode_request_store: ModeRequestStore,
    ) -> None:
        from unittest.mock import MagicMock

        manager.mode_request_store = mode_request_store
        coordinator = MagicMock()
        manager.gate_coordinator = coordinator

        row = await mode_request_store.create(
            requester="dev",
            requested_mode="plan",
            approver="user",
            kind="gate",
        )

        result = await dispatcher.dispatch(f"/approve gate {row['id']}")
        assert f"#{row['id']}" in result.text
        coordinator.ring.assert_called_once_with("dev")

        resolved = await mode_request_store.get(row["id"])
        assert resolved["status"] == "approved"

    async def test_deny_gate_resolves_and_rings(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        mode_request_store: ModeRequestStore,
    ) -> None:
        from unittest.mock import MagicMock

        manager.mode_request_store = mode_request_store
        coordinator = MagicMock()
        manager.gate_coordinator = coordinator

        row = await mode_request_store.create(
            requester="dev",
            requested_mode="plan",
            approver="user",
            kind="gate",
        )

        result = await dispatcher.dispatch(f"/deny gate {row['id']} re-plan")
        assert f"#{row['id']}" in result.text
        coordinator.ring.assert_called_once_with("dev")

        resolved = await mode_request_store.get(row["id"])
        assert resolved["status"] == "denied"
        assert resolved["reason"] == "re-plan"

    async def test_approve_gate_unknown_id_returns_not_found(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        mode_request_store: ModeRequestStore,
    ) -> None:
        from unittest.mock import MagicMock

        manager.mode_request_store = mode_request_store
        coordinator = MagicMock()
        manager.gate_coordinator = coordinator

        result = await dispatcher.dispatch("/approve gate 99999")
        assert "not found" in result.text.lower()
        coordinator.ring.assert_not_called()

    async def test_approve_gate_with_option_records_choice(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        mode_request_store: ModeRequestStore,
    ) -> None:
        """`/approve gate <id> <option>` persists the picked option index (#23)."""
        from unittest.mock import MagicMock

        manager.mode_request_store = mode_request_store
        manager.gate_coordinator = MagicMock()

        row = await mode_request_store.create(
            requester="dev", requested_mode="ask", approver="user", kind="gate"
        )

        result = await dispatcher.dispatch(f"/approve gate {row['id']} 2")
        assert f"#{row['id']}" in result.text

        resolved = await mode_request_store.get(row["id"])
        assert resolved["status"] == "approved"
        assert resolved["chosen_option"] == 2

    async def test_approve_gate_non_integer_option_returns_usage(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        mode_request_store: ModeRequestStore,
    ) -> None:
        """A non-integer option is rejected with usage; the gate stays pending."""
        from unittest.mock import MagicMock

        manager.mode_request_store = mode_request_store
        coordinator = MagicMock()
        manager.gate_coordinator = coordinator

        row = await mode_request_store.create(
            requester="dev", requested_mode="ask", approver="user", kind="gate"
        )

        result = await dispatcher.dispatch(f"/approve gate {row['id']} two")
        assert "usage" in result.text.lower()
        coordinator.ring.assert_not_called()
        resolved = await mode_request_store.get(row["id"])
        assert resolved["status"] == "pending"


async def test_dispatch_empty_returns_empty_result(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("", actor="test")
    assert isinstance(result, CommandResult)
    assert result.text == ""


async def test_dispatch_unknown_command(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/notarealcommand", actor="test")
    assert "Unknown command" in result.text


async def test_cut_commands_return_unknown(dispatcher: CommandDispatcher) -> None:
    """Ticket 050: /swarm, /broadcast, /budget, /agent are removed — dispatching
    any of them hits the unknown-command path, not a stale handler."""
    for text in ("/swarm backend go", "/broadcast hi", "/budget", "/agent dev hi"):
        result = await dispatcher.dispatch(text, actor="test")
        assert "Unknown command" in result.text, f"{text!r} should be unknown, got: {result.text!r}"


async def test_dispatch_status_no_entities(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/status", actor="test")
    assert "No entities running" in result.text


async def test_dispatch_help_returns_help_text(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/help", actor="test")
    # /help renders the grouped command listing — should mention several commands
    assert "/status" in result.text or "status" in result.text


async def test_dispatch_kill_missing_target(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/kill", actor="test")
    assert "Usage" in result.text


async def test_dispatch_returns_command_result(dispatcher: CommandDispatcher) -> None:
    """All dispatch paths must return a CommandResult (text + metadata)."""
    result = await dispatcher.dispatch("/health", actor="test")
    assert isinstance(result, CommandResult)
    assert isinstance(result.text, str)
    assert isinstance(result.metadata, dict)


# ---------------------------------------------------------------------------
# Task and audit commands — verify store side-effects work
# ---------------------------------------------------------------------------


async def test_dispatch_task_add_creates_task(
    dispatcher: CommandDispatcher, task_store: TaskStore
) -> None:
    result = await dispatcher.dispatch('/task add "fix the thing"', actor="test:42")
    assert "added" in result.text

    pending = await task_store.list()
    assert len(pending) == 1
    assert pending[0].title == "fix the thing"
    assert pending[0].created_by == "test:42"


async def test_dispatch_task_add_records_audit(
    dispatcher: CommandDispatcher, audit_log: AuditLog
) -> None:
    await dispatcher.dispatch('/task add "audited"', actor="test:audit")
    events = await audit_log.recent(limit=5, action_prefix="task.")
    assert any(e["actor"] == "test:audit" and e["action"] == "task.create" for e in events)


# ---------------------------------------------------------------------------
# Surface-agnostic — actor parameter threads through to stores
# ---------------------------------------------------------------------------


async def test_actor_param_threads_to_task_creation(
    dispatcher: CommandDispatcher, task_store: TaskStore
) -> None:
    """Both Telegram (`user:42`) and web (`web:user`) actors should land in created_by."""
    await dispatcher.dispatch('/task add "via web"', actor="web:user")
    tasks = await task_store.list()
    assert tasks[0].created_by == "web:user"


# ---------------------------------------------------------------------------
# /files — Sprint 17 attachment listing
# ---------------------------------------------------------------------------


async def test_files_empty_returns_friendly_message(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/files", actor="test")
    assert "No attachments" in result.text


async def test_files_lists_recent_uploads(
    dispatcher: CommandDispatcher, attachment_store: AttachmentStore
) -> None:
    a_id = await attachment_store.save(
        file_path="/tmp/uploads/abc.jpg",
        original_name="cat.jpg",
        mime_type="image/jpeg",
        size_bytes=1500,
        source="telegram",
        actor="user:42",
        forwarded_to="dev",
    )
    b_id = await attachment_store.save(
        file_path="/tmp/uploads/def.pdf",
        original_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=2 * 1024 * 1024,
        source="web",
        actor="web:user",
        forwarded_to=None,
    )
    result = await dispatcher.dispatch("/files", actor="test")
    text = result.text
    assert f"#{a_id}" in text
    assert f"#{b_id}" in text
    assert "telegram" in text
    assert "web" in text
    assert "→dev" in text
    assert "image/jpeg" in text
    assert "application/pdf" in text
    # Newest first → b_id (saved last) before a_id in output
    assert text.index(f"#{b_id}") < text.index(f"#{a_id}")


async def test_files_respects_limit(
    dispatcher: CommandDispatcher, attachment_store: AttachmentStore
) -> None:
    for i in range(5):
        await attachment_store.save(
            file_path=f"/tmp/uploads/{i}.bin",
            original_name=None,
            mime_type=None,
            size_bytes=i,
            source="web",
            actor=None,
        )
    result = await dispatcher.dispatch("/files 2", actor="test")
    assert "last 2" in result.text


async def test_files_invalid_arg_returns_usage(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/files notanumber", actor="test")
    assert "Usage" in result.text


# ---------------------------------------------------------------------------
# /new maestro — default model and routed flag
# ---------------------------------------------------------------------------


def _write_min_personality(dir_path: Path, name: str, model: str = "opus") -> Path:
    """Create a minimal personality file for tests of the file-exists branch."""
    path = dir_path / f"{name}.md"
    path.write_text(
        f"# Maestro: {name}\n\n"
        "## Identity\n"
        f"- **Name**: {name}\n"
        "- **Role**: maestro\n"
        f"- **Model**: {model}\n\n"
        "## System Prompt\nMinimal personality for tests.\n"
    )
    return path


# ---------------------------------------------------------------------------
# /new maestro — Phase 2 interactive flow (no personality file present)
# ---------------------------------------------------------------------------


class TestNewMaestroInteractiveFlow:
    """`/new maestro <name>` with no personality file enters a multi-turn Q&A.

    The dispatcher tracks pending state per actor; subsequent plain-text
    messages from the same actor are interpreted as answers, not as
    messages to the default maestro. Final answer writes a templated
    personality file and registers the maestro.
    """

    async def test_cancel_command_aborts_flow(
        self, dispatcher: CommandDispatcher, manager: ProcessManager
    ) -> None:
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        result = await dispatcher.dispatch("/cancel", actor="user:42")
        assert "cancel" in result.text.lower()
        # No file written, no registration
        assert "otter" not in manager.entities

    async def test_other_command_cancels_pending_flow(self, dispatcher: CommandDispatcher) -> None:
        """A different /command (not /cancel) interrupts the flow."""
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        # Send a different command — should cancel flow and execute the new command
        result = await dispatcher.dispatch("/status", actor="user:42")
        # Status response, not the next flow question
        assert "for?" not in result.text.lower()
        assert "purpose" not in result.text.lower()
        # Subsequent plain text should NOT advance a (now-cancelled) flow
        followup = await dispatcher.dispatch("manage projects", actor="user:42")
        assert "style" not in followup.text.lower()


# ---------------------------------------------------------------------------
# Ticket 029: awaiting_decision clears on a USER reply, not a peer message
# ---------------------------------------------------------------------------


async def test_peer_triggered_send_does_not_clear_awaiting_decision(
    manager: ProcessManager,
) -> None:
    """The shared orchestration path (scheduler poke / peer wake) must NOT
    clear the flag — only a user-sourced reply does."""
    await manager.register_entity(Vault(name="dev"))
    manager._entities["dev"].awaiting_decision = True

    adapter = FakeAdapter(responses="working")
    with using_adapter(manager, adapter):
        await manager.send_to_entity("dev", "[peer poke]")

    assert manager._entities["dev"].awaiting_decision is True


# ---------------------------------------------------------------------------
# T007 — Command surface v2
# ---------------------------------------------------------------------------


class TestModelCommandV2:
    """/model gains `fable` and a billing warning driven by one API-billed set."""

    async def test_model_fable_is_accepted(self, dispatcher: CommandDispatcher) -> None:
        await dispatcher.process_manager.register_entity(Vault(name="dev"))
        result = await dispatcher.dispatch("/model fable dev")
        assert "set to 'fable'" in result.text
        assert dispatcher.process_manager._entities["dev"].model == "fable"

    async def test_plan_billed_model_has_no_billing_warning(
        self, dispatcher: CommandDispatcher
    ) -> None:
        await dispatcher.process_manager.register_entity(Vault(name="dev"))
        result = await dispatcher.dispatch("/model fable dev")
        # fable is plan-billed under the Max plan today — no warning.
        assert "API-billed" not in result.text

    async def test_api_billed_model_emits_billing_warning(
        self, dispatcher: CommandDispatcher, monkeypatch
    ) -> None:
        """The warning path is covered even though the set ships empty:
        inject a member into the one-place set and confirm the warning fires."""
        import hive.models.entity as entity_mod

        monkeypatch.setattr(entity_mod, "API_BILLED_MODELS", frozenset({"sonnet"}))
        await dispatcher.process_manager.register_entity(Vault(name="dev"))
        result = await dispatcher.dispatch("/model sonnet dev")
        assert "API-billed" in result.text
        assert "real money" in result.text.lower()


class TestModeCommandV2:
    """/mode offers only yolo / yotree."""

    async def test_mode_rejects_edit_auto_plan(self, dispatcher: CommandDispatcher) -> None:
        await dispatcher.process_manager.register_entity(Vault(name="dev"))
        for dropped in ("edit", "auto", "plan"):
            result = await dispatcher.dispatch(f"/mode {dropped} dev")
            assert "yolo" in result.text and "yotree" in result.text
            assert "set to" not in result.text  # rejected, not applied

    async def test_mode_accepts_yolo_and_yotree(self, dispatcher: CommandDispatcher) -> None:
        await dispatcher.process_manager.register_entity(Vault(name="dev"))
        result = await dispatcher.dispatch("/mode yolo dev")
        assert "set to 'yolo'" in result.text


class TestShipCommand:
    """/ship folds /commit /pr /merge."""


class TestGoalSeedingAtSpawn:
    """Hive seeds native /goal on an entity's first turn; /loop is gone."""

    def test_loop_removed_from_surface(self) -> None:
        assert "loop" not in KNOWN_COMMANDS

    async def test_first_task_turn_is_seeded_with_goal(self, manager: ProcessManager) -> None:
        await manager.register_entity(Vault(name="dev"))
        adapter = FakeAdapter(responses="ok")
        with using_adapter(manager, adapter):
            # A genuine task delivery marks seed_goal.
            await manager.send_to_entity("dev", "build the widget", seed_goal=True)
        assert adapter.prompts[-1].startswith("/goal ")
        assert "build the widget" in adapter.prompts[-1]

    async def test_second_turn_is_not_seeded(self, manager: ProcessManager) -> None:
        await manager.register_entity(Vault(name="dev"))
        first = FakeAdapter(responses="ok")
        with using_adapter(manager, first):
            await manager.send_to_entity("dev", "build the widget", seed_goal=True)
        # session_id is now set; the next turn must not be re-seeded.
        second = FakeAdapter(responses="ok")
        with using_adapter(manager, second):
            await manager.send_to_entity("dev", "keep going", seed_goal=True)
        assert not second.prompts[-1].startswith("/goal ")

    async def test_internal_first_turn_send_is_not_seeded(self, manager: ProcessManager) -> None:
        """A poke / peer mail / compact reseed that lands as the first send must
        NOT be wrapped in /goal — only a genuine task (seed_goal) is."""
        await manager.register_entity(Vault(name="dev"))
        adapter = FakeAdapter(responses="working")
        with using_adapter(manager, adapter):
            # Default seed_goal=False: the shared-chokepoint machine path.
            await manager.send_to_entity("dev", "[peer poke]")
        assert not adapter.prompts[-1].startswith("/goal ")
        assert "[peer poke]" in adapter.prompts[-1]

    async def test_user_command_path_seeds_goal(
        self, manager: ProcessManager, dispatcher: CommandDispatcher
    ) -> None:
        """The user/command entrypoint (dispatch._send_to_entity) marks the
        first task for /goal seeding end to end."""
        await manager.register_entity(Vault(name="dev"))
        adapter = FakeAdapter(responses="ok")
        with using_adapter(manager, adapter):
            await dispatcher._send_to_entity("dev", "build the widget")
        assert adapter.prompts[-1].startswith("/goal ")
        assert "build the widget" in adapter.prompts[-1]
