"""Tests for VaultStore — pending action approval flow."""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from hive.bus.vault_store import VaultStore


async def test_create_pending_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(
        vault_name="vault", description="Pay invoice #123", requester="dev"
    )
    assert action["id"] is not None
    assert action["status"] == "pending"
    assert action["vault_name"] == "vault"


async def test_list_pending_actions(vault_store: VaultStore) -> None:
    await vault_store.create_action(vault_name="vault", description="Action 1", requester="dev")
    await vault_store.create_action(vault_name="vault", description="Action 2", requester="dev")

    pending = await vault_store.pending("vault")
    assert len(pending) == 2


async def test_approve_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(vault_name="vault", description="Pay", requester="dev")
    result = await vault_store.approve(action["id"])
    assert result is not None
    assert result["status"] == "approved"
    assert result["resolved_at"] is not None


async def test_deny_action(vault_store: VaultStore) -> None:
    action = await vault_store.create_action(vault_name="vault", description="Pay", requester="dev")
    result = await vault_store.deny(action["id"])
    assert result is not None
    assert result["status"] == "denied"


async def test_approve_nonexistent_returns_none(vault_store: VaultStore) -> None:
    result = await vault_store.approve(99999)
    assert result is None


async def test_vault_log(vault_store: VaultStore) -> None:
    await vault_store.create_action(vault_name="vault", description="Action 1", requester="dev")
    action = await vault_store.create_action(
        vault_name="vault", description="Action 2", requester="dev"
    )
    await vault_store.approve(action["id"])

    log = await vault_store.log("vault")
    assert len(log) == 2


# -----------------------------------------------------------------------------
# Sprint 25 — payment-typed action flow
# -----------------------------------------------------------------------------


async def test_create_payment_action_persists_structured_fields(
    vault_store: VaultStore,
) -> None:
    row = await vault_store.create_action(
        vault_name="vault",
        description="Pay $5.00 USD to test@example.com: smoke",
        requester="vault",
        action_type="payment",
        amount_cents=500,
        currency="USD",
        recipient="test@example.com",
        idempotency_key="key-1",
        payload={"reason": "smoke"},
    )
    assert row["action_type"] == "payment"
    assert row["amount_cents"] == 500
    assert row["currency"] == "USD"
    assert row["recipient"] == "test@example.com"
    assert row["idempotency_key"] == "key-1"


async def test_get_returns_row(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(vault_name="vault", description="x", requester="vault")
    fetched = await vault_store.get(row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]


async def test_get_missing_returns_none(vault_store: VaultStore) -> None:
    assert await vault_store.get(99999) is None


async def test_idempotency_key_unique(vault_store: VaultStore) -> None:
    await vault_store.create_action(
        vault_name="vault",
        description="first",
        requester="vault",
        action_type="payment",
        amount_cents=100,
        currency="USD",
        recipient="r",
        idempotency_key="dup",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await vault_store.create_action(
            vault_name="vault",
            description="second",
            requester="vault",
            action_type="payment",
            amount_cents=200,
            currency="USD",
            recipient="r",
            idempotency_key="dup",
        )


async def test_mark_executed_sets_completed(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=500,
        currency="USD",
        recipient="r",
        idempotency_key="exec-1",
    )
    result = await vault_store.mark_executed(row["id"], {"provider": "stub", "ref": "abc"})
    assert result is not None
    assert result["status"] == "completed"
    assert result["executed_at"] is not None
    assert result["execution_result"] == '{"provider": "stub", "ref": "abc"}' or (
        isinstance(result["execution_result"], dict) and result["execution_result"]["ref"] == "abc"
    )


async def test_mark_failed_sets_failed_with_reason(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=500,
        currency="USD",
        recipient="r",
        idempotency_key="fail-1",
    )
    result = await vault_store.mark_failed(row["id"], "provider down")
    assert result is not None
    assert result["status"] == "failed"
    assert result["denial_reason"] == "provider down"


async def test_mark_executed_skips_non_pending(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=500,
        currency="USD",
        recipient="r",
        idempotency_key="skip-1",
    )
    await vault_store.deny(row["id"], reason="user denied")
    # Now in 'denied' — mark_executed must not flip it to 'completed'.
    result = await vault_store.mark_executed(row["id"], {"provider": "stub"})
    assert result is None


async def test_spend_total_cents_sums_only_completed_payments(
    vault_store: VaultStore,
) -> None:
    # one completed, one pending, one failed, one wrong currency, one wrong type
    completed = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=300,
        currency="USD",
        recipient="r",
        idempotency_key="ok-1",
    )
    await vault_store.mark_executed(completed["id"], {"ok": True})

    await vault_store.create_action(  # still pending
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=400,
        currency="USD",
        recipient="r",
        idempotency_key="pend-1",
    )

    failed = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=200,
        currency="USD",
        recipient="r",
        idempotency_key="fail-2",
    )
    await vault_store.mark_failed(failed["id"], "boom")

    wrong_curr = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=999,
        currency="EUR",
        recipient="r",
        idempotency_key="eur-1",
    )
    await vault_store.mark_executed(wrong_curr["id"], {"ok": True})

    # generic action (Sprint 6 path) — not a payment, must be excluded
    generic = await vault_store.create_action(
        vault_name="vault", description="generic", requester="dev"
    )
    await vault_store.approve(generic["id"])

    since = datetime.now(UTC) - timedelta(hours=24)
    total = await vault_store.spend_total_cents("vault", "USD", since)
    assert total == 300  # only the one completed USD payment


async def test_deny_records_reason(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(vault_name="vault", description="x", requester="vault")
    result = await vault_store.deny(row["id"], reason="not authorised")
    assert result is not None
    assert result["denial_reason"] == "not authorised"
