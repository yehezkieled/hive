"""Process manager — spawns, tracks, and kills Claude Code agent subprocesses."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
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
    can_spawn_worker,  # noqa: F401  re-exported; moved to MessageDispatcher
    cc_targets_for,  # noqa: F401  re-exported; moved to MessageDispatcher
)
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import (
    ADVISOR_ENABLED,
    AUTO_COMPACT_ENABLED,  # noqa: F401  re-exported; MessageDispatcher reads it via this module
    AUTO_COMPACT_THRESHOLD,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_ENABLED,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_FIRST_TURN_ONLY,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_INCLUDE_ATTACHMENTS,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_MAX_DISTANCE,  # noqa: F401  re-exported; read via this module
    AUTO_RETRIEVE_TOP_K,  # noqa: F401  re-exported; read via this module
    DEFAULT_MAESTRO,
    HIVE_USE_PTY,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.mcp.config import (
    generate_mcp_config,  # noqa: F401  re-exported; MessageDispatcher reads it via this module
)
from hive.models.entity import (
    Entity,
    EntityState,
    is_auto_generated_personality,
)
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import Worker
from hive.notifications import Notification, NotificationDispatcher
from hive.process.approval_handler import ApprovalHandler
from hive.process.claude_session import ClaudeSession
from hive.process.message_dispatcher import (
    _PARSE_FAILURE_MAX_PER_WINDOW,  # noqa: F401  re-exported for `from ...manager import`
    _PARSE_FAILURE_WINDOW_SECONDS,  # noqa: F401  re-exported for `from ...manager import`
    MessageDispatcher,
)
from hive.process.wake_scheduler import (
    _WAKE_ON_INBOUND_TEXT,  # noqa: F401  re-exported for `from ...manager import` in tests
    WakeScheduler,
)
from hive.process.worktree import WorktreeManager
from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig
from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.quota_monitor import QuotaMonitor
from hive.vault.provider import PaymentProvider

logger = logging.getLogger(__name__)


def _render_auto_personality(
    *,
    entity_name: str,
    role: str,
    model: str,
    display_name: str,
    personality: str,
) -> str:
    """Render the markdown body for an auto-generated personality file.

    Frontmatter ``auto_generated: true`` is the cleanup signal — only
    files with this flag are deleted on kill. User-authored files (no
    frontmatter) are always preserved.

    Maestros and leads default to read-only tools (Read, Grep, Glob)
    so the role boundary holds — they cannot drift into hands-on
    coding because Edit/Write/Bash aren't available. Workers and other
    roles inherit the platform default toolkit.

    The ``disallowedTools`` line blocks Claude Code's internal
    subagent tools (``Agent``/``Task``) and TodoWrite-family tools for
    coordinator roles. ``allowedTools`` alone is bypassed when the
    entity runs under ``--dangerously-skip-permissions`` (yolo mode);
    ``disallowedTools`` is still honored. Without this guard, a yolo
    lead spawns Claude Code's own subagents instead of Hive workers
    and the org never grows.
    """
    tools_section = ""
    if role in ("maestro", "lead"):
        tools_section = (
            "\n## Tools\n"
            "- allowedTools: Read Grep Glob\n"
            "- disallowedTools: Agent Task ExitPlanMode TodoWrite TaskCreate "
            "TaskUpdate TaskList TaskGet TaskOutput TaskStop\n"
        )
    knowledge_section = (
        "\n## Knowledge search\n"
        "You have a `search_knowledge(query, kind, limit)` MCP tool "
        "(via `hive-knowledge`).\n\n"
        "Call it when:\n"
        "- The auto-context above didn't include what you need\n"
        "- You're mid-task and realise different keywords might match better\n"
        "- You need more than the 1 result the auto-context gave you\n\n"
        "Tips:\n"
        "- Phrase the query like keywords, not a sentence "
        '("rate limit handling" not "how do I handle rate limits?")\n'
        '- `kind="blueprints"` for design notes; `kind="attachments"` for '
        'uploaded files; `kind="both"` if unsure\n'
        "- Distances < 0.3 are usually solid matches; > 0.6 is noise\n"
    )
    return (
        "---\n"
        "auto_generated: true\n"
        "---\n"
        f"# Entity: {display_name}\n\n"
        "## Identity\n"
        f"- **Name**: {entity_name}\n"
        f"- **Role**: {role}\n"
        f"- **Model**: {model}\n\n"
        "## System Prompt\n"
        f"You are {display_name}.\n\n"
        f"{personality}\n"
        f"{tools_section}"
        f"{knowledge_section}"
    )


def _adapter_config_from_entity(entity: Entity) -> ClaudeAdapterConfig:
    """Map an Entity to the ClaudeAdapterConfig needed by ClaudeAdapter."""
    return ClaudeAdapterConfig(
        model=entity.model,
        system_prompt=entity.system_prompt,
        allowed_tools=list(entity.allowed_tools),
        disallowed_tools=list(entity.disallowed_tools),
        permission_mode=entity.permission_mode,
        loop_mode=entity.loop_mode,
        role=entity.role,
        name=entity.name,
        mcp_config_path=Path(entity.mcp_config_path) if ADVISOR_ENABLED else None,
    )


class ProcessManager:
    """Manages all Claude Code subprocesses for Hive entities."""

    def __init__(
        self,
        router: MessageRouter,
        worktree_mgr: WorktreeManager | None = None,
        max_sessions: int = 3,
        entity_store: EntityStore | None = None,
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
        self._sessions: dict[str, ClaudeSession] = {}
        self._adapters: dict[str, ClaudeAdapter] = {}
        # Single asyncio.Lock guards mutations to _entities / _sessions when
        # those mutations need to be consistent (e.g. entity + session
        # registered together on spawn). Single-key reads do not acquire
        # this lock — CPython dict get/set on a single key is atomic under
        # the GIL. asyncio.Lock is NOT re-entrant; never hold it across an
        # await that calls back into ProcessManager (deadlock).
        self._state_lock: asyncio.Lock = asyncio.Lock()
        self._last_routed_actions: list[str] = []
        self._last_mode_requests: list[int] = []
        self._last_failure_reports: list[int] = []
        self._last_spawned_teams: list[str] = []
        self._last_spawned_workers: list[str] = []
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
        self._wake_budget: dict[str, deque[datetime]] = defaultdict(deque)
        # Per-entity sliding window of parse-failure timestamps. Bounds
        # the feedback->retry loop when a model keeps emitting malformed
        # <hive_actions> blocks; on overflow we escalate to the parent
        # instead of resending feedback.
        self._parse_failure_budget: dict[str, deque[datetime]] = defaultdict(deque)
        self._compacting: set[str] = set()
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

        # Collaborators (Ticket 004): focused objects holding a back-ref to
        # this manager. They reach all shared state via ``self._mgr``; the
        # facade thin-delegates every externally-referenced method to them.
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
        elif entity.role == "worker":
            sender_lead = ".".join(entity_name.split(".")[:-1])
            sender_maestro = entity_name.split(".")[0]
            parent = sender_lead
            for name, e in self._entities.items():
                if e.role != "worker" or name == entity_name:
                    continue
                their_lead = ".".join(name.split(".")[:-1])
                their_maestro = name.split(".")[0]
                if their_lead == sender_lead:
                    same_parent.append(f"{name} (same team — direct)")
                elif their_maestro == sender_maestro:
                    cross_parent.append(f"{name} (cross-team — both leads CC'd)")
            scope_label = "worker peer-to-peer"

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

    def _personality_path(self, entity_name: str) -> Path:
        return self.personalities_dir / f"{entity_name}.md"

    def _maybe_write_auto_personality(
        self,
        *,
        entity_name: str,
        role: str,
        model: str,
        display_name: str | None,
        personality: str | None,
    ) -> Path | None:
        """Write an auto-generated personality file when both fields present.

        Pair-or-nothing: missing either field skips the write entirely.
        Existing files are never overwritten — user-authored files are
        protected, and re-spawning under the same name is a no-op.

        Returns the path that was written, or ``None`` if no file was
        created (pair incomplete, file already existed, or write failed).
        """
        if not display_name or not personality:
            return None
        path = self._personality_path(entity_name)
        if path.exists():
            logger.info("Skipping auto personality write — file exists at %s", path)
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _render_auto_personality(
                    entity_name=entity_name,
                    role=role,
                    model=model,
                    display_name=display_name,
                    personality=personality,
                )
            )
            logger.info("Wrote auto personality file: %s", path)
            return path
        except OSError:
            logger.exception("Failed to write personality file %s", path)
            return None

    def _maybe_delete_auto_personality(self, entity_name: str) -> None:
        """Delete the personality file if it exists and is auto-generated."""
        path = self._personality_path(entity_name)
        if not is_auto_generated_personality(path):
            return
        try:
            path.unlink()
            logger.info("Deleted auto personality file: %s", path)
        except OSError:
            logger.exception("Failed to delete personality file %s", path)

    @property
    def entities(self) -> dict[str, Entity]:
        return dict(self._entities)

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_alive)

    async def _preempt_for_priority(self, priority: int) -> str | None:
        """Try to free a session slot by killing the lowest-priority running entity.

        Returns the name of the killed entity, or None if no preemption is
        possible (under capacity, or no RUNNING entity is strictly worse
        than the requested priority). The default maestro is never
        preempted — it's the org's root and killing it would break every
        downstream entity. Only RUNNING entities are considered because
        IDLE entities don't hold a session slot.
        """
        if self.active_count < self.max_sessions:
            return None

        worst_name: str | None = None
        worst_priority = -1
        for name, entity in self._entities.items():
            if name == DEFAULT_MAESTRO:
                continue
            if entity.state == EntityState.RUNNING and entity.current_priority > worst_priority:
                worst_priority = entity.current_priority
                worst_name = name

        if worst_name is None or worst_priority <= priority:
            return None

        await self.kill_entity(worst_name)
        await self._audit(
            "entity.kill",
            target=worst_name,
            details={"reason": "preempt", "preempted_priority": worst_priority},
            actor="system",
        )
        return worst_name

    async def register_maestro(
        self,
        name: str,
        model: str = "opus",
        personality_path: Path | None = None,
    ) -> Maestro:
        """Create and register a new maestro entity.

        Does not spawn a subprocess — the maestro stays IDLE until it
        receives its first message via send_to_entity.
        """
        if name in self._entities:
            raise ValueError(f"Entity {name!r} already exists.")

        maestro = Maestro(
            name=name,
            model=model,
            personality_path=personality_path,
        )
        # New maestros default to `yolo` so first-message tool calls
        # don't get auto-denied under headless `claude -p`. Existing
        # maestros restored from postgres keep their persisted mode.
        maestro.permission_mode = "yolo"
        if personality_path and personality_path.exists():
            maestro.load_personality()

        async with self._state_lock:
            self._entities[name] = maestro
        self.router.register(name)
        await self._persist(maestro)
        await self._audit(
            "entity.register",
            target=name,
            details={"role": "maestro", "model": model},
        )
        logger.info("Registered maestro: %s (model=%s)", name, model)
        return maestro

    async def register_entity(self, entity: Entity) -> None:
        """Register a pre-built entity in IDLE state without spawning a subprocess.

        Useful for tests and for restoring entities that were constructed
        externally. The entity must not already be registered.
        """
        if entity.name in self._entities:
            raise ValueError(f"Entity {entity.name!r} already exists.")
        async with self._state_lock:
            self._entities[entity.name] = entity
        self.router.register(entity.name)
        logger.info("Registered entity: %s (role=%s)", entity.name, entity.role)

    async def spawn_entity(self, entity: Entity, cwd: Path | None = None) -> ClaudeSession:
        """Spawn a Claude Code subprocess for an entity.

        Loads personality, builds CLI args, creates session, and starts it.
        Preemption is the last-resort safety net — the maestro is the
        primary capacity manager via the priority scheduler. When at cap
        and HIVE_PRIORITY_PREEMPT_ENABLED, try to free a slot by killing
        the lowest-priority RUNNING entity worse than this one.
        """
        if self.active_count >= self.max_sessions:
            from hive.config import PRIORITY_PREEMPT_ENABLED

            preempted: str | None = None
            if PRIORITY_PREEMPT_ENABLED:
                preempted = await self._preempt_for_priority(entity.current_priority)
            if preempted is None:
                raise RuntimeError(
                    f"Max concurrent sessions ({self.max_sessions}) reached. Kill an entity first."
                )
            logger.info(
                "Preempted %s (p%s) to free a slot for %s (p%s)",
                preempted,
                "?",
                entity.name,
                entity.current_priority,
            )

        if entity.name in self._sessions and self._sessions[entity.name].is_alive:
            raise RuntimeError(f"Entity {entity.name!r} is already running.")

        # Load personality if available
        entity.load_personality()

        # Write per-entity MCP config so Claude Code can connect to the advisor server
        if ADVISOR_ENABLED:
            generate_mcp_config(entity.name, entity.mcp_config_path)

        # Build CLI args
        args = entity.build_cli_args()

        # Transition state
        entity.transition_to(EntityState.STARTING)
        await self._persist(entity)

        # Create and start session
        session = ClaudeSession(args=args, cwd=cwd)
        try:
            await session.start()
            entity.pid = session.pid
            entity.transition_to(EntityState.RUNNING)
        except Exception as exc:
            entity.transition_to(EntityState.ERROR)
            await self._persist(entity)
            await self._audit(
                "entity.error",
                target=entity.name,
                details={"phase": "spawn", "error": str(exc)},
            )
            raise

        # Register in router for message delivery
        self.router.register(entity.name)

        # Track — entity + session must appear together so callers don't
        # observe an entity in RUNNING state without its session.
        async with self._state_lock:
            self._entities[entity.name] = entity
            self._sessions[entity.name] = session

        await self._persist(entity)
        await self._audit(
            "entity.spawn",
            target=entity.name,
            details={"role": entity.role, "model": entity.model, "pid": entity.pid},
        )

        logger.info(
            "Spawned entity %s (role=%s, model=%s, pid=%s)",
            entity.name,
            entity.role,
            entity.model,
            entity.pid,
        )
        return session

    async def _get_or_create_adapter(self, entity: Entity) -> ClaudeAdapter:
        """Return a live adapter for entity, creating one if needed.

        In PTY mode (HIVE_USE_PTY=True) adapters are cached per entity so
        the same persistent PTY process handles all turns. In subprocess mode
        a fresh adapter is built per call (stateless, backward-compatible).
        """
        if HIVE_USE_PTY:
            existing = self._adapters.get(entity.name)
            if existing is not None and existing.is_alive():
                return existing

        cwd = (
            Path(entity.worktree_path)
            if isinstance(entity, Worker) and entity.worktree_path
            else None
        )
        config = _adapter_config_from_entity(entity)
        adapter = ClaudeAdapter(
            config,
            cwd=cwd,
            session_factory=lambda args, c: ClaudeSession(args=args, cwd=c),
            initial_session_id=entity.session_id if not HIVE_USE_PTY else None,
            use_pty=HIVE_USE_PTY,
            gate_coordinator=self.gate_coordinator,
            entity_name=entity.name,
            on_gate_state=self._on_gate_state,
        )
        await adapter.start()
        if HIVE_USE_PTY:
            async with self._state_lock:
                self._adapters[entity.name] = adapter
        return adapter

    # -----------------------------------------------------------------
    # Outbound sends + inbound action routing (Ticket 004 — MessageDispatcher)
    # -----------------------------------------------------------------

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

        Workers escalate to their lead, leads to their maestro. Maestros
        have no Hive parent — callers escalate to ``user`` via the
        notification dispatcher instead.
        """
        from hive.models.team_lead import TeamLead
        from hive.models.worker import Worker

        if isinstance(entity, Worker):
            return entity.lead_name or None
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
        model: str = "sonnet",
        display_name: str | None = None,
        personality: str | None = None,
    ) -> TeamLead:
        """Create a new team under a maestro.

        Registers a TeamLead entity named ``maestro.team``. The lead is
        not spawned as a subprocess — it stays IDLE until someone sends
        it a message via send_to_entity.

        If both ``display_name`` and ``personality`` are provided and no
        file exists at the target path, an auto-generated personality
        file is written. Pair-or-nothing: either both fields or neither.
        """
        entity = self._entities.get(maestro_name)
        if entity is None:
            raise KeyError(f"Maestro {maestro_name!r} not found.")
        if not isinstance(entity, Maestro):
            raise TypeError(f"Entity {maestro_name!r} is not a maestro.")

        # Delegate to Maestro.create_team (raises ValueError on duplicate)
        team = entity.create_team(team_name)

        lead_name = f"{maestro_name}.{team_name}"
        lead = TeamLead(
            name=lead_name,
            team_name=team_name,
            maestro_name=maestro_name,
            model=model,
            permission_mode=entity.permission_mode,
        )
        team.lead = lead_name

        async with self._state_lock:
            self._entities[lead_name] = lead
        self.router.register(lead_name)
        written_path = self._maybe_write_auto_personality(
            entity_name=lead_name,
            role="lead",
            model=model,
            display_name=display_name,
            personality=personality,
        )
        if written_path is not None:
            lead.personality_path = written_path
            lead.load_personality()
        await self._persist(lead)
        await self._audit(
            "entity.create_team",
            target=lead_name,
            details={"maestro": maestro_name, "team": team_name},
        )
        logger.info("Created team %s under maestro %s", team_name, maestro_name)
        return lead

    async def spawn_worker(
        self,
        lead_name: str,
        worker_name: str | None = None,
        task_id: int | None = None,
        display_name: str | None = None,
        personality: str | None = None,
    ) -> Worker:
        """Spawn a worker under a team lead.

        If worker_name is None, auto-generates ``w1``, ``w2``, etc.
        The worker is registered but not spawned as a subprocess — it
        stays IDLE until work is assigned via send_to_entity.

        If both ``display_name`` and ``personality`` are provided and no
        file exists at the target path, an auto-generated personality
        file is written. Pair-or-nothing: either both fields or neither.
        """
        lead = self._entities.get(lead_name)
        if lead is None:
            raise KeyError(f"Lead {lead_name!r} not found.")
        if not isinstance(lead, TeamLead):
            raise TypeError(f"Entity {lead_name!r} is not a team lead.")

        if len(lead.workers) >= lead.max_workers:
            raise RuntimeError(
                f"Lead {lead_name!r} already has "
                f"{len(lead.workers)}/{lead.max_workers} workers (max)."
            )

        if worker_name is None:
            # Auto-name: find the next available w<N>
            existing_nums = []
            for wname in lead.workers:
                suffix = wname.rsplit(".", 1)[-1]
                if suffix.startswith("w") and suffix[1:].isdigit():
                    existing_nums.append(int(suffix[1:]))
            n = max(existing_nums, default=0) + 1
            worker_name = f"w{n}"

        full_name = f"{lead_name}.{worker_name}"

        # Create worktree for isolated work if a WorktreeManager is configured
        worktree_path = None
        if self.worktree_mgr:
            worktree_path = await self.worktree_mgr.create(full_name, branch=f"hive/{full_name}")

        worker = Worker(
            name=full_name,
            team_name=lead.team_name,
            lead_name=lead_name,
            model=lead.model,
            permission_mode=lead.permission_mode,
            task_id=task_id,
            worktree_path=worktree_path,
        )

        lead.workers.append(full_name)

        # Update the team's worker list in the parent maestro
        maestro = self._entities.get(lead.maestro_name)
        if isinstance(maestro, Maestro):
            team = maestro.get_team(lead.team_name)
            if team:
                team.workers.append(full_name)

        async with self._state_lock:
            self._entities[full_name] = worker
        self.router.register(full_name)
        written_path = self._maybe_write_auto_personality(
            entity_name=full_name,
            role="worker",
            model=lead.model,
            display_name=display_name,
            personality=personality,
        )
        if written_path is not None:
            worker.personality_path = written_path
            worker.load_personality()
        await self._persist(worker)
        await self._audit(
            "entity.spawn_worker",
            target=full_name,
            details={"lead": lead_name, "team": lead.team_name, "task_id": task_id},
        )
        logger.info("Spawned worker %s under lead %s", full_name, lead_name)
        return worker

    async def kill_team(self, maestro_name: str, team_name: str) -> None:
        """Kill a team — removes the lead and all its workers."""
        maestro = self._entities.get(maestro_name)
        if not isinstance(maestro, Maestro):
            return

        team = maestro.get_team(team_name)
        if team is None:
            return

        # Kill workers first, then the lead
        for worker_name in list(team.workers):
            await self.kill_entity(worker_name)
        if team.lead:
            await self.kill_entity(team.lead)

        maestro.remove_team(team_name)
        logger.info("Killed team %s under maestro %s", team_name, maestro_name)

    async def kill_entity(self, name: str) -> None:
        """Kill an entity's subprocess and clean up.

        If a personality file exists for this entity and was auto-generated
        (frontmatter ``auto_generated: true``), it is deleted. User-authored
        files are always preserved.
        """
        self._maybe_delete_auto_personality(name)

        session = self._sessions.get(name)
        if session:
            await session.kill()
            async with self._state_lock:
                self._sessions.pop(name, None)

        adapter = self._adapters.pop(name, None)
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Failed to stop adapter for %s on kill", name)

        entity = self._entities.get(name)
        if entity:
            # Clean up worktree for workers
            if isinstance(entity, Worker) and entity.worktree_path and self.worktree_mgr:
                try:
                    await self.worktree_mgr.remove(name)
                except Exception:
                    logger.exception("Failed to remove worktree for %s", name)

            # Remove worker from parent lead's and team's worker lists
            if isinstance(entity, Worker) and entity.lead_name:
                lead = self._entities.get(entity.lead_name)
                if isinstance(lead, TeamLead) and name in lead.workers:
                    lead.workers.remove(name)
                # Also remove from the Team object on the maestro
                maestro_name = lead.maestro_name if isinstance(lead, TeamLead) else ""
                maestro = self._entities.get(maestro_name)
                if isinstance(maestro, Maestro):
                    team = maestro.get_team(entity.team_name)
                    if team and name in team.workers:
                        team.workers.remove(name)

            # When killing a lead, also drop the Team object on the maestro
            # so the team name can be reused. kill_team() already calls
            # maestro.remove_team — wrap in try/except so the two paths
            # remain idempotent.
            if isinstance(entity, TeamLead) and entity.maestro_name:
                maestro = self._entities.get(entity.maestro_name)
                if isinstance(maestro, Maestro):
                    try:
                        maestro.remove_team(entity.team_name)
                    except KeyError:
                        pass

            # Clear session_id so a stale --resume isn't persisted to DB
            entity.session_id = None

            if entity.state == EntityState.RUNNING:
                entity.transition_to(EntityState.STOPPED)
            async with self._state_lock:
                self._entities.pop(name, None)

        # Remove from DB so dead entities don't reappear on restart
        if self.entity_store is not None:
            try:
                await self.entity_store.delete(name)
            except Exception:
                logger.exception("Failed to delete entity %s from DB", name)

        self.router.unregister(name)
        await self._audit("entity.kill", target=name)
        if self.scheduler is not None:
            self.scheduler.refund_autospawn(name)
        logger.info("Killed entity: %s", name)

    async def kill_all(self) -> None:
        """Gracefully shutdown all entities."""
        names = list(self._entities.keys())
        for name in names:
            await self.kill_entity(name)

    async def stop_all(self) -> None:
        """Stop all entity subprocesses without deleting DB rows.

        Used on graceful shutdown so entities can be restored on next boot
        via restore() + rebuild_hierarchy(). Preserves session_id so the
        next spawn can --resume the prior conversation.
        """
        for name, session in list(self._sessions.items()):
            try:
                await session.kill()
            except Exception:
                logger.exception("Failed to kill session for %s on shutdown", name)
        async with self._state_lock:
            self._sessions.clear()

        for name, adapter in list(self._adapters.items()):
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Failed to stop adapter for %s on shutdown", name)
        self._adapters.clear()

        if self.quota_monitor is not None:
            try:
                await self.quota_monitor.stop()
            except Exception:
                logger.exception("Failed to stop QuotaMonitor on shutdown")

        logger.info("Stopped %d entity sessions for restart", len(self._entities))

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

    def _task_id_for(self, entity_name: str) -> int | None:
        return self.dispatcher._task_id_for(entity_name)

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
            session = self._sessions.get(name)
            statuses.append(
                {
                    "name": name,
                    "role": entity.role,
                    "state": entity.state.value,
                    "model": entity.model,
                    "pid": entity.pid,
                    "alive": session.is_alive if session else False,
                    "uptime": entity.uptime_seconds,
                }
            )
        return statuses

    async def health_check(self) -> list[str]:
        """Check which sessions are dead but entities think they're running.

        Returns list of entity names that need attention.
        """
        unhealthy: list[str] = []
        # Snapshot to avoid iteration-during-mutation. Don't hold the lock
        # across the awaits below.
        async with self._state_lock:
            entries = list(self._entities.items())
        for name, entity in entries:
            session = self._sessions.get(name)
            if entity.state == EntityState.RUNNING and (session is None or not session.is_alive):
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
        """Compact an entity's context: summarize, kill, re-register, seed.

        Returns the summary text on success.
        Raises KeyError if entity not found, ValueError if no active session.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            raise KeyError(f"Entity {entity_name!r} not found.")
        if not entity.session_id:
            raise ValueError(f"Entity {entity_name!r} has no active session to compact.")

        # Step 1: Ask entity to summarize its context
        summary = await self.send_to_entity(
            entity_name,
            "Summarize your entire conversation context in 3 concise bullet points. "
            "Include key decisions, current state, and next steps.",
        )

        # Step 2: Kill entity (clears session_id, removes from registry)
        await self.kill_entity(entity_name)

        # Step 3: Re-register entity in IDLE state
        async with self._state_lock:
            self._entities[entity_name] = entity
        self.router.register(entity_name)
        entity.session_id = None
        entity.state = EntityState.IDLE

        # Step 4: Seed new session with summary
        await self.send_to_entity(
            entity_name,
            f"Here is your prior context (compacted):\n{summary}\n\nContinue from here.",
        )

        await self._persist(entity)
        await self._audit(
            "entity.compact",
            target=entity_name,
            details={"summary_len": len(summary)},
        )
        logger.info("Compacted entity %s (summary: %d chars)", entity_name, len(summary))
        return summary

    async def kill_idle_entities(
        self,
        timeout_minutes: int,
        exempt_names: set[str] | None = None,
    ) -> list[str]:
        """Kill entities that have been idle longer than timeout_minutes.

        Returns list of killed entity names.
        Entities in exempt_names are never killed.
        """
        exempt = exempt_names or set()
        cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
        killed: list[str] = []

        for name, entity in list(self._entities.items()):
            if name in exempt:
                continue
            # A GATED entity is parked on an interactive gate awaiting the
            # user's decision (ADR 0004). It is intentionally idle and must
            # never be reaped, regardless of exempt_names.
            if entity.state == EntityState.GATED:
                continue
            if entity.last_activity_at is None:
                continue
            if entity.last_activity_at < cutoff:
                idle_minutes = int(
                    (datetime.now(UTC) - entity.last_activity_at).total_seconds() / 60
                )
                try:
                    await self.kill_entity(name)
                    await self._audit(
                        "entity.auto_kill_idle",
                        target=name,
                        details={"idle_minutes": idle_minutes},
                    )
                    await self._notify(f"Auto-killed idle entity {name} (inactive {idle_minutes}m)")
                    killed.append(name)
                except Exception:
                    logger.exception("Failed to auto-kill idle entity %s", name)

        return killed

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
        """Reconstruct Maestro.teams from restored TeamLead/Worker entities.

        Called once after all entities are restored from the DB. Iterates
        restored entities and links TeamLeads to their parent Maestro's
        teams dict, and Workers to their TeamLead's workers list.
        """
        from hive.models.team import Team

        # First pass: create teams from TeamLeads
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

        # Second pass: attach Workers to their leads and teams
        for entity in self._entities.values():
            if isinstance(entity, Worker) and entity.lead_name:
                lead = self._entities.get(entity.lead_name)
                if isinstance(lead, TeamLead) and entity.name not in lead.workers:
                    lead.workers.append(entity.name)

                # Also add to the team's worker list
                if entity.lead_name:
                    maestro_name = entity.lead_name.split(".")[0] if "." in entity.lead_name else ""
                    maestro = self._entities.get(maestro_name)
                    if isinstance(maestro, Maestro):
                        team = maestro.get_team(entity.team_name)
                        if team and entity.name not in team.workers:
                            team.workers.append(entity.name)

        logger.info("Rebuilt hierarchy for %d entities", len(self._entities))
