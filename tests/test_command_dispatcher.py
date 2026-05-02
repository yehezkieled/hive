"""Tests for :class:`hive.commands.CommandDispatcher`.

Bridge integration tests cover most command behavior already; these tests
exist to verify the dispatcher works *without* a TelegramBridge — i.e.
that future surfaces (web endpoints, MCP tools) can use it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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


async def test_new_maestro_defaults_to_opus(
    dispatcher: CommandDispatcher, manager: ProcessManager
) -> None:
    """`/new maestro <name>` (no model arg) must register the maestro with model=opus."""
    result = await dispatcher.dispatch("/new maestro testbot", actor="test")
    assert "registered" in result.text.lower()
    assert "model=opus" in result.text
    assert manager.entities["testbot"].model == "opus"


async def test_new_maestro_explicit_model_honored(
    dispatcher: CommandDispatcher, manager: ProcessManager
) -> None:
    """Caller can still override the model — defaults only kick in when omitted."""
    result = await dispatcher.dispatch("/new maestro otherbot haiku", actor="test")
    assert "model=haiku" in result.text
    assert manager.entities["otherbot"].model == "haiku"
