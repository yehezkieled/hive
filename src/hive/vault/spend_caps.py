"""Spend-cap policy for the Vault approval flow.

Daily and monthly caps are configured per-process (``HIVE_VAULT_*_CAP_CENTS``).
Both windows must allow the new payment for it to clear; whichever cap
is hit first becomes the rejection reason. USD-only for Sprint 25 —
non-USD requests raise ``ValueError`` so caller surfaces are forced to
think about it explicitly rather than silently bypassing the check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive.bus.vault_store import VaultStore

_SUPPORTED_CURRENCIES = frozenset({"USD"})


@dataclass(frozen=True)
class CapCheck:
    """Outcome of a cap evaluation. ``ok=False`` carries a human reason."""

    ok: bool
    reason: str | None = None
    daily_used_cents: int = 0
    monthly_used_cents: int = 0


def _format_dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


async def check_caps(
    vault_store: VaultStore,
    *,
    vault_name: str,
    amount_cents: int,
    currency: str,
    daily_cap_cents: int,
    monthly_cap_cents: int,
    now: datetime | None = None,
) -> CapCheck:
    """Decide whether ``amount_cents`` clears the daily and monthly caps.

    Counts only ``status='completed'`` rows in the same currency window.
    Pending/failed/denied requests are not deducted — only money that
    has actually moved counts against the cap.
    """
    if currency.upper() not in _SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Unsupported currency for spend caps: {currency!r}. "
            f"Supported: {sorted(_SUPPORTED_CURRENCIES)}"
        )
    cap_currency = currency.upper()
    now = now or datetime.now(UTC)
    daily_since = now - timedelta(hours=24)
    monthly_since = now - timedelta(days=30)

    daily_used = await vault_store.spend_total_cents(vault_name, cap_currency, daily_since)
    monthly_used = await vault_store.spend_total_cents(vault_name, cap_currency, monthly_since)

    if daily_cap_cents > 0 and daily_used + amount_cents > daily_cap_cents:
        return CapCheck(
            ok=False,
            reason=(
                f"daily cap exceeded: {_format_dollars(daily_used)} used "
                f"+ {_format_dollars(amount_cents)} requested > "
                f"{_format_dollars(daily_cap_cents)} cap"
            ),
            daily_used_cents=daily_used,
            monthly_used_cents=monthly_used,
        )

    if monthly_cap_cents > 0 and monthly_used + amount_cents > monthly_cap_cents:
        return CapCheck(
            ok=False,
            reason=(
                f"monthly cap exceeded: {_format_dollars(monthly_used)} used "
                f"+ {_format_dollars(amount_cents)} requested > "
                f"{_format_dollars(monthly_cap_cents)} cap"
            ),
            daily_used_cents=daily_used,
            monthly_used_cents=monthly_used,
        )

    return CapCheck(
        ok=True,
        daily_used_cents=daily_used,
        monthly_used_cents=monthly_used,
    )
