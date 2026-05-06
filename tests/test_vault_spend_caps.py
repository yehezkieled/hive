"""Sprint 25 — spend cap tests."""

from __future__ import annotations

import pytest

from hive.bus.vault_store import VaultStore
from hive.vault.spend_caps import check_caps


async def _completed_payment(
    vault_store: VaultStore,
    *,
    amount_cents: int,
    currency: str = "USD",
    idempotency_key: str,
) -> int:
    row = await vault_store.create_action(
        vault_name="vault",
        description="x",
        requester="vault",
        action_type="payment",
        amount_cents=amount_cents,
        currency=currency,
        recipient="r",
        idempotency_key=idempotency_key,
    )
    await vault_store.mark_executed(row["id"], {"provider": "stub", "ok": True})
    return row["id"]


async def test_check_caps_allows_under_both_caps(vault_store: VaultStore) -> None:
    await _completed_payment(vault_store, amount_cents=100, idempotency_key="d-1")
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=200,
        currency="USD",
        daily_cap_cents=1000,
        monthly_cap_cents=5000,
    )
    assert cap.ok is True
    assert cap.daily_used_cents == 100


async def test_check_caps_blocks_when_daily_exceeded(vault_store: VaultStore) -> None:
    await _completed_payment(vault_store, amount_cents=900, idempotency_key="d-2")
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=200,
        currency="USD",
        daily_cap_cents=1000,
        monthly_cap_cents=10000,
    )
    assert cap.ok is False
    assert cap.reason is not None
    assert "daily" in cap.reason


async def test_check_caps_blocks_when_monthly_exceeded(vault_store: VaultStore) -> None:
    # Two payments each within daily window but together hit monthly.
    await _completed_payment(vault_store, amount_cents=4000, idempotency_key="m-1")
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=2000,
        currency="USD",
        daily_cap_cents=10000,
        monthly_cap_cents=5000,
    )
    assert cap.ok is False
    assert cap.reason is not None
    assert "monthly" in cap.reason


async def test_check_caps_zero_caps_means_disabled(vault_store: VaultStore) -> None:
    """A 0 cap means "no cap" — used by tests/dev environments."""
    await _completed_payment(vault_store, amount_cents=10_000_00, idempotency_key="z-1")
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=10_000_00,
        currency="USD",
        daily_cap_cents=0,
        monthly_cap_cents=0,
    )
    assert cap.ok is True


async def test_check_caps_pending_rows_dont_count(vault_store: VaultStore) -> None:
    """Caps only deduct money that's actually moved (status='completed')."""
    # Create a pending payment — must NOT be deducted from the cap.
    await vault_store.create_action(
        vault_name="vault",
        description="pending",
        requester="vault",
        action_type="payment",
        amount_cents=900,
        currency="USD",
        recipient="r",
        idempotency_key="pend-cap-1",
    )
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=200,
        currency="USD",
        daily_cap_cents=1000,
        monthly_cap_cents=10000,
    )
    assert cap.ok is True


async def test_check_caps_failed_rows_dont_count(vault_store: VaultStore) -> None:
    row = await vault_store.create_action(
        vault_name="vault",
        description="failed",
        requester="vault",
        action_type="payment",
        amount_cents=900,
        currency="USD",
        recipient="r",
        idempotency_key="fail-cap-1",
    )
    await vault_store.mark_failed(row["id"], "provider down")
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=200,
        currency="USD",
        daily_cap_cents=1000,
        monthly_cap_cents=10000,
    )
    assert cap.ok is True


async def test_check_caps_other_currency_excluded(vault_store: VaultStore) -> None:
    """A completed EUR payment must not deduct from the USD cap."""
    await _completed_payment(
        vault_store, amount_cents=900, currency="EUR", idempotency_key="eur-cap-1"
    )
    cap = await check_caps(
        vault_store,
        vault_name="vault",
        amount_cents=200,
        currency="USD",
        daily_cap_cents=1000,
        monthly_cap_cents=10000,
    )
    assert cap.ok is True
    assert cap.daily_used_cents == 0


async def test_check_caps_unsupported_currency_raises(vault_store: VaultStore) -> None:
    with pytest.raises(ValueError):
        await check_caps(
            vault_store,
            vault_name="vault",
            amount_cents=100,
            currency="EUR",
            daily_cap_cents=1000,
            monthly_cap_cents=10000,
        )
