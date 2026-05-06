"""Payment provider abstraction.

A ``PaymentProvider`` is the seam between the Vault approval flow and a
real-money execution backend. Sprint 25 ships only ``StubPaymentProvider``;
swapping in Stripe means writing a sibling class that satisfies the same
Protocol — no orchestrator changes.

Stub semantics:
- Always succeeds, except when the request's reason or description
  contains the magic substring ``FORCE_FAIL``. That gives tests and
  smoke-tests a deterministic failure path without depending on
  per-call mocking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of a payment provider's ``execute`` call."""

    ok: bool
    provider: str
    reference: str | None = None
    error: str | None = None
    details: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialise for storage in ``vault_actions.execution_result`` JSONB."""
        out: dict[str, Any] = {"provider": self.provider, "ok": self.ok}
        if self.reference is not None:
            out["reference"] = self.reference
        if self.error is not None:
            out["error"] = self.error
        if self.details:
            out["details"] = self.details
        return out


@runtime_checkable
class PaymentProvider(Protocol):
    """Anything that can execute a vault action."""

    name: str

    async def execute(self, action: dict) -> ExecutionResult: ...


class StubPaymentProvider:
    """No-op provider: pretends to send money and returns a fake reference.

    Honours a ``FORCE_FAIL`` substring in either the action's free-text
    description or its payload reason so tests can drive the failure
    branch deterministically.
    """

    name: str = "stub"

    async def execute(self, action: dict) -> ExecutionResult:
        description = (action.get("description") or "").lower()
        payload = action.get("payload") or {}
        reason = ""
        if isinstance(payload, dict):
            reason = str(payload.get("reason") or "").lower()

        if "force_fail" in description or "force_fail" in reason:
            return ExecutionResult(
                ok=False,
                provider=self.name,
                error="forced failure (FORCE_FAIL marker present)",
            )

        reference = f"stub-{action.get('id', '?')}-{action.get('idempotency_key', '?')}"
        logger.info(
            "stub provider executed action id=%s amount=%s ref=%s",
            action.get("id"),
            action.get("amount_cents"),
            reference,
        )
        return ExecutionResult(ok=True, provider=self.name, reference=reference)


def build_provider(name: str) -> PaymentProvider:
    """Construct a provider by name. Unknown names fall back to stub."""
    n = (name or "stub").lower()
    if n == "stub":
        return StubPaymentProvider()
    logger.warning("unknown payment provider %r, falling back to stub", name)
    return StubPaymentProvider()
