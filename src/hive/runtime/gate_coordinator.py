"""GateCoordinator — park-and-wake lifecycle for one interactive gate.

This is the stateful deep module of Ticket 003's bridge. PtySession just
``await``s ``resolve(...)`` and injects the result — it never touches
doorbells, approval rows, or nudge timers; all of that lives here.

The flow (ADR 0004):

1. ``resolve`` creates a pending approval row tagged ``kind="gate"`` — the
   durable surface that spans Telegram and the web dashboard.
2. It registers an ``asyncio.Event`` doorbell keyed to the Entity and parks
   on it **forever**. No timeout, never auto-decide.
3. ``/approve`` or ``/deny`` marks the row resolved and ``ring()``s the
   doorbell, waking the parked ``resolve``.
4. On wake, ``resolve`` reads the decision off the row's status and returns
   the keystrokes from ``KeystrokePlanner``.

The doorbell is in-memory by design: a Hive restart kills the held Turn
anyway, so there is nothing to persist for the wake path (the row survives
for re-detection — recovery is a later slice).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from hive.runtime.gates import Gate, KeystrokePlanner

logger = logging.getLogger(__name__)


class _ApprovalStore(Protocol):
    """The subset of ModeRequestStore the coordinator needs."""

    async def create(
        self,
        requester: str,
        requested_mode: str,
        approver: str,
        reason: str | None = ...,
        kind: str = ...,
    ) -> dict: ...

    async def get(self, request_id: int) -> dict | None: ...


# Status -> plan decision. Anything that isn't an approval is treated as a
# denial so a parked Turn never injects an approve on an ambiguous status.
_APPROVED_STATUS = "approved"


class GateCoordinator:
    """Owns the doorbell + approval-row surface for interactive gates."""

    def __init__(
        self,
        store: _ApprovalStore,
        planner: KeystrokePlanner | None = None,
    ) -> None:
        self._store = store
        self._planner = planner or KeystrokePlanner()
        # entity name -> doorbell. One in-flight gate per Entity (its single
        # PTY can only sit on one menu at a time, ADR 0004).
        self._doorbells: dict[str, asyncio.Event] = {}
        # entity name -> the pending row id, so the wake path can read the
        # resolved decision back off the store.
        self._pending: dict[str, int] = {}

    async def resolve(self, entity_name: str, gate: Gate, *, approver: str) -> list[str]:
        """Park on ``gate`` until the user decides, then return the keys.

        Creates the durable approval row, registers the doorbell, and blocks
        forever on it. Never auto-decides. On wake, reads the row's status and
        plans the keystrokes for that decision.
        """
        row = await self._store.create(
            requester=entity_name,
            requested_mode=gate.kind,
            approver=approver,
            reason=self._reason_for(gate),
            kind="gate",
        )
        request_id = row["id"]

        doorbell = asyncio.Event()
        self._doorbells[entity_name] = doorbell
        self._pending[entity_name] = request_id

        try:
            await doorbell.wait()  # park forever — no timeout, no auto-decide
        finally:
            # Always tear down so a later gate on the same Entity parks afresh
            # rather than waking on this stale doorbell.
            self._doorbells.pop(entity_name, None)
            self._pending.pop(entity_name, None)

        resolved = await self._store.get(request_id)
        decision = self._decision_from_row(resolved)
        return self._plan_keys(gate, decision)

    def ring(self, entity_name: str) -> None:
        """Wake the Turn parked on this Entity's gate. No-op if none parked."""
        doorbell = self._doorbells.get(entity_name)
        if doorbell is None:
            logger.debug("ring(%s): no gate parked, ignoring", entity_name)
            return
        doorbell.set()

    def pending_request_id(self, entity_name: str) -> int | None:
        """The approval-row id of the gate this Entity is parked on, if any."""
        return self._pending.get(entity_name)

    @staticmethod
    def _reason_for(gate: Gate) -> str | None:
        if gate.kind == "plan":
            return gate.payload.get("plan")
        return None

    @staticmethod
    def _decision_from_row(row: dict | None) -> str:
        """Map an approval-row status to a gate decision (approve/deny)."""
        status = (row or {}).get("status")
        return "approve" if status == _APPROVED_STATUS else "deny"

    def _plan_keys(self, gate: Gate, decision: str) -> list[str]:
        if gate.kind == "plan":
            return self._planner.plan_keys(gate, decision)
        raise NotImplementedError(f"No keystroke plan for gate kind {gate.kind!r}")
