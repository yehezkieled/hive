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
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from hive.runtime.gates import Gate, KeystrokePlanner

logger = logging.getLogger(__name__)

# Default no-answer nudge cadence: re-ping the user's surface once an hour
# while a gate stays parked, then keep waiting. Never auto-decides (#25).
_DEFAULT_NUDGE_INTERVAL_SECONDS = 3600.0

# on_nudge(entity_name, request_id). May be sync or async; the coordinator
# awaits the result when it's a coroutine.
NudgeCallback = Callable[[str, int], Awaitable[None] | None]


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
        *,
        nudge_interval_seconds: float = _DEFAULT_NUDGE_INTERVAL_SECONDS,
        on_nudge: NudgeCallback | None = None,
    ) -> None:
        self._store = store
        self._planner = planner or KeystrokePlanner()
        # entity name -> doorbell. One in-flight gate per Entity (its single
        # PTY can only sit on one menu at a time, ADR 0004).
        self._doorbells: dict[str, asyncio.Event] = {}
        # entity name -> the pending row id, so the wake path can read the
        # resolved decision back off the store.
        self._pending: dict[str, int] = {}
        # No-answer nudge (#25): re-ping the user's surface every interval
        # while parked. This never resolves the gate — it only re-surfaces it.
        self._nudge_interval_seconds = nudge_interval_seconds
        self._on_nudge = on_nudge

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
            await self._park(entity_name, request_id, doorbell)
        finally:
            # Always tear down so a later gate on the same Entity parks afresh
            # rather than waking on this stale doorbell.
            self._doorbells.pop(entity_name, None)
            self._pending.pop(entity_name, None)

        resolved = await self._store.get(request_id)
        return self._keys_for(gate, resolved)

    async def _park(self, entity_name: str, request_id: int, doorbell: asyncio.Event) -> None:
        """Block until the doorbell rings, nudging the user each interval.

        Park-forever and never-auto-decide are preserved: the only exit is the
        doorbell. Each ``nudge_interval_seconds`` that elapses while still
        parked fires ``on_nudge`` and then keeps waiting — it never resolves the
        gate. When the doorbell rings, the in-flight interval wait is abandoned
        and ``_park`` returns.
        """
        while not doorbell.is_set():
            try:
                await asyncio.wait_for(doorbell.wait(), timeout=self._nudge_interval_seconds)
            except TimeoutError:
                # Interval elapsed and the gate is still pending. Re-ping the
                # user, then loop back and keep waiting. Never auto-decide.
                await self._fire_nudge(entity_name, request_id)

    async def _fire_nudge(self, entity_name: str, request_id: int) -> None:
        """Invoke the nudge callback, tolerating sync or async callbacks.

        A failing nudge must never break the park — re-surfacing is best-effort,
        the durable approval row is the source of truth.
        """
        if self._on_nudge is None:
            return
        try:
            result = self._on_nudge(entity_name, request_id)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Nudge callback failed for gate %s (entity %s)",
                request_id,
                entity_name,
            )

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
        if gate.kind == "ask":
            return gate.payload.get("question")
        return None

    def _keys_for(self, gate: Gate, row: dict | None) -> list[str]:
        """Plan the keystrokes for the resolved gate row.

        Plan gates resolve to approve/deny (Enter vs navigate-to-reject). Ask
        gates resolve to the user's chosen option index, carried on the row,
        which maps to ``Down × index`` + Enter. Anything not explicitly an
        approval is treated as a denial so a parked Turn never injects an
        approve on an ambiguous status.
        """
        if gate.kind == "plan":
            decision = self._decision_from_row(row)
            return self._planner.plan_keys(gate, decision)
        if gate.kind == "ask":
            option_index = self._chosen_option_from_row(row)
            return self._planner.ask_keys(gate, option_index)
        raise NotImplementedError(f"No keystroke plan for gate kind {gate.kind!r}")

    @staticmethod
    def _decision_from_row(row: dict | None) -> str:
        """Map an approval-row status to a gate decision (approve/deny)."""
        status = (row or {}).get("status")
        return "approve" if status == _APPROVED_STATUS else "deny"

    @staticmethod
    def _chosen_option_from_row(row: dict | None) -> int:
        """Read the user's chosen option index off a resolved ask-gate row.

        ``/approve gate <id> <option>`` records the picked index on the row.
        Defaults to 0 (the highlighted first option) when absent so a bare
        approval still resolves to a valid selection rather than failing.
        """
        chosen = (row or {}).get("chosen_option")
        if isinstance(chosen, int):
            return chosen
        return 0
