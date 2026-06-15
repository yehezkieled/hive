"""Process manager — registers, tracks, and kills Hive entities and their PTY adapters."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from hive.bus.actions import (
    Action,
    neutralize_action_tags,  # noqa: F401  re-exported; moved to MessageDispatcher
    parse_actions,  # noqa: F401  re-exported; moved to MessageDispatcher
)
from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.permissions import (
    can_kill,  # noqa: F401  re-exported; moved to MessageDispatcher
    can_message,  # noqa: F401  re-exported; moved to MessageDispatcher (patched in test_advisor_mcp)
    can_request_decision,  # noqa: F401  re-exported; moved to MessageDispatcher
    can_spawn_team,  # noqa: F401  re-exported; moved to MessageDispatcher
    cc_targets_for,  # noqa: F401  re-exported; moved to MessageDispatcher
)
from hive.bus.project_store import ProjectStore
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import (
    AUTO_COMPACT_ENABLED,  # noqa: F401  re-exported; MessageDispatcher reads it via this module
    AUTO_COMPACT_THRESHOLD,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_ENABLED,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_FIRST_TURN_ONLY,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_INCLUDE_ATTACHMENTS,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_MAX_DISTANCE,  # noqa: F401  read via this module
    AUTO_RETRIEVE_TOP_K,  # noqa: F401  read via this module
    BOUNCE_FLAP_MAX,  # Ticket 020: auto-bounce knobs, read as module globals + patched in tests
    BOUNCE_FLAP_WINDOW_S,
    BOUNCE_STALL_THRESHOLD,
    BOUNCE_WORKFLOW_WINDOW_S,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.mcp.config import (
    generate_mcp_config,  # noqa: F401  re-exported; MessageDispatcher + LifecycleManager read it via this module
    mcp_servers_enabled,  # noqa: F401  re-exported; MessageDispatcher reads it via this module
)
from hive.models.entity import (
    Entity,
    EntityState,
)
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.notifications import Notification, NotificationDispatcher
from hive.process.approval_handler import ApprovalHandler
from hive.process.lifecycle_manager import (
    LifecycleManager,
    _adapter_config_from_entity,  # noqa: F401  re-exported for `from ...manager import`
    _render_auto_personality,  # noqa: F401  re-exported for `from ...manager import` in tests
)
from hive.process.message_dispatcher import (
    _PARSE_FAILURE_MAX_PER_WINDOW,  # noqa: F401  re-exported for `from ...manager import`
    _PARSE_FAILURE_WINDOW_SECONDS,  # noqa: F401  re-exported for `from ...manager import`
    MessageDispatcher,
)
from hive.process.wake_scheduler import (
    _WAKE_ON_INBOUND_TEXT,  # noqa: F401  re-exported for `from ...manager import` in tests
    WakeScheduler,
)
from hive.process.workflow_watcher import ProgressStore
from hive.process.worktree import WorktreeManager
from hive.runtime.claude_adapter import (
    ClaudeAdapter,  # noqa: F401  re-exported; LifecycleManager reads it via this module
)
from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.quota_monitor import QuotaMonitor
from hive.vault.provider import PaymentProvider

logger = logging.getLogger(__name__)


def _monotonic() -> float:
    """Indirection over ``time.monotonic`` so the flap window is patchable in
    tests without monkeypatching the stdlib clock globally."""
    return time.monotonic()


class ProcessManager:
    """Manages Hive entities and their persistent PTY adapters."""

    def __init__(
        self,
        router: MessageRouter,
        worktree_mgr: WorktreeManager | None = None,
        max_sessions: int = 3,
        entity_store: EntityStore | None = None,
        project_store: ProjectStore | None = None,
        token_store: TokenStore | None = None,
        audit_log: AuditLog | None = None,
        blueprint_store: BlueprintStore | None = None,
        attachment_store: AttachmentStore | None = None,
        mode_request_store: ModeRequestStore | None = None,
        task_store: TaskStore | None = None,
        vault_store: VaultStore | None = None,
        payment_provider: PaymentProvider | None = None,
        vault_daily_cap_cents: int = 0,
        vault_monthly_cap_cents: int = 0,
        vault_cap_currencies: Iterable[str] = ("AUD", "USD"),
        notification_dispatcher: NotificationDispatcher | None = None,
        personalities_dir: Path | None = None,
    ) -> None:
        self.router = router
        self.worktree_mgr = worktree_mgr
        self.max_sessions = max_sessions
        self.entity_store = entity_store
        self.project_store = project_store
        self.token_store = token_store
        self.audit_log = audit_log
        self.blueprint_store = blueprint_store
        self.attachment_store = attachment_store
        self.mode_request_store = mode_request_store
        self.task_store = task_store
        self.vault_store = vault_store
        self.payment_provider = payment_provider
        self.vault_daily_cap_cents = vault_daily_cap_cents
        self.vault_monthly_cap_cents = vault_monthly_cap_cents
        self.vault_cap_currencies = tuple(sorted({c.upper() for c in vault_cap_currencies if c}))
        self.notification_dispatcher = notification_dispatcher
        self.personalities_dir = personalities_dir or Path("personalities")
        self._entities: dict[str, Entity] = {}
        self._adapters: dict[str, ClaudeAdapter] = {}
        # Single asyncio.Lock guards mutations to _entities / _adapters when
        # those mutations need to be consistent. Single-key reads do not
        # acquire this lock — CPython dict get/set on a single key is atomic
        # under the GIL. asyncio.Lock is NOT re-entrant; never hold it across
        # an await that calls back into ProcessManager (deadlock).
        self._state_lock: asyncio.Lock = asyncio.Lock()
        self._last_routed_actions: list[str] = []
        self._last_mode_requests: list[int] = []
        self._last_failure_reports: list[int] = []
        self._last_spawned_teams: list[str] = []
        self._last_killed_entities: list[str] = []
        self._last_vault_requests: list[int] = []
        self._last_kickoffs: list[str] = []
        self._kickoff_tasks: set[asyncio.Task] = set()
        # Wake-on-inbound state: detached tasks are tracked so they
        # aren't GC'd mid-flight, and per-recipient deques hold the
        # rolling window of wake timestamps for rate-limit checks. The
        # router hook itself is opt-in via enable_wake_on_inbound() so
        # tests that seed queues with router.route() aren't disturbed.
        self._wake_tasks: set[asyncio.Task] = set()
        # Detached interactive-gate notification tasks (Ticket 003/008),
        # tracked so they aren't GC'd mid-flight.
        self._gate_tasks: set[asyncio.Task] = set()
        self._wake_budget: dict[str, deque[datetime]] = defaultdict(deque)
        # Per-entity sliding window of parse-failure timestamps. Bounds
        # the feedback->retry loop when a model keeps emitting malformed
        # <hive_actions> blocks; on overflow we escalate to the parent
        # instead of resending feedback.
        self._parse_failure_budget: dict[str, deque[datetime]] = defaultdict(deque)
        self._compacting: set[str] = set()
        # Auto-bounce liveness state (Ticket 020, ADR 0015). Per-entity
        # ``{"stalls": int, "bounces": deque[float]}``: the consecutive
        # genuine-stall count and a monotonic-timestamp window of recent
        # bounces for the flap-guard. MUST live here (not on the adapter) —
        # the bounce destroys and respawns the adapter, so an adapter-held
        # counter would be wiped exactly when the flap-guard must fire.
        self._liveness: dict[str, dict] = {}
        # Set after construction by __main__.py so the dispatch site can
        # consult the rate limiter. Optional — tests construct managers
        # without a scheduler and the spawn dispatch falls back to "allow".
        self.scheduler: object | None = None
        # Set after construction by __main__.py. Optional — tests
        # construct managers without quota monitoring.
        self.quota_monitor: QuotaMonitor | None = None
        # Set after construction. The interactive-gate doorbell registry
        # (Ticket 003). /approve and /deny on a gate row ring it to wake the
        # parked Turn. Optional — tests construct managers without it.
        self.gate_coordinator: GateCoordinator | None = None
        # Set after construction by __main__.py (Ticket 017). The in-memory
        # store of in-flight Workflow runs the WorkflowWatcher fills; the
        # dashboard view-model reads it via process_manager.progress_store.
        # Optional — tests construct managers without the watcher.
        self.progress_store: ProgressStore | None = None

        # Collaborators (Ticket 004): focused objects holding a back-ref to
        # this manager. They reach all shared state via ``self._mgr``; the
        # facade thin-delegates every externally-referenced method to them.
        self.lifecycle = LifecycleManager(self)
        self.approvals = ApprovalHandler(self)
        self.dispatcher = MessageDispatcher(self)
        self.wake = WakeScheduler(self)

    async def _persist(self, entity: Entity) -> None:
        """Persist an entity's current state to the entity store, if configured.

        Called after every state transition the manager drives. Kept at the
        manager level (not inside Entity.transition_to) so the Entity
        dataclass stays sync and DB-free for tests.
        """
        if self.entity_store is None:
            return
        try:
            await self.entity_store.upsert(entity)
        except Exception:
            # Persistence failure should not take down the orchestrator —
            # log and continue. The in-memory roster is still correct.
            logger.exception("Failed to persist entity %s", entity.name)

    async def clear_awaiting_decision(self, entity_name: str) -> None:
        """Unpark an entity waiting on a user decision (Ticket 029, ADR 0018).

        Called from the USER command path when the human replies — never from
        the shared scheduler/peer path — so a peer message can't false-clear the
        flag. Persists the cleared flag so the unpark survives a restart. No-op
        if the entity is unknown or wasn't waiting.
        """
        entity = self._entities.get(entity_name)
        if entity is None or not entity.awaiting_decision:
            return
        entity.awaiting_decision = False
        # #144: disarm the nudge clock so no stray reminder fires after unpark.
        entity.last_nudged_at = None
        await self._persist(entity)

    async def _audit(
        self,
        action: str,
        target: str | None = None,
        details: dict | None = None,
        actor: str = "system",
    ) -> None:
        """Write one audit event, if an audit log is configured.

        Kept separate from ``_persist`` because they track different things:
        ``_persist`` is about the current DB roster row, ``_audit`` is about
        the historical stream of events. The audit log itself handles
        exceptions internally (fire-and-continue), so this helper is just a
        None-guarded convenience wrapper.
        """
        if self.audit_log is None:
            return
        await self.audit_log.record(actor=actor, action=action, target=target, details=details)

    def _peer_directory_for(self, entity_name: str) -> str:
        """Build a 'peers you can message' block for an entity's prompt.

        Lists peers grouped by reach (same-parent direct, cross-parent
        with CC) plus the entity's direct parent for request_decision.
        Returns empty string if the entity is unknown.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            return ""

        same_parent: list[str] = []
        cross_parent: list[str] = []
        parent: str | None = None
        scope_label = ""

        if entity.role == "maestro":
            for name, e in self._entities.items():
                if e.role == "maestro" and name != entity_name:
                    same_parent.append(f"{name} (peer maestro — direct)")
            scope_label = "maestro peer-to-peer"
        elif entity.role == "lead":
            sender_maestro = entity_name.split(".")[0]
            parent = sender_maestro
            for name, e in self._entities.items():
                if e.role != "lead" or name == entity_name:
                    continue
                their_maestro = name.split(".")[0]
                if their_maestro == sender_maestro:
                    same_parent.append(f"{name} (same maestro — direct)")
                else:
                    cross_parent.append(f"{name} (cross-maestro — both maestros CC'd)")
            scope_label = "lead peer-to-peer"

        lines = [f"## Peers you can message ({scope_label})"]
        if same_parent:
            lines.extend(f"- {p}" for p in sorted(same_parent))
        if cross_parent:
            lines.extend(f"- {p}" for p in sorted(cross_parent))
        if not same_parent and not cross_parent:
            lines.append("- (none registered yet)")

        if parent:
            lines.append("")
            lines.append("## Direct parent (use request_decision for escalations)")
            lines.append(f"- {parent}")

        return "\n".join(lines)

    async def _record_usage(self, entity: Entity, usage: dict | None) -> None:
        """Record token usage from a completed turn, if a store is configured.

        Merges the entity's canonical ``model`` into the usage dict before
        handing it to the store. Fire-and-continue: any DB error is logged
        and swallowed, since token bookkeeping must not take down the
        user-facing send path. Skips zero-usage dicts (no tokens charged).
        """
        if self.token_store is None:
            return
        if not usage or not usage.get("input_tokens"):
            return
        try:
            await self.token_store.record(
                entity.name,
                {**usage, "model": entity.model},
            )
        except Exception:
            logger.exception("Failed to record token usage for %s", entity.name)

    @property
    def entities(self) -> dict[str, Entity]:
        return dict(self._entities)

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._adapters.values() if a.is_alive())

    async def register_maestro(
        self,
        name: str,
        model: str = "opus",
        personality_path: Path | None = None,
    ) -> Maestro:
        return await self.lifecycle.register_maestro(name, model, personality_path)

    async def register_entity(self, entity: Entity) -> None:
        return await self.lifecycle.register_entity(entity)

    async def _get_or_create_adapter(self, entity: Entity) -> ClaudeAdapter:
        return await self.lifecycle._get_or_create_adapter(entity)

    # -----------------------------------------------------------------
    # Outbound sends + inbound action routing (Ticket 004 — MessageDispatcher)
    # -----------------------------------------------------------------

    def is_parked_at_gate(self, entity_name: str) -> bool:
        """True while a Turn is parked on an interactive gate for this entity.

        The coordinator-owned source of truth (Ticket 028):
        ``pending_request_id`` is non-None exactly between gate-park and
        gate-resume, for every gate kind (plan / ask / permission). Senders
        consult this before injecting into the PTY — a poke typed at a parked
        TUI menu submits the highlighted default (the gate's "answer"), so any
        new-turn injection must be skipped while parked. Gate *answers* take a
        separate, menu-aware path (``ring`` → ``resolve`` → inject keys) and are
        unaffected. Single seam for a future ``waitingFor`` fallback.
        """
        gc = self.gate_coordinator
        return gc is not None and gc.pending_request_id(entity_name) is not None

    # -----------------------------------------------------------------
    # Auto-bounce jammed PTY sessions (Ticket 020, ADR 0015)
    # -----------------------------------------------------------------

    def _liveness_entry(self, name: str) -> dict:
        """Per-entity liveness record, created on first access."""
        return self._liveness.setdefault(name, {"stalls": 0, "bounces": deque()})

    def _note_turn_success(self, entity_name: str) -> None:
        """Reset the consecutive-stall count after any turn that completed.

        A successful turn means the session is healthy again, so a later
        isolated stall starts from zero rather than inheriting an old partial
        count (Ticket 020 §D4).
        """
        entry = self._liveness.get(entity_name)
        if entry is not None:
            entry["stalls"] = 0

    async def _maybe_bounce_on_timeout(self, entity: Entity, adapter: ClaudeAdapter) -> bool:
        """Decide what to do when a turn raised ``TimeoutError`` (Ticket 020 §D1).

        Returns ``True`` when the jammed session was bounced and the caller
        should retry the send once on the fresh adapter; ``False`` when the
        timeout should propagate unchanged — because the entity is legitimately
        waiting (a safety check held off), the stall is still below threshold,
        or the flap-guard gave up.

        The safety checks are re-run HERE, at decision time, not inferred from
        the fact that a timeout happened — so 020 stays correct even if an
        upstream liveness reset is imperfect. Each is a reason to hold off:
          1. parked at a plan/ask gate — a permission-prompt jam is undetectable
             as a gate (ADR 0005), so this is False for exactly the jam to kill;
          2. a Workflow run advanced within the liveness window (030's
             false-timeout class);
          3. parked on a user decision (029 defense-in-depth — cannot currently
             overlap an in-flight send, but cheap insurance).
        A held-off timeout does NOT count as a stall.
        """
        name = entity.name
        if self.is_parked_at_gate(name):
            logger.info("liveness: %s timed out while gated — holding off bounce", name)
            return False
        try:
            if adapter.workflow_active(BOUNCE_WORKFLOW_WINDOW_S):
                logger.info("liveness: %s timed out with a live Workflow — holding off", name)
                return False
        except Exception:
            logger.exception("liveness: workflow_active probe failed for %s", name)
        if getattr(entity, "awaiting_decision", False):
            logger.info("liveness: %s timed out awaiting a user decision — holding off", name)
            return False

        entry = self._liveness_entry(name)
        entry["stalls"] += 1
        if entry["stalls"] < BOUNCE_STALL_THRESHOLD:
            logger.warning(
                "liveness: %s stalled (%d/%d consecutive) — not bouncing yet",
                name,
                entry["stalls"],
                BOUNCE_STALL_THRESHOLD,
            )
            return False

        reason = self._bounce_reason(entity, adapter)
        return await self._bounce_adapter(entity, reason)

    async def _bounce_adapter(self, entity: Entity, reason: str) -> bool:
        """Kill + respawn a jammed session (conversation preserved), or give up.

        Flap-guarded (Ticket 020 §D3): if ``BOUNCE_FLAP_MAX`` bounces already
        landed inside ``BOUNCE_FLAP_WINDOW_S``, stop bouncing — move the entity
        to ERROR and escalate to the user instead of flapping. Otherwise stop
        the adapter, drop it from the cache, and re-provision through the normal
        lazy path (which adds ``--continue`` when a prior transcript exists, so
        the conversation resumes). Returns True when bounced, False on give-up.
        """
        name = entity.name
        entry = self._liveness_entry(name)
        bounces: deque = entry["bounces"]
        now = _monotonic()
        cutoff = now - BOUNCE_FLAP_WINDOW_S
        while bounces and bounces[0] < cutoff:
            bounces.popleft()

        if len(bounces) >= BOUNCE_FLAP_MAX:
            await self._bounce_give_up(entity, reason, len(bounces))
            return False

        adapter = self._adapters.pop(name, None)
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("liveness: failed to stop adapter for %s on bounce", name)
        try:
            await self._get_or_create_adapter(entity)
        except Exception:
            logger.exception("liveness: respawn failed for %s after bounce", name)
            return False

        bounces.append(now)
        entry["stalls"] = 0
        logger.warning("Auto-bounced %s — %s (bounce %d in window)", name, reason, len(bounces))
        await self._notify(
            f"Auto-bounced {name} — {reason}. Conversation resumed.",
            kind="auto_bounce",
            data={"entity": name},
        )
        await self._audit(
            "entity.bounce",
            target=name,
            details={"reason": reason, "bounces_in_window": len(bounces)},
        )
        return True

    async def _bounce_give_up(self, entity: Entity, reason: str, bounce_count: int) -> None:
        """Stop bouncing a session that keeps jamming; surface it for a human.

        Stops the adapter without respawning, moves the entity to ERROR so it
        reads as visibly broken (not silently dead), and escalates with the
        best-effort reason. Recovery is now a human action (Ticket 020 §D3).
        """
        name = entity.name
        adapter = self._adapters.pop(name, None)
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("liveness: failed to stop adapter for %s on give-up", name)
        if entity.state in (EntityState.RUNNING, EntityState.GATED):
            try:
                entity.transition_to(EntityState.ERROR)
                await self._persist(entity)
            except Exception:
                logger.exception("liveness: could not move %s to ERROR on give-up", name)
        minutes = int(BOUNCE_FLAP_WINDOW_S // 60)
        logger.error(
            "Gave up on %s — %d bounces in %dm, still stuck (%s)",
            name,
            bounce_count,
            minutes,
            reason,
        )
        await self._notify(
            f"Gave up on {name} — {bounce_count} bounces in {minutes} min, {reason}. Needs you.",
            kind="auto_bounce_failed",
            data={"entity": name},
        )
        await self._audit(
            "entity.bounce_failed",
            target=name,
            details={"reason": reason, "bounces": bounce_count},
        )

    def _bounce_reason(self, entity: Entity, adapter: ClaudeAdapter) -> str:
        """Best-effort human reason for a bounce — ADVISORY only (Ticket 020 §D5).

        First hit wins: the session-state ``waitingFor``/``status`` (the
        permission-jam signal) → a dead process → "cause unknown". NEVER raises
        and NEVER feeds the bounce DECISION (that is the safety-check logic in
        ``_maybe_bounce_on_timeout``); this only enriches the notification, so a
        dropped/renamed CC field degrades to "cause unknown", not a failure.
        """
        try:
            state = adapter.describe_jam() if hasattr(adapter, "describe_jam") else None
        except Exception:
            state = None
        if isinstance(state, dict):
            waiting_for = state.get("waitingFor")
            if waiting_for:
                return f"waiting for {waiting_for}"
            status = state.get("status")
            if status:
                return f"session status: {status}"
        try:
            if not adapter.is_alive():
                return "the Claude process is no longer alive"
        except Exception:
            pass
        return "no output past the no-progress timeout — cause unknown"

    async def send_to_entity(self, entity_name: str, prompt: str) -> str:
        return await self.dispatcher.send_to_entity(entity_name, prompt)

    async def _handle_actions(
        self,
        entity_name: str,
        clean_text: str,
        actions: list[Action],
        *,
        parse_errors: list[str] | None = None,
    ) -> str:
        return await self.dispatcher._handle_actions(
            entity_name, clean_text, actions, parse_errors=parse_errors
        )

    async def _handle_parse_errors(self, entity: Entity, parse_errors: list[str]) -> None:
        return await self.dispatcher._handle_parse_errors(entity, parse_errors)

    def _parent_of(self, entity: Entity) -> str | None:
        """Return the entity's direct parent for escalation, or None.

        Leads escalate to their maestro. Maestros have no Hive parent —
        callers escalate to ``user`` via the notification dispatcher instead.
        """
        if isinstance(entity, TeamLead):
            return entity.maestro_name or None
        return None

    # -----------------------------------------------------------------
    # Wake-on-inbound + spawn-kickoff (Ticket 004 — WakeScheduler)
    # -----------------------------------------------------------------

    async def _auto_kickoff(self, target: str) -> None:
        return await self.wake._auto_kickoff(target)

    def enable_wake_on_inbound(self) -> None:
        return self.wake.enable_wake_on_inbound()

    async def create_team(
        self,
        maestro_name: str,
        team_name: str,
        model: str = "opus",
        display_name: str | None = None,
        personality: str | None = None,
    ) -> TeamLead:
        return await self.lifecycle.create_team(
            maestro_name, team_name, model, display_name, personality
        )

    async def kill_team(self, maestro_name: str, team_name: str) -> None:
        return await self.lifecycle.kill_team(maestro_name, team_name)

    async def kill_entity(self, name: str) -> None:
        return await self.lifecycle.kill_entity(name)

    async def kill_all(self) -> None:
        return await self.lifecycle.kill_all()

    async def stop_all(self) -> None:
        return await self.lifecycle.stop_all()

    # -----------------------------------------------------------------
    # Mode-change approval flow (Sprint 12 Phase 2C)
    # -----------------------------------------------------------------

    def _approver_for(self, entity: Entity) -> str:
        return self.approvals._approver_for(entity)

    async def request_mode_change(
        self,
        requester: str,
        requested_mode: str,
        reason: str | None = None,
    ) -> int:
        return await self.approvals.request_mode_change(requester, requested_mode, reason)

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
        return await self.approvals.request_payment(
            requester,
            amount_cents=amount_cents,
            currency=currency,
            recipient=recipient,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    async def approve_vault_action(self, action_id: int) -> dict | None:
        return await self.approvals.approve_vault_action(action_id)

    async def deny_vault_action(self, action_id: int, reason: str | None = None) -> dict | None:
        return await self.approvals.deny_vault_action(action_id, reason)

    async def approve_mode_request(self, request_id: int) -> dict | None:
        return await self.approvals.approve_mode_request(request_id)

    async def deny_mode_request(self, request_id: int, reason: str | None = None) -> dict | None:
        return await self.approvals.deny_mode_request(request_id, reason)

    def _on_gate_state(self, entity_name: str, state: str) -> None:
        return self.approvals._on_gate_state(entity_name, state)

    async def _notify_gate_waiting(self, entity_name: str) -> None:
        return await self.approvals._notify_gate_waiting(entity_name)

    async def _gate_nudge(self, entity_name: str, request_id: int) -> None:
        return await self.approvals._gate_nudge(entity_name, request_id)

    async def approve_gate(self, request_id: int, chosen_option: int | None = None) -> dict | None:
        return await self.approvals.approve_gate(request_id, chosen_option)

    async def deny_gate(self, request_id: int, reason: str | None = None) -> dict | None:
        return await self.approvals.deny_gate(request_id, reason)

    async def reconcile_orphaned_gates(self, approver: str = "user") -> list[dict]:
        return await self.approvals.reconcile_orphaned_gates(approver)

    async def expire_old_mode_requests(self, cutoff: datetime) -> list[dict]:
        return await self.approvals.expire_old_mode_requests(cutoff)

    # -----------------------------------------------------------------
    # Auto-recovery on task failures (Sprint 12 Phase 4)
    # -----------------------------------------------------------------

    def _escalation_target_for(self, entity_name: str) -> str:
        return self.approvals._escalation_target_for(entity_name)

    async def handle_task_failure(self, task_id: int, error: str) -> None:
        return await self.approvals.handle_task_failure(task_id, error)

    def get_status(self) -> list[dict]:
        """Return status of all tracked entities."""
        statuses = []
        # Snapshot via list() — sync method can't use the async _state_lock,
        # but a snapshot prevents "dictionary changed size during iteration"
        # if another coroutine mutates _entities while we iterate.
        for name, entity in list(self._entities.items()):
            adapter = self._adapters.get(name)
            statuses.append(
                {
                    "name": name,
                    "role": entity.role,
                    "state": entity.state.value,
                    "model": entity.model,
                    "pid": entity.pid,
                    "alive": adapter.is_alive() if adapter else False,
                    "uptime": entity.uptime_seconds,
                }
            )
        return statuses

    async def health_check(self) -> list[str]:
        """Check which adapters are dead but entities think they're running.

        Returns list of entity names that need attention.
        """
        unhealthy: list[str] = []
        # Snapshot to avoid iteration-during-mutation. Don't hold the lock
        # across the awaits below.
        async with self._state_lock:
            entries = list(self._entities.items())
        for name, entity in entries:
            adapter = self._adapters.get(name)
            if entity.state == EntityState.RUNNING and (adapter is None or not adapter.is_alive()):
                unhealthy.append(name)
                entity.transition_to(EntityState.ERROR)
                await self._persist(entity)
                await self._audit(
                    "entity.error",
                    target=name,
                    details={"phase": "health"},
                )
                logger.warning("Entity %s died unexpectedly", name)
        return unhealthy

    async def _notify(
        self,
        message: str,
        kind: str = "info",
        data: dict | None = None,
    ) -> None:
        """Send a proactive notification through the registered dispatcher."""
        if self.notification_dispatcher is None:
            return
        await self.notification_dispatcher.dispatch(
            Notification(text=message, kind=kind, data=data)
        )

    async def compact_entity(self, entity_name: str) -> str:
        return await self.lifecycle.compact_entity(entity_name)

    async def kill_idle_entities(
        self,
        timeout_minutes: int,
        exempt_names: set[str] | None = None,
    ) -> list[str]:
        return await self.lifecycle.kill_idle_entities(timeout_minutes, exempt_names)

    async def reconcile_worktrees(self) -> dict[str, list[str]]:
        return await self.lifecycle.reconcile_worktrees()

    def restore(self, entity: Entity) -> None:
        """Re-register a persisted entity on orchestrator startup.

        Structural restoration only — no subprocess is spawned. The entity
        comes back in IDLE state (forced by EntityStore on load) so the next
        spawn goes through the normal IDLE -> STARTING -> RUNNING path. We
        can't reattach to the old PID because the subprocess died with the
        previous orchestrator.
        """
        self._entities[entity.name] = entity
        self.router.register(entity.name)
        logger.info(
            "Restored entity %s (role=%s, model=%s)",
            entity.name,
            entity.role,
            entity.model,
        )

    def rebuild_hierarchy(self) -> None:
        """Reconstruct Maestro.teams from restored TeamLead entities.

        Called once after all entities are restored from the DB. Links each
        TeamLead to its parent Maestro's teams dict.
        """
        from hive.models.team import Team

        for entity in self._entities.values():
            if isinstance(entity, TeamLead) and entity.maestro_name:
                maestro = self._entities.get(entity.maestro_name)
                if isinstance(maestro, Maestro) and entity.team_name not in maestro.teams:
                    team = Team(
                        name=entity.team_name,
                        maestro=maestro.name,
                        lead=entity.name,
                    )
                    maestro.teams[entity.team_name] = team

        logger.info("Rebuilt hierarchy for %d entities", len(self._entities))
