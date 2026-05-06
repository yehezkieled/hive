"""Spend-cap policy for the Vault approval flow.

Caps are applied per-currency independently: the same daily / monthly
cap (in minor units / cents) applies to each currency in the
allow-list separately. So a $50/day cap with allow-list
``[AUD, USD]`` means $50 AUD/day AND $50 USD/day, not a combined
total. No FX conversion — actions whose currency isn't in the
allow-list raise ``ValueError`` so callers think about it explicitly
rather than silently bypassing the check.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive.bus.vault_store import VaultStore


@dataclass(frozen=True)
class CapCheck:
    """Outcome of a cap evaluation. ``ok=False`` carries a human reason."""

    ok: bool
    reason: str | None = None
    daily_used_cents: int = 0
    monthly_used_cents: int = 0


def _format_money(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:.2f}"


def _normalise_currencies(currencies: Iterable[str]) -> frozenset[str]:
    return frozenset(c.upper() for c in currencies if c)


async def check_caps(
    vault_store: VaultStore,
    *,
    vault_name: str,
    amount_cents: int,
    currency: str,
    daily_cap_cents: int,
    monthly_cap_cents: int,
    cap_currencies: Iterable[str] = ("AUD", "USD"),
    now: datetime | None = None,
) -> CapCheck:
    """Decide whether ``amount_cents`` clears the daily and monthly caps.

    The same cap value applies to each currency in ``cap_currencies``
    independently. Counts only ``status='completed'`` rows in the
    action's currency window — pending / failed / denied requests
    are not deducted, and other currencies don't pollute this
    currency's tally.
    """
    action_currency = currency.upper()
    allowed = _normalise_currencies(cap_currencies)
    if action_currency not in allowed:
        raise ValueError(
            f"Currency {action_currency!r} not in cap allow-list "
            f"{sorted(allowed)}. Add it to HIVE_VAULT_CAP_CURRENCIES "
            f"or reject the request."
        )
    now = now or datetime.now(UTC)
    daily_since = now - timedelta(hours=24)
    monthly_since = now - timedelta(days=30)

    daily_used = await vault_store.spend_total_cents(vault_name, action_currency, daily_since)
    monthly_used = await vault_store.spend_total_cents(vault_name, action_currency, monthly_since)

    if daily_cap_cents > 0 and daily_used + amount_cents > daily_cap_cents:
        return CapCheck(
            ok=False,
            reason=(
                f"daily cap exceeded: {_format_money(daily_used, action_currency)} used "
                f"+ {_format_money(amount_cents, action_currency)} requested > "
                f"{_format_money(daily_cap_cents, action_currency)} cap"
            ),
            daily_used_cents=daily_used,
            monthly_used_cents=monthly_used,
        )

    if monthly_cap_cents > 0 and monthly_used + amount_cents > monthly_cap_cents:
        return CapCheck(
            ok=False,
            reason=(
                f"monthly cap exceeded: {_format_money(monthly_used, action_currency)} used "
                f"+ {_format_money(amount_cents, action_currency)} requested > "
                f"{_format_money(monthly_cap_cents, action_currency)} cap"
            ),
            daily_used_cents=daily_used,
            monthly_used_cents=monthly_used,
        )

    return CapCheck(
        ok=True,
        daily_used_cents=daily_used,
        monthly_used_cents=monthly_used,
    )
