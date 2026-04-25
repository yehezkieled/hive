"""Tests for :class:`hive.commands.CommandDispatcher`.

Bridge integration tests cover most command behavior already; these tests
exist to verify the dispatcher works *without* a TelegramBridge — i.e.
that future surfaces (web endpoints, MCP tools) can use it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

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
