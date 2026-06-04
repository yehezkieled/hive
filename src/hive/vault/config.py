"""Configuration surface for the Vault / payment subsystem.

Vault / payment lead (Sprint 25). The default vault is opt-in until a
real provider ships — flip ``HIVE_VAULT_ENABLED=true`` to register the
default vault entity on startup. Caps are applied per-currency
independently: ``HIVE_VAULT_CAP_CURRENCIES`` is the comma-separated
allow-list (default ``AUD,USD``); the same daily/monthly cap (in
minor units / cents) applies to each currency separately, so a
$50/day cap means $50 AUD/day AND $50 USD/day. Action currencies
outside the allow-list are rejected at cap-check time. No FX
conversion — that's a future-sprint concern. Provider names:
``stub`` (no real money). A future sprint adds ``stripe`` etc.
behind the same Protocol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class VaultConfig:
    """Grouped env-derived config for the Vault payment boundary.

    One immutable value object so the money-spending boundary's inputs
    read in one place. Built once at the composition root via
    ``from_env()``.
    """

    enabled: bool
    provider: str
    daily_cap_cents: int
    monthly_cap_cents: int
    cap_currencies: tuple[str, ...]

    @classmethod
    def from_env(cls) -> VaultConfig:
        """Build a VaultConfig from the ``HIVE_VAULT_*`` environment.

        Calls ``load_dotenv()`` (idempotent) so this module is
        self-contained with no import dependency on ``hive.config``.
        """
        load_dotenv()
        return cls(
            enabled=os.environ.get("HIVE_VAULT_ENABLED", "false").lower() == "true",
            provider=os.environ.get("HIVE_VAULT_PROVIDER", "stub"),
            daily_cap_cents=int(os.environ.get("HIVE_VAULT_DAILY_CAP_CENTS", "5000")),
            monthly_cap_cents=int(os.environ.get("HIVE_VAULT_MONTHLY_CAP_CENTS", "50000")),
            cap_currencies=tuple(
                sorted(
                    {
                        c.strip().upper()
                        for c in os.environ.get("HIVE_VAULT_CAP_CURRENCIES", "AUD,USD").split(",")
                        if c.strip()
                    }
                )
            ),
        )
