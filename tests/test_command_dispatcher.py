"""Tests for :class:`hive.commands.CommandDispatcher`.

Bridge integration tests cover most command behavior already; these tests
exist to verify the dispatcher works *without* a TelegramBridge — i.e.
that future surfaces (web endpoints, MCP tools) can use it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
from hive.process.manager import ProcessManager


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
        default_maestro="dev",
        token_store=token_store,
        task_store=task_store,
        audit_log=audit_log,
        vault_store=vault_store,
        mode_request_store=mode_request_store,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
        personalities_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Module surface — KNOWN_COMMANDS frozenset
# ---------------------------------------------------------------------------


def test_known_commands_is_frozenset() -> None:
    assert isinstance(KNOWN_COMMANDS, frozenset)
    # Spot-check a few commands across categories
    assert {"status", "help", "task", "commit", "broadcast"} <= KNOWN_COMMANDS
    # Heartbeat is bridge-only — must NOT be in the dispatcher's surface
    assert "heartbeat" not in KNOWN_COMMANDS


def test_bridge_commands_extends_known_commands() -> None:
    """The Telegram bridge re-exports KNOWN_COMMANDS plus its surface-only commands."""
    from hive.telegram.bridge import BRIDGE_COMMANDS

    assert KNOWN_COMMANDS <= BRIDGE_COMMANDS
    assert "heartbeat" in BRIDGE_COMMANDS


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


async def test_dispatch_empty_returns_empty_result(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("", actor="test")
    assert isinstance(result, CommandResult)
    assert result.text == ""


async def test_dispatch_unknown_command(dispatcher: CommandDispatcher) -> None:
    result = await dispatcher.dispatch("/notarealcommand", actor="test")
    assert "Unknown command" in result.text


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


async def test_new_maestro_defaults_to_opus(
    dispatcher: CommandDispatcher, manager: ProcessManager, tmp_path: Path
) -> None:
    """`/new maestro <name>` with personality file present registers the maestro at model=opus."""
    _write_min_personality(tmp_path, "testbot")
    result = await dispatcher.dispatch("/new maestro testbot", actor="test")
    assert "registered" in result.text.lower()
    assert "model=opus" in result.text
    assert manager.entities["testbot"].model == "opus"


async def test_new_maestro_file_model_overrides_cli_arg(
    dispatcher: CommandDispatcher, manager: ProcessManager, tmp_path: Path
) -> None:
    """When a personality file is present, its `**Model**` field is authoritative.

    The CLI model arg is only honored when no file exists yet (the
    interactive flow uses it for the freshly-written file). Documents
    the behavior change introduced with the Phase 2 flow.
    """
    _write_min_personality(tmp_path, "otherbot", model="haiku")
    result = await dispatcher.dispatch("/new maestro otherbot opus", actor="test")
    # File says haiku; arg said opus — file wins.
    assert "model=haiku" in result.text
    assert manager.entities["otherbot"].model == "haiku"


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

    async def test_no_personality_starts_flow_with_first_question(
        self, dispatcher: CommandDispatcher, manager: ProcessManager
    ) -> None:
        result = await dispatcher.dispatch("/new maestro otter", actor="user:42")
        # Question text — purpose / "for?" — case-insensitive match
        text = result.text.lower()
        assert "for?" in text or "purpose" in text
        # Maestro is NOT yet registered — flow must collect answers first
        assert "otter" not in manager.entities

    async def test_plain_text_advances_flow(
        self, dispatcher: CommandDispatcher, manager: ProcessManager
    ) -> None:
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        result = await dispatcher.dispatch("manage my projects", actor="user:42")
        # Second question asks about communication style
        text = result.text.lower()
        assert "style" in text or "communicate" in text or "tone" in text
        assert "otter" not in manager.entities

    async def test_full_flow_writes_file_and_registers(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        tmp_path: Path,
    ) -> None:
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        await dispatcher.dispatch("manage my projects", actor="user:42")
        result = await dispatcher.dispatch("terse and direct", actor="user:42")
        assert "registered" in result.text.lower()
        # File written with the user's answers embedded
        target = tmp_path / "otter.md"
        assert target.exists()
        content = target.read_text()
        assert "**Name**: otter" in content
        assert "manage my projects" in content
        assert "terse and direct" in content
        # Maestro registered in process manager
        assert "otter" in manager.entities

    async def test_full_flow_honors_explicit_model_arg(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        tmp_path: Path,
    ) -> None:
        """Model passed via `/new maestro <name> <model>` carries through the flow."""
        await dispatcher.dispatch("/new maestro otter haiku", actor="user:42")
        await dispatcher.dispatch("a purpose", actor="user:42")
        result = await dispatcher.dispatch("casual", actor="user:42")
        assert "model=haiku" in result.text
        assert manager.entities["otter"].model == "haiku"
        assert "**Model**: haiku" in (tmp_path / "otter.md").read_text()

    async def test_cancel_command_aborts_flow(
        self, dispatcher: CommandDispatcher, manager: ProcessManager
    ) -> None:
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        result = await dispatcher.dispatch("/cancel", actor="user:42")
        assert "cancel" in result.text.lower()
        # No file written, no registration
        assert "otter" not in manager.entities

    async def test_cancel_command_outside_flow_returns_friendly_message(
        self, dispatcher: CommandDispatcher
    ) -> None:
        """`/cancel` with no pending flow should not return 'Unknown command'."""
        result = await dispatcher.dispatch("/cancel", actor="user:42")
        text = result.text.lower()
        assert "unknown command" not in text
        assert "nothing to cancel" in text

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

    async def test_pending_state_isolated_per_actor(self, dispatcher: CommandDispatcher) -> None:
        """Two actors can run /new maestro concurrently without crosstalk."""
        await dispatcher.dispatch("/new maestro alpha", actor="user:1")
        await dispatcher.dispatch("/new maestro beta", actor="user:2")
        result_a = await dispatcher.dispatch("alpha purpose", actor="user:1")
        result_b = await dispatcher.dispatch("beta purpose", actor="user:2")
        # Each actor sees their own next-question advance
        assert "style" in result_a.text.lower() or "communicate" in result_a.text.lower()
        assert "style" in result_b.text.lower() or "communicate" in result_b.text.lower()

    async def test_pending_state_times_out(self, dispatcher: CommandDispatcher) -> None:
        """Pending flow expires after 10 minutes of inactivity."""
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        # Backdate the actor's pending state
        pending = dispatcher._pending_new["user:42"]  # type: ignore[attr-defined]
        pending.last_active = datetime.now(UTC) - timedelta(minutes=11)
        # Plain text should NOT be interpreted as the answer anymore
        result = await dispatcher.dispatch("an answer", actor="user:42")
        assert "style" not in result.text.lower()
        assert "communicate" not in result.text.lower()

    async def test_flow_does_not_register_if_name_taken(
        self,
        dispatcher: CommandDispatcher,
        manager: ProcessManager,
        tmp_path: Path,
    ) -> None:
        """If a maestro is registered mid-flow, finalization reports the error gracefully."""
        # Register otter via another path before the flow finishes
        await manager.register_maestro("otter", model="opus")
        await dispatcher.dispatch("/new maestro otter", actor="user:42")
        await dispatcher.dispatch("a purpose", actor="user:42")
        result = await dispatcher.dispatch("a style", actor="user:42")
        # Final step surfaces the duplicate-name error from register_maestro
        assert "error" in result.text.lower() or "already" in result.text.lower()
