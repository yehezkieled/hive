"""Approval handler — mode-change, vault payment, interactive-gate, and
task-failure escalation flows lifted out of ProcessManager.

Collaborator object (Ticket 004): holds a back-reference to the owning
ProcessManager (`self._mgr`) and reaches all shared state and sibling
methods through it. It imports nothing from ``manager.py`` at runtime; the
manager type hint is under ``TYPE_CHECKING`` only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import asyncpg

from hive.models.entity import DANGEROUS_MODES, Entity, EntityState
from hive.models.team_lead import TeamLead
from hive.models.vault import Vault
from hive.models.worker import Worker
from hive.notifications import Notification
from hive.vault.spend_caps import check_caps

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


class ApprovalHandler:
    """Mode-change / vault / interactive-gate / task-failure flows.

    One responsibility cluster lifted out of ProcessManager. All shared
    state lives on the facade and is reached via ``self._mgr``.
    """

    def __init__(self, mgr: ProcessManager) -> None:
        self._mgr = mgr

    # -----------------------------------------------------------------
    # Mode-change approval flow (Sprint 12 Phase 2C)
    # -----------------------------------------------------------------

    def _approver_for(self, entity: Entity) -> str:
        """Return the approver name for a mode-elevation request.

        Maestros escalate to the user; leads escalate to their parent
        maestro; workers escalate to their parent lead.
        """
        if entity.role == "maestro":
            return "user"
        if entity.role == "lead" and isinstance(entity, TeamLead):
            return entity.maestro_name
        if entity.role == "worker" and isinstance(entity, Worker):
            return entity.lead_name
        raise ValueError(
            f"Cannot determine approver for entity {entity.name!r} (role={entity.role!r})"
        )

    async def request_mode_change(
        self,
        requester: str,
        requested_mode: str,
        reason: str | None = None,
    ) -> int:
        """Record a pending mode-elevation request.

        Raises KeyError if the requester is not registered, or ValueError
        if the requested mode is not an approval-gated one or the store
        has not been configured.
        """
        if self._mgr.mode_request_store is None:
            raise ValueError("mode_request_store not configured")
        if requested_mode not in DANGEROUS_MODES:
            raise ValueError(
                f"Mode {requested_mode!r} does not require approval. "
                f"Valid: {', '.join(sorted(DANGEROUS_MODES))}"
            )
        entity = self._mgr._entities.get(requester)
        if entity is None:
            raise KeyError(f"Unknown requester {requester!r}")

        approver = self._approver_for(entity)
        row = await self._mgr.mode_request_store.create(
            requester=requester,
            requested_mode=requested_mode,
            approver=approver,
            reason=reason,
        )
        await self._mgr._audit(
            "mode.request",
            target=requester,
            details={
                "id": row["id"],
                "requested_mode": requested_mode,
                "approver": approver,
                "reason": reason,
            },
        )
        if approver == "user":
            reason_text = reason or "(no reason given)"
            await self._mgr._notify(
                f"[mode request #{row['id']}] {requester} -> {requested_mode}. "
                f"Reason: {reason_text}\n"
                f"Approve: /approve mode {row['id']}   "
                f"Deny: /deny mode {row['id']} <reason>",
                kind="mode_request",
                data={
                    "id": row["id"],
                    "requester": requester,
                    "requested_mode": requested_mode,
                    "reason": reason,
                },
            )
        return row["id"]

    async def request_payment(
        self,
        requester: str,
        *,
        amount_cents: int,
        currency: str,
        recipient: str,
        idempotency_key: str,
        reason: str,
    ) -> int | None:
        """Record a pending payment request from a Vault entity.

        Only entities with role=='vault' may request payments. Returns the
        new vault_actions row id, or None when the store is not configured.
        Raises ``PermissionError`` for non-Vault requesters,
        ``KeyError`` for unknown requesters, and ``ValueError`` for
        invalid inputs. Duplicate ``idempotency_key`` is caught and audited
        as ``vault.duplicate_idempotency_key``; the method returns None.
        """
        if self._mgr.vault_store is None:
            return None
        if amount_cents <= 0:
            raise ValueError(f"amount_cents must be positive, got {amount_cents}")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not recipient:
            raise ValueError("recipient is required")
        currency_norm = currency.upper()
        if len(currency_norm) != 3:
            raise ValueError(f"currency must be a 3-letter code, got {currency!r}")

        entity = self._mgr._entities.get(requester)
        if entity is None:
            raise KeyError(f"Unknown requester {requester!r}")
        if not isinstance(entity, Vault):
            await self._mgr._audit(
                "vault.unauthorized",
                target=requester,
                details={
                    "role": entity.role,
                    "amount_cents": amount_cents,
                    "currency": currency_norm,
                    "recipient": recipient,
                },
                actor=requester,
            )
            raise PermissionError(
                f"request_payment requires role=vault; {requester!r} is {entity.role!r}"
            )

        description = f"Pay {amount_cents / 100:.2f} {currency_norm} to {recipient}: {reason}"
        try:
            row = await self._mgr.vault_store.create_action(
                vault_name=requester,
                description=description,
                requester=requester,
                action_type="payment",
                amount_cents=amount_cents,
                currency=currency_norm,
                recipient=recipient,
                idempotency_key=idempotency_key,
                payload={"reason": reason},
            )
        except asyncpg.UniqueViolationError:
            await self._mgr._audit(
                "vault.duplicate_idempotency_key",
                target=requester,
                details={"idempotency_key": idempotency_key, "recipient": recipient},
                actor=requester,
            )
            return None

        await self._mgr._audit(
            "vault.requested",
            target=requester,
            details={
                "id": row["id"],
                "amount_cents": amount_cents,
                "currency": currency_norm,
                "recipient": recipient,
                "idempotency_key": idempotency_key,
            },
            actor=requester,
        )
        await self._mgr._notify(
            f"[vault request #{row['id']}] {requester}: pay "
            f"{amount_cents / 100:.2f} {currency_norm} to {recipient}. "
            f"Reason: {reason}\n"
            f"Approve: /vault approve {row['id']}   "
            f"Deny: /vault deny {row['id']}",
            kind="vault_action_pending",
            data={
                "id": row["id"],
                "requester": requester,
                "amount_cents": amount_cents,
                "currency": currency_norm,
                "recipient": recipient,
                "reason": reason,
            },
        )
        return row["id"]

    async def approve_vault_action(self, action_id: int) -> dict | None:
        """Run the full payment lifecycle for a pending vault action.

        Sequence: load → cap check → execute → mark completed/failed →
        audit + notify. Idempotent: repeated calls on a non-pending row
        return that row unchanged. Generic Sprint 6 actions (action_type
        != 'payment') fall through to the legacy ``vault_store.approve``
        path so the historical free-text approval surface still works.
        """
        if self._mgr.vault_store is None:
            return None
        row = await self._mgr.vault_store.get(action_id)
        if row is None:
            return None
        if row["status"] != "pending":
            return row

        # Legacy generic action — keep the Sprint 6 free-text path alive.
        if row.get("action_type") != "payment":
            approved = await self._mgr.vault_store.approve(action_id)
            if approved is not None:
                await self._mgr._audit(
                    "vault.approved",
                    target=approved["vault_name"],
                    details={"id": action_id, "action_type": "generic"},
                )
            return approved

        amount = int(row["amount_cents"])
        currency = row["currency"]
        recipient = row["recipient"]
        vault_name = row["vault_name"]

        try:
            cap = await check_caps(
                self._mgr.vault_store,
                vault_name=vault_name,
                amount_cents=amount,
                currency=currency,
                daily_cap_cents=self._mgr.vault_daily_cap_cents,
                monthly_cap_cents=self._mgr.vault_monthly_cap_cents,
                cap_currencies=self._mgr.vault_cap_currencies,
            )
        except ValueError as exc:
            cap_reason = str(exc)
            denied = await self._mgr.vault_store.deny(action_id, reason=cap_reason)
            await self._mgr._audit(
                "vault.cap_exceeded",
                target=vault_name,
                details={"id": action_id, "reason": cap_reason},
            )
            await self._mgr._notify(
                f"[vault denied #{action_id}] {cap_reason}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "denied", "reason": cap_reason},
            )
            return denied

        if not cap.ok:
            denied = await self._mgr.vault_store.deny(action_id, reason=cap.reason)
            await self._mgr._audit(
                "vault.cap_exceeded",
                target=vault_name,
                details={
                    "id": action_id,
                    "reason": cap.reason,
                    "amount_cents": amount,
                    "currency": currency,
                    "daily_used_cents": cap.daily_used_cents,
                    "monthly_used_cents": cap.monthly_used_cents,
                },
            )
            await self._mgr._notify(
                f"[vault denied #{action_id}] {cap.reason}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "denied", "reason": cap.reason},
            )
            return denied

        provider = self._mgr.payment_provider
        if provider is None:
            err = "no payment provider configured"
            failed = await self._mgr.vault_store.mark_failed(action_id, err)
            await self._mgr._audit(
                "vault.failed",
                target=vault_name,
                details={"id": action_id, "reason": err},
            )
            return failed

        try:
            result = await provider.execute(dict(row))
        except Exception as exc:
            err = f"provider raised: {exc!r}"
            logger.exception("payment provider raised for action %s", action_id)
            failed = await self._mgr.vault_store.mark_failed(action_id, err)
            await self._mgr._audit(
                "vault.failed",
                target=vault_name,
                details={"id": action_id, "reason": err},
            )
            await self._mgr._notify(
                f"[vault failed #{action_id}] {err}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "failed", "reason": err},
            )
            return failed

        if not result.ok:
            failed = await self._mgr.vault_store.mark_failed(
                action_id,
                result.error or "provider reported failure",
                result.to_payload(),
            )
            await self._mgr._audit(
                "vault.failed",
                target=vault_name,
                details={
                    "id": action_id,
                    "reason": result.error,
                    "provider": result.provider,
                    "amount_cents": amount,
                    "currency": currency,
                    "recipient": recipient,
                },
            )
            await self._mgr._notify(
                f"[vault failed #{action_id}] {result.error or 'provider failure'}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "failed", "reason": result.error},
            )
            return failed

        completed = await self._mgr.vault_store.mark_executed(action_id, result.to_payload())
        await self._mgr._audit(
            "vault.executed",
            target=vault_name,
            details={
                "id": action_id,
                "provider": result.provider,
                "reference": result.reference,
                "amount_cents": amount,
                "currency": currency,
                "recipient": recipient,
            },
        )
        await self._mgr._notify(
            f"[vault executed #{action_id}] {amount / 100:.2f} {currency} → {recipient} "
            f"(ref {result.reference})",
            kind="vault_action_resolved",
            data={
                "id": action_id,
                "status": "completed",
                "reference": result.reference,
                "amount_cents": amount,
                "currency": currency,
            },
        )
        return completed

    async def deny_vault_action(self, action_id: int, reason: str | None = None) -> dict | None:
        """Deny a pending vault action (any action_type) and audit the event."""
        if self._mgr.vault_store is None:
            return None
        denied = await self._mgr.vault_store.deny(action_id, reason=reason)
        if denied is None:
            return None
        await self._mgr._audit(
            "vault.denied",
            target=denied["vault_name"],
            details={"id": action_id, "reason": reason},
        )
        await self._mgr._notify(
            f"[vault denied #{action_id}] {reason or 'no reason given'}",
            kind="vault_action_resolved",
            data={"id": action_id, "status": "denied", "reason": reason},
        )
        return denied

    async def approve_mode_request(self, request_id: int) -> dict | None:
        """Approve a pending mode request and update the requester's mode.

        For ``yotree``, caller is responsible for ensuring a worktree is
        attached before the next spawn — workers already have one; leads
        and maestros need one provisioned by the caller or by a future
        spawn helper.
        """
        if self._mgr.mode_request_store is None:
            return None
        row = await self._mgr.mode_request_store.approve(request_id)
        if row is None:
            return None

        entity = self._mgr._entities.get(row["requester"])
        if entity is not None:
            entity.permission_mode = row["requested_mode"]
            await self._mgr._persist(entity)
        await self._mgr._audit(
            "mode.approve",
            target=row["requester"],
            details={"id": request_id, "mode": row["requested_mode"]},
        )
        return row

    async def deny_mode_request(self, request_id: int, reason: str | None = None) -> dict | None:
        """Deny a pending mode request. Entity's current mode is unchanged."""
        if self._mgr.mode_request_store is None:
            return None
        row = await self._mgr.mode_request_store.deny(request_id, reason=reason)
        if row is None:
            return None
        await self._mgr._audit(
            "mode.deny",
            target=row["requester"],
            details={"id": request_id, "reason": reason},
        )
        return row

    def _on_gate_state(self, entity_name: str, state: str) -> None:
        """React to a PtySession gate transition (Ticket 003 runtime wiring).

        On ``"gated"`` the Entity moves to GATED — exempt from idle-kill and the
        reader timeout — and the user is pushed the initial surface. On
        ``"running"`` it moves back to RUNNING. Called from PtySession's sync
        ``on_gate_state`` hook inside the event loop, so the notification is
        fired as a background task.
        """
        entity = self._mgr._entities.get(entity_name)
        if entity is None:
            return
        target = EntityState.GATED if state == "gated" else EntityState.RUNNING
        try:
            entity.transition_to(target)
        except Exception:
            logger.exception("gate transition to %s failed for %s", target, entity_name)
        if state == "gated":
            asyncio.create_task(self._notify_gate_waiting(entity_name))

    async def _notify_gate_waiting(self, entity_name: str) -> None:
        """Push the initial 'a gate is waiting' surface to the user (#22/#23)."""
        if self._mgr.notification_dispatcher is None:
            return
        request_id = (
            self._mgr.gate_coordinator.pending_request_id(entity_name)
            if self._mgr.gate_coordinator is not None
            else None
        )
        suffix = (
            f" Reply /approve gate {request_id} or /deny gate {request_id}."
            if request_id is not None
            else ""
        )
        await self._mgr.notification_dispatcher.dispatch(
            Notification(
                text=f"⏸ {entity_name} is waiting at an interactive gate.{suffix}",
                kind="gate",
                data={"entity": entity_name, "request_id": request_id},
            )
        )

    async def _gate_nudge(self, entity_name: str, request_id: int) -> None:
        """on_nudge callback (#25): re-ping the user about a still-parked gate."""
        if self._mgr.notification_dispatcher is None:
            return
        await self._mgr.notification_dispatcher.dispatch(
            Notification(
                text=(
                    f"⏸ {entity_name} is still waiting at a gate. "
                    f"Reply /approve gate {request_id} or /deny gate {request_id}."
                ),
                kind="gate",
                data={"entity": entity_name, "request_id": request_id},
            )
        )

    async def approve_gate(self, request_id: int, chosen_option: int | None = None) -> dict | None:
        """Approve a parked interactive gate and wake its Turn (Ticket 003).

        Marks the gate row approved, then rings the coordinator's doorbell
        keyed to the row's requester so the blocked Turn injects the approve
        keypress and resumes. Returns the resolved row, or None if the gate
        was not found or already resolved.

        ``chosen_option`` carries the picked AskUserQuestion option index for an
        ask gate (Ticket 003 #23); omit it for plan gates (binary approve/deny).
        """
        if self._mgr.mode_request_store is None:
            return None
        row = await self._mgr.mode_request_store.approve(request_id, chosen_option=chosen_option)
        if row is None:
            return None
        await self._mgr._audit(
            "gate.approve",
            target=row["requester"],
            details={"id": request_id},
        )
        if self._mgr.gate_coordinator is not None:
            self._mgr.gate_coordinator.ring(row["requester"])
        return row

    async def deny_gate(self, request_id: int, reason: str | None = None) -> dict | None:
        """Deny a parked interactive gate and wake its Turn (Ticket 003).

        Marks the gate row denied, then rings the doorbell so the blocked Turn
        injects the deny keypress (e.g. "keep planning") and resumes.
        """
        if self._mgr.mode_request_store is None:
            return None
        row = await self._mgr.mode_request_store.deny(request_id, reason=reason)
        if row is None:
            return None
        await self._mgr._audit(
            "gate.deny",
            target=row["requester"],
            details={"id": request_id, "reason": reason},
        )
        if self._mgr.gate_coordinator is not None:
            self._mgr.gate_coordinator.ring(row["requester"])
        return row

    async def reconcile_orphaned_gates(self, approver: str = "user") -> list[dict]:
        """Mark gate rows orphaned by a restart as stale (Ticket 003, #27).

        A Hive restart kills the in-memory doorbell and the parked coroutine,
        but the pending ``kind='gate'`` approval row survives in the DB with no
        coroutine behind it. Left alone it would dangle forever — the user sees
        a gate they can no longer resolve into a live Turn.

        On startup we deny those orphans with a ``"stale: lost on restart"``
        reason so the surface clears. This is the simplest safe recovery: we do
        **not** re-spawn the PTY and we never auto-approve — a denial just lets
        the user re-issue the turn. A row that still has a live doorbell (a Turn
        genuinely parked right now) is left untouched.

        Returns the rows it reconciled. No-op (``[]``) when no store is wired.
        """
        if self._mgr.mode_request_store is None:
            return []

        pending = await self._mgr.mode_request_store.list_pending(approver, kind="gate")
        reconciled: list[dict] = []
        for row in pending:
            requester = row["requester"]
            # Defensive: if a doorbell is live for this entity the Turn is
            # genuinely parked, not orphaned — leave it for the real /approve.
            if self._mgr.gate_coordinator is not None:
                if self._mgr.gate_coordinator.pending_request_id(requester) is not None:
                    continue
            denied = await self._mgr.mode_request_store.deny(
                row["id"], reason="stale: lost on restart"
            )
            if denied is None:
                continue
            reconciled.append(denied)
            await self._mgr._audit(
                "gate.reconcile_stale",
                target=requester,
                details={"id": row["id"]},
            )
            logger.info(
                "Reconciled orphaned gate %s for %s (stale: lost on restart)",
                row["id"],
                requester,
            )

        if reconciled:
            logger.info("Reconciled %d orphaned gate(s) on restore", len(reconciled))
        return reconciled

    async def expire_old_mode_requests(self, cutoff: datetime) -> list[dict]:
        """Expire pending mode requests older than cutoff. Returns expired rows."""
        if self._mgr.mode_request_store is None:
            return []
        rows = await self._mgr.mode_request_store.expire_older_than(cutoff)
        for row in rows:
            await self._mgr._audit(
                "mode.expire",
                target=row["requester"],
                details={"id": row["id"], "mode": row["requested_mode"]},
            )
        return rows

    # -----------------------------------------------------------------
    # Auto-recovery on task failures (Sprint 12 Phase 4)
    # -----------------------------------------------------------------

    def _escalation_target_for(self, entity_name: str) -> str:
        """Next rung up the hierarchy when a task fails past max retries.

        Workers escalate to their parent lead, leads to their parent
        maestro, maestros to the user. Returns ``"user"`` when escalation
        reaches the top.
        """
        entity = self._mgr._entities.get(entity_name)
        if isinstance(entity, Worker):
            return entity.lead_name
        if isinstance(entity, TeamLead):
            return entity.maestro_name
        return "user"

    async def handle_task_failure(self, task_id: int, error: str) -> None:
        """Retry the task on its current owner, then escalate on max retries.

        Flow:
          1. Bump ``retry_count`` and record the failure reason on the task.
          2. If ``retry_count < max_retries`` and the task still has an
             ``assigned_to`` entity, resend the original title to that
             entity prefixed with the failure context so Claude can retry.
          3. Otherwise escalate — route a failure report to the next rung
             (parent lead -> parent maestro -> user via Telegram notify).
        """
        if self._mgr.task_store is None:
            logger.warning("handle_task_failure called but task_store not configured")
            return

        task = await self._mgr.task_store.increment_retry(task_id, error)
        if task is None:
            logger.warning("handle_task_failure: task %s not found", task_id)
            return

        assigned = task.assigned_to
        if task.retry_count <= task.max_retries and assigned and assigned in self._mgr._entities:
            await self._mgr._audit(
                "task.retry",
                target=assigned,
                details={
                    "task_id": task_id,
                    "attempt": task.retry_count,
                    "reason": error[:200],
                },
            )
            retry_prompt = (
                f"[retry {task.retry_count}/{task.max_retries}] "
                f"Your previous attempt at this task failed: {error}\n\n"
                f"Task: {task.title}"
            )
            if task.description:
                retry_prompt += f"\n\n{task.description}"
            try:
                await self._mgr.send_to_entity(assigned, retry_prompt)
            except Exception:
                logger.exception("Retry send_to_entity failed for %s", assigned)
            return

        # Escalate: reached max retries, or no assignee to retry on.
        if assigned and assigned in self._mgr._entities:
            next_rung = self._escalation_target_for(assigned)
        else:
            next_rung = "user"
        await self._mgr.task_store.update_failure(task_id, error)
        await self._mgr._audit(
            "task.escalated",
            target=next_rung,
            details={
                "task_id": task_id,
                "from": assigned,
                "reason": error[:200],
                "attempts": task.retry_count,
            },
        )

        summary = (
            f"[task #{task_id}] {task.title!r} failed after "
            f"{task.retry_count} attempt(s).\nLast error: {error}"
        )
        if next_rung == "user":
            await self._mgr._audit(
                "task.gave_up",
                target=str(task_id),
                details={"reason": error[:200]},
            )
            await self._mgr._notify(summary)
            return

        # Escalate to a registered parent entity by routing an internal
        # message. The parent's next prompt will include this as pending
        # inbox content; they can decide to reassign, abort, or message
        # the user.
        if next_rung in self._mgr._entities and assigned is not None:
            await self._mgr.router.route(assigned, next_rung, summary)
