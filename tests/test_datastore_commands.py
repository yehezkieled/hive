"""Isolated unit tests for the DataStore command group (Ticket 045).

Vault (hard-money, ADR 0017) + blueprint commands, exercised with a mock
ProcessManager and mock stores — no DB, no facade.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

from hive.commands.datastore_commands import DataStoreCommands


def _cmd(target: str | None = None, args: str = "") -> MagicMock:
    c = MagicMock()
    c.target = target
    c.args = args
    return c


async def test_vault_unconfigured_without_store() -> None:
    ds = DataStoreCommands(MagicMock())
    assert (await ds.vault(_cmd(target="status"), "user")).text == "Vault store not configured."


async def test_blueprint_unconfigured_without_store() -> None:
    ds = DataStoreCommands(MagicMock())
    assert (await ds.blueprint(_cmd(target="list"), "user")).text == "Blueprints not configured."


async def test_vault_status_empty() -> None:
    vault = MagicMock()
    vault.pending = AsyncMock(return_value=[])
    ds = DataStoreCommands(MagicMock(), vault_store=vault)
    assert (await ds.vault(_cmd(target="status"), "user")).text == "No pending vault actions."


async def test_vault_approve_delegates_to_process_manager() -> None:
    pm = MagicMock()
    pm.approve_vault_action = AsyncMock(return_value={"status": "approved"})
    ds = DataStoreCommands(pm, vault_store=MagicMock())
    result = await ds.vault(_cmd(target="approve", args="5"), "user")
    assert result.text == "Action #5 approved."
    pm.approve_vault_action.assert_awaited_once_with(5)


async def test_blueprint_list_empty() -> None:
    bp = MagicMock()
    bp.list_all = AsyncMock(return_value=[])
    ds = DataStoreCommands(MagicMock(), blueprint_store=bp)
    assert (await ds.blueprint(_cmd(target="list"), "user")).text == "No blueprints saved."


async def test_blueprint_save_round_trips_title() -> None:
    bp = MagicMock()
    bp.save = AsyncMock(return_value=7)
    ds = DataStoreCommands(MagicMock(), blueprint_store=bp)
    result = await ds.blueprint(_cmd(target="save", args='"deploy runbook"'), "user")
    assert result.text == "Blueprint #7 saved: deploy runbook"


def test_datastore_takes_vault_and_blueprint_stores() -> None:
    params = set(inspect.signature(DataStoreCommands.__init__).parameters)
    assert {"vault_store", "blueprint_store"} <= params
