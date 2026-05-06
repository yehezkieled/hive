"""Vault payment subsystem — providers and spend-cap policy.

Sprint 25 introduces a stub provider and a USD-only spend-cap check so
the request → approve → execute → audit pipeline can be exercised
end-to-end without moving real money. A future sprint swaps Stripe in
behind ``PaymentProvider``.
"""

from hive.vault.provider import (
    ExecutionResult,
    PaymentProvider,
    StubPaymentProvider,
    build_provider,
)
from hive.vault.spend_caps import CapCheck, check_caps

__all__ = [
    "CapCheck",
    "ExecutionResult",
    "PaymentProvider",
    "StubPaymentProvider",
    "build_provider",
    "check_caps",
]
