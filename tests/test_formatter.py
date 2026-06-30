"""Isolated unit tests for the read-only command Formatter (Ticket 045).

The Formatter renders the read-only views (status / org / teams / quota / help /
maestros / comms / health / cost / audit / tasks / files). These tests construct
it with a *mock* ProcessManager and only the read-only stores — proving it works
with **no** approval/mutation store (vault / mode_request / blueprint), which is
the whole point of the split (a web read endpoint can build only this).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from hive.commands.formatter import Formatter


def _cmd(target: str | None = None, args: str = "") -> MagicMock:
    c = MagicMock()
    c.target = target
    c.args = args
    return c


@pytest.fixture
def pm() -> MagicMock:
    m = MagicMock()
    m.get_status.return_value = []
    m.entities = {}
    m.health_check = AsyncMock(return_value=[])
    m.router.store.get_recent = AsyncMock(return_value=[])
    m.quota_monitor = None
    return m


async def test_formatter_constructs_with_process_manager_only(pm: MagicMock) -> None:
    """Read views render with NO stores — no vault/approval machinery needed."""
    f = Formatter(pm)
    assert (await f.status(_cmd(), "user")).text == "No entities running."
    assert (await f.org(_cmd(), "user")).text == "No entities running."
    assert (await f.teams(_cmd(), "user")).text == "No maestros registered."
    assert (await f.health(_cmd(), "user")).text == "All entities healthy."
    assert (await f.maestros(_cmd(), "user")).text == "No maestros running."
    assert (await f.comms(_cmd(), "user")).text == "No messages yet."
    assert "quota" in (await f.quota(_cmd(), "user")).text.lower()
    assert (await f.help(_cmd(), "user")).text  # static help text, non-empty


async def test_store_backed_views_report_unconfigured_without_stores(pm: MagicMock) -> None:
    f = Formatter(pm)
    assert (await f.cost(_cmd(), "user")).text == "Token tracking not configured."
    assert (await f.audit(_cmd(), "user")).text == "Audit log not configured."
    assert (await f.tasks(_cmd(), "user")).text == "Task tracking not configured."
    assert (await f.files(_cmd(), "user")).text == "Attachments not configured."


async def test_store_backed_views_render_with_readonly_stores(pm: MagicMock) -> None:
    token_store = MagicMock()
    token_store.totals = AsyncMock(return_value={"call_count": 0})
    audit_log = MagicMock()
    audit_log.recent = AsyncMock(return_value=[])
    task_store = MagicMock()
    task_store.list = AsyncMock(return_value=[])
    attachment_store = MagicMock()
    attachment_store.list_recent = AsyncMock(return_value=[])

    f = Formatter(
        pm,
        token_store=token_store,
        audit_log=audit_log,
        task_store=task_store,
        attachment_store=attachment_store,
    )
    assert "No token usage" in (await f.cost(_cmd(), "user")).text
    assert "No audit events" in (await f.audit(_cmd(), "user")).text
    assert (await f.tasks(_cmd(), "user")).text == "No open tasks."
    assert (await f.files(_cmd(), "user")).text == "No attachments yet."


def test_formatter_takes_no_mutation_or_approval_stores() -> None:
    """The constructor must NOT accept vault/mode_request/blueprint stores."""
    params = set(inspect.signature(Formatter.__init__).parameters)
    assert "vault_store" not in params
    assert "mode_request_store" not in params
    assert "blueprint_store" not in params
    assert {"token_store", "audit_log", "task_store", "attachment_store"} <= params
