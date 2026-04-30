"""Process manager — spawns, tracks, and kills Claude Code agent subprocesses."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hive.bus.actions import parse_actions
from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.permissions import can_message
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.config import (
    ADVISOR_ENABLED,
    AUTO_COMPACT_ENABLED,
    AUTO_COMPACT_THRESHOLD,
    AUTO_RETRIEVE_ENABLED,
    AUTO_RETRIEVE_INCLUDE_ATTACHMENTS,
    AUTO_RETRIEVE_MAX_DISTANCE,
    AUTO_RETRIEVE_TOP_K,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.mcp.config import generate_mcp_config
from hive.models.entity import DANGEROUS_MODES, Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent
from hive.notifications import Notification, NotificationDispatcher
from hive.process.claude_session import ClaudeSession
from hive.process.worktree import WorktreeManager

logger = logging.getLogger(__name__)


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
        notification_dispatcher: NotificationDispatcher | None = None,
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
        self.notification_dispatcher = notification_dispatcher
        self._entities: dict[str, Entity] = {}
        self._sessions: dict[str, ClaudeSession] = {}
        self._last_routed_actions: list[str] = []
        self._last_mode_requests: list[int] = []
        self._last_failure_reports: list[int] = []
        self._compacting: set[str] = set()

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

    async def _record_usage(self, entity: Entity, session: ClaudeSession) -> None:
        """Record token usage from a completed session, if a store is configured.

        Merges the entity's canonical ``model`` into the session's captured
        usage dict before handing it to the store. Fire-and-continue: any
        DB error is logged and swallowed, since token bookkeeping must not
        take down the user-facing send path.
        """
        if self.token_store is None:
            return
        usage = session.last_usage
        if usage is None:
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
        return sum(1 for s in self._sessions.values() if s.is_alive)

    async def _preempt_for_priority(self, priority: int) -> str | None:
        """Try to free a session slot by killing the lowest-priority running entity.

        Returns the name of the killed entity, or None if no preemption is
        possible (either under capacity or all running entities are at equal
        or higher priority).
        """
        if self.active_count < self.max_sessions:
            return None

        # Find the running entity with the worst (highest number) priority
        worst_name: str | None = None
        worst_priority = -1
        for name, entity in self._entities.items():
            if entity.state == EntityState.RUNNING and entity.current_priority > worst_priority:
                worst_priority = entity.current_priority
                worst_name = name

        if worst_name is None or worst_priority <= priority:
            return None

        await self.kill_entity(worst_name)
        return worst_name

    async def register_maestro(
        self,
        name: str,
        model: str = "sonnet",
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
        self._entities[entity.name] = entity
        self.router.register(entity.name)
        logger.info("Registered entity: %s (role=%s)", entity.name, entity.role)

    async def spawn_entity(self, entity: Entity, cwd: Path | None = None) -> ClaudeSession:
        """Spawn a Claude Code subprocess for an entity.

        Loads personality, builds CLI args, creates session, and starts it.
        """
        if self.active_count >= self.max_sessions:
            raise RuntimeError(
                f"Max concurrent sessions ({self.max_sessions}) reached. Kill an entity first."
            )

        if entity.name in self._sessions and self._sessions[entity.name].is_alive:
            raise RuntimeError(f"Entity {entity.name!r} is already running.")

        # Load personality if available
        entity.load_personality()

        # Write per-entity MCP config so Claude Code can connect to the advisor server
        from hive.config import ADVISOR_ENABLED
        from hive.mcp.config import generate_mcp_config

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

        # Track
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

    async def send_to_entity(self, entity_name: str, prompt: str) -> str:
        """Send a prompt to an entity and get the response.

        Each call spawns a fresh subprocess. If the entity has a stored
        session_id from a previous call, ``--resume`` is passed so the
        Claude CLI resumes the prior conversation context.

        Pending inter-agent messages are prepended to the prompt.
        After the response, any ``<hive_actions>`` block is parsed and
        routed to the appropriate recipients.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            raise KeyError(f"Entity {entity_name!r} not found.")

        # Track activity for idle-kill detection
        entity.last_activity_at = datetime.now(UTC)

        # --- Phase 2: drain pending inter-agent messages ---
        pending: list[str] = []
        while self.router.has_pending(entity_name):
            msg = await self.router.get_next(entity_name, timeout=0.1)
            if msg:
                pending.append(f"[Message from {msg.sender}]: {msg.content}")
        if pending:
            inbox = "\n".join(pending)
            prompt = f"You have pending messages from other entities:\n{inbox}\n\n---\n\n{prompt}"

        # --- Sprint 11: auto-retrieve top-K blueprints as context ---
        # --- Sprint 18: also pull semantically-related uploaded files. ---
        # Blueprints are consumed inline (body in the prompt); files are
        # rendered as a separate block listing paths so the agent's first
        # move is a `Read` on the path it cares about.
        prepended_blocks: list[str] = []
        if AUTO_RETRIEVE_ENABLED and prompt.strip():
            if self.blueprint_store is not None:
                try:
                    blueprint_hits = await self.blueprint_store.search(
                        prompt,
                        limit=AUTO_RETRIEVE_TOP_K,
                        max_distance=AUTO_RETRIEVE_MAX_DISTANCE,
                    )
                except Exception:
                    logger.exception("auto-retrieve failed; continuing without blueprints")
                    blueprint_hits = []
                if blueprint_hits:
                    bp_lines = ["Relevant past blueprints (retrieved automatically):"]
                    for h in blueprint_hits:
                        bp_lines.append(f"\n### {h['title']}\n{h['body']}")
                    prepended_blocks.append("\n".join(bp_lines))

            if AUTO_RETRIEVE_INCLUDE_ATTACHMENTS and self.attachment_store is not None:
                try:
                    attachment_hits = await self.attachment_store.search(
                        prompt,
                        limit=AUTO_RETRIEVE_TOP_K,
                        max_distance=AUTO_RETRIEVE_MAX_DISTANCE,
                    )
                except Exception:
                    logger.exception("auto-retrieve failed; continuing without attachments")
                    attachment_hits = []
                if attachment_hits:
                    file_lines = ["Relevant uploaded files (retrieved automatically):"]
                    for h in attachment_hits:
                        snippet = (h.get("embed_text") or "")[:200].replace("\n", " ")
                        name = h.get("original_name") or h["file_path"]
                        mime = h.get("mime_type") or "unknown"
                        file_lines.append(
                            f"- {h['file_path']} ({mime}, original: {name})"
                            + (f' — snippet: "{snippet}"' if snippet else "")
                        )
                    prepended_blocks.append("\n".join(file_lines))

        if prepended_blocks:
            context_block = "\n\n---\n\n".join(prepended_blocks)
            prompt = f"{context_block}\n\n---\n\n{prompt}"

        args = entity.build_cli_args()
        if entity.session_id:
            args.extend(["--resume", entity.session_id])

        if ADVISOR_ENABLED:
            generate_mcp_config(entity.name, entity.mcp_config_path)

        session = ClaudeSession(args=args)
        await session.start()

        try:
            response = await session.send_prompt(prompt)
            await self._record_usage(entity, session)

            # Auto-compact if context is too large
            if (
                AUTO_COMPACT_ENABLED
                and entity_name not in self._compacting
                and session.last_usage
                and session.last_usage.get("input_tokens", 0) > AUTO_COMPACT_THRESHOLD
            ):
                input_tokens = session.last_usage["input_tokens"]
                logger.info(
                    "Auto-compacting %s (input_tokens=%d > threshold=%d)",
                    entity_name,
                    input_tokens,
                    AUTO_COMPACT_THRESHOLD,
                )
                self._compacting.add(entity_name)
                try:
                    await self.compact_entity(entity_name)
                    await self._notify(
                        f"Auto-compacted {entity_name} (context: {input_tokens:,} tokens)"
                    )
                    await self._audit(
                        "entity.auto_compact",
                        target=entity_name,
                        details={"input_tokens": input_tokens},
                    )
                except Exception:
                    logger.exception("Auto-compact failed for %s", entity_name)
                finally:
                    self._compacting.discard(entity_name)

            # Store session_id for resume on next call
            if session.session_id:
                entity.session_id = session.session_id
                await self._persist(entity)
        finally:
            await session.kill()

        # --- Phase 3: parse and route actions from response ---
        clean_text, actions = parse_actions(response)
        self._last_routed_actions = []
        self._last_mode_requests = []
        self._last_failure_reports = []
        for action in actions:
            if action.type == "message":
                recipient = self._entities.get(action.to) if action.to else None
                if not recipient:
                    logger.warning("Unknown recipient: %s", action.to)
                    continue
                if not can_message(entity.role, entity.name, recipient.role, recipient.name):
                    logger.warning("Permission denied: %s -> %s", entity.name, action.to)
                    continue
                await self.router.route(entity_name, action.to, action.text or "")
                self._last_routed_actions.append(action.to)
                await self._audit(
                    "message.autonomous",
                    target=action.to,
                    details={"sender": entity_name, "text": (action.text or "")[:200]},
                    actor=entity_name,
                )
            elif action.type == "request_mode_change":
                if not action.requested_mode:
                    continue
                try:
                    req_id = await self.request_mode_change(
                        entity_name,
                        action.requested_mode,
                        reason=action.reason,
                    )
                    self._last_mode_requests.append(req_id)
                except (KeyError, ValueError) as exc:
                    logger.warning("request_mode_change from %s failed: %s", entity_name, exc)
            elif action.type == "report_failure":
                reason = action.reason or "(no reason given)"
                task_id = action.task_id
                if task_id is None:
                    task_id = self._task_id_for(entity_name)
                if task_id is None:
                    logger.warning(
                        "report_failure from %s with no task_id and entity has no bound task",
                        entity_name,
                    )
                    continue
                try:
                    await self.handle_task_failure(task_id, reason)
                    self._last_failure_reports.append(task_id)
                except Exception:
                    logger.exception("handle_task_failure failed for task %s", task_id)

        return clean_text

    async def create_team(
        self, maestro_name: str, team_name: str, model: str = "sonnet"
    ) -> TeamLead:
        """Create a new team under a maestro.

        Registers a TeamLead entity named ``maestro.team``. The lead is
        not spawned as a subprocess — it stays IDLE until someone sends
        it a message via send_to_entity.
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
        )
        team.lead = lead_name

        self._entities[lead_name] = lead
        self.router.register(lead_name)
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
    ) -> WorkerAgent:
        """Spawn a worker under a team lead.

        If worker_name is None, auto-generates ``w1``, ``w2``, etc.
        The worker is registered but not spawned as a subprocess — it
        stays IDLE until work is assigned via send_to_entity.
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

        worker = WorkerAgent(
            name=full_name,
            team_name=lead.team_name,
            lead_name=lead_name,
            model=lead.model,
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

        self._entities[full_name] = worker
        self.router.register(full_name)
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
        """Kill an entity's subprocess and clean up."""
        session = self._sessions.get(name)
        if session:
            await session.kill()
            del self._sessions[name]

        entity = self._entities.get(name)
        if entity:
            # Clean up worktree for workers
            if isinstance(entity, WorkerAgent) and entity.worktree_path and self.worktree_mgr:
                try:
                    await self.worktree_mgr.remove(name)
                except Exception:
                    logger.exception("Failed to remove worktree for %s", name)

            # Remove worker from parent lead's and team's worker lists
            if isinstance(entity, WorkerAgent) and entity.lead_name:
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

            # Clear session_id so a stale --resume isn't persisted to DB
            entity.session_id = None

            if entity.state == EntityState.RUNNING:
                entity.transition_to(EntityState.STOPPED)
            del self._entities[name]

        # Remove from DB so dead entities don't reappear on restart
        if self.entity_store is not None:
            try:
                await self.entity_store.delete(name)
            except Exception:
                logger.exception("Failed to delete entity %s from DB", name)

        self.router.unregister(name)
        await self._audit("entity.kill", target=name)
        logger.info("Killed entity: %s", name)

    async def kill_all(self) -> None:
        """Gracefully shutdown all entities."""
        names = list(self._entities.keys())
        for name in names:
            await self.kill_entity(name)

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
        if entity.role == "worker" and isinstance(entity, WorkerAgent):
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
        if self.mode_request_store is None:
            raise ValueError("mode_request_store not configured")
        if requested_mode not in DANGEROUS_MODES:
            raise ValueError(
                f"Mode {requested_mode!r} does not require approval. "
                f"Valid: {', '.join(sorted(DANGEROUS_MODES))}"
            )
        entity = self._entities.get(requester)
        if entity is None:
            raise KeyError(f"Unknown requester {requester!r}")

        approver = self._approver_for(entity)
        row = await self.mode_request_store.create(
            requester=requester,
            requested_mode=requested_mode,
            approver=approver,
            reason=reason,
        )
        await self._audit(
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
            await self._notify(
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

    async def approve_mode_request(self, request_id: int) -> dict | None:
        """Approve a pending mode request and update the requester's mode.

        For ``yotree``, caller is responsible for ensuring a worktree is
        attached before the next spawn — workers already have one; leads
        and maestros need one provisioned by the caller or by a future
        spawn helper.
        """
        if self.mode_request_store is None:
            return None
        row = await self.mode_request_store.approve(request_id)
        if row is None:
            return None

        entity = self._entities.get(row["requester"])
        if entity is not None:
            entity.permission_mode = row["requested_mode"]
            await self._persist(entity)
        await self._audit(
            "mode.approve",
            target=row["requester"],
            details={"id": request_id, "mode": row["requested_mode"]},
        )
        return row

    async def deny_mode_request(self, request_id: int, reason: str | None = None) -> dict | None:
        """Deny a pending mode request. Entity's current mode is unchanged."""
        if self.mode_request_store is None:
            return None
        row = await self.mode_request_store.deny(request_id, reason=reason)
        if row is None:
            return None
        await self._audit(
            "mode.deny",
            target=row["requester"],
            details={"id": request_id, "reason": reason},
        )
        return row

    async def expire_old_mode_requests(self, cutoff: datetime) -> list[dict]:
        """Expire pending mode requests older than cutoff. Returns expired rows."""
        if self.mode_request_store is None:
            return []
        rows = await self.mode_request_store.expire_older_than(cutoff)
        for row in rows:
            await self._audit(
                "mode.expire",
                target=row["requester"],
                details={"id": row["id"], "mode": row["requested_mode"]},
            )
        return rows

    # -----------------------------------------------------------------
    # Auto-recovery on task failures (Sprint 12 Phase 4)
    # -----------------------------------------------------------------

    def _task_id_for(self, entity_name: str) -> int | None:
        """Return the active task_id bound to an entity, if it's a worker."""
        entity = self._entities.get(entity_name)
        if isinstance(entity, WorkerAgent):
            return entity.task_id
        return None

    def _escalation_target_for(self, entity_name: str) -> str:
        """Next rung up the hierarchy when a task fails past max retries.

        Workers escalate to their parent lead, leads to their parent
        maestro, maestros to the user. Returns ``"user"`` when escalation
        reaches the top.
        """
        entity = self._entities.get(entity_name)
        if isinstance(entity, WorkerAgent):
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
        if self.task_store is None:
            logger.warning("handle_task_failure called but task_store not configured")
            return

        task = await self.task_store.increment_retry(task_id, error)
        if task is None:
            logger.warning("handle_task_failure: task %s not found", task_id)
            return

        assigned = task.assigned_to
        if task.retry_count <= task.max_retries and assigned and assigned in self._entities:
            await self._audit(
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
                await self.send_to_entity(assigned, retry_prompt)
            except Exception:
                logger.exception("Retry send_to_entity failed for %s", assigned)
            return

        # Escalate: reached max retries, or no assignee to retry on.
        if assigned and assigned in self._entities:
            next_rung = self._escalation_target_for(assigned)
        else:
            next_rung = "user"
        await self.task_store.update_failure(task_id, error)
        await self._audit(
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
            await self._audit(
                "task.gave_up",
                target=str(task_id),
                details={"reason": error[:200]},
            )
            await self._notify(summary)
            return

        # Escalate to a registered parent entity by routing an internal
        # message. The parent's next prompt will include this as pending
        # inbox content; they can decide to reassign, abort, or message
        # the user.
        if next_rung in self._entities and assigned is not None:
            await self.router.route(assigned, next_rung, summary)

    def get_status(self) -> list[dict]:
        """Return status of all tracked entities."""
        statuses = []
        for name, entity in self._entities.items():
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
        for name, entity in self._entities.items():
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
            if isinstance(entity, WorkerAgent) and entity.lead_name:
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
