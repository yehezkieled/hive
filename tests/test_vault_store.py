"""Tests for VaultStore — pending action approval flow."""

from hive.bus.vault_store import VaultStore


async def test_create_pending_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(
        vault_name="vault", description="Pay invoice #123", requester="dev"
    )
    assert action["id"] is not None
    assert action["status"] == "pending"
    assert action["vault_name"] == "vault"


async def test_list_pending_actions(vault_store: VaultStore) -> None:
    await vault_store.create_action(
        vault_name="vault", description="Action 1", requester="dev"
    )
    await vault_store.create_action(
        vault_name="vault", description="Action 2", requester="dev"
    )

    pending = await vault_store.pending("vault")
    assert len(pending) == 2


async def test_approve_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(
        vault_name="vault", description="Pay", requester="dev"
    )
    result = await vault_store.approve(action["id"])
    assert result is not None
    assert result["status"] == "approved"
    assert result["resolved_at"] is not None


async def test_deny_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(
        vault_name="vault", description="Pay", requester="dev"
    )
    result = await vault_store.deny(action["id"])
    assert result is not None
    assert result["status"] == "denied"


async def test_approve_nonexistent_returns_none(vault_store: VaultStore) -> None:
    result = await vault_store.approve(99999)
    assert result is None


async def test_vault_log(vault_store: VaultStore) -> None:
    await vault_store.create_action(
        vault_name="vault", description="Action 1", requester="dev"
    )
    action = await vault_store.create_action(
        vault_name="vault", description="Action 2", requester="dev"
    )
    await vault_store.approve(action["id"])

    log = await vault_store.log("vault")
    assert len(log) == 2
