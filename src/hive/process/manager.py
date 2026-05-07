"""Process manager — spawns, tracks, and kills Claude Code agent subprocesses."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from hive.bus.actions import Action, parse_actions
from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.permissions import (
    can_kill,
    can_message,
    can_request_decision,
    can_spawn_team,
    can_spawn_worker,
    cc_targets_for,
)
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import (
    ADVISOR_ENABLED,
    AUTO_COMPACT_ENABLED,
    AUTO_COMPACT_THRESHOLD,
    AUTO_RETRIEVE_ENABLED,
    AUTO_RETRIEVE_FIRST_TURN_ONLY,
    AUTO_RETRIEVE_INCLUDE_ATTACHMENTS,
    AUTO_RETRIEVE_MAX_DISTANCE,
    AUTO_RETRIEVE_TOP_K,
    DEFAULT_MAESTRO,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.mcp.config import generate_mcp_config
from hive.models.entity import (
    DANGEROUS_MODES,
    Entity,
    EntityState,
    is_auto_generated_personality,
)
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.vault import Vault
from hive.models.worker import WorkerAgent
from hive.notifications import Notification, NotificationDispatcher
from hive.process.claude_session import ClaudeSession
from hive.process.worktree import WorktreeManager
from hive.vault.provider import PaymentProvider
from hive.vault.spend_caps import check_caps

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
    """
    tools_section = ""
    if role in ("maestro", "lead"):
        tools_section = "\n## Tools\n- allowedTools: Read Grep Glob\n"
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
        self._compacting: set[str] = set()
        # Set after construction by __main__.py so the dispatch site can
        # consult the rate limiter. Optional — tests construct managers
        # without a scheduler and the spawn dispatch falls back to "allow".
        self.scheduler: object | None = None

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
        # --- Sprint 27: dialed down — top_k=1, first-turn only. Smarter
        #     agents call the ``search_knowledge`` MCP tool when they need
        #     more or different context.
        prepended_blocks: list[str] = []
        directory_block = self._peer_directory_for(entity_name)
        if directory_block:
            prepended_blocks.append(directory_block)

        # ``session_id`` is set after the first prompt of an activation, so
        # ``is None`` is the cheapest signal for "first turn this session."
        is_first_turn = entity.session_id is None
        auto_retrieve_active = (
            AUTO_RETRIEVE_ENABLED
            and prompt.strip()
            and (is_first_turn or not AUTO_RETRIEVE_FIRST_TURN_ONLY)
        )

        if auto_retrieve_active:
            knowledge_blocks: list[str] = []

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
                        # Sprint 26: render the matching chunk only, not the
                        # full body — sharper context, less prompt bloat.
                        bp_lines.append(f"\n### {h['title']}\n{h['chunk_text']}")
                    knowledge_blocks.append("\n".join(bp_lines))

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
                    knowledge_blocks.append("\n".join(file_lines))

            if knowledge_blocks:
                # Sprint 27: nudge the agent toward search_knowledge for
                # mid-conversation lookups when the auto-block doesn't match.
                knowledge_blocks.append(
                    "(Need different context? Call the `search_knowledge` "
                    "MCP tool with your own query.)"
                )
                prepended_blocks.extend(knowledge_blocks)

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
        return await self._handle_actions(entity_name, clean_text, actions)

    async def _handle_actions(
        self, entity_name: str, clean_text: str, actions: list[Action]
    ) -> str:
        """Route parsed actions to the appropriate handlers.

        Extracted from ``send_to_entity`` so tests can drive the
        dispatch loop directly without going through a real Claude
        subprocess.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            return clean_text

        self._last_routed_actions = []
        self._last_mode_requests = []
        self._last_failure_reports = []
        self._last_spawned_teams = []
        self._last_spawned_workers = []
        self._last_killed_entities = []
        self._last_vault_requests = []
        for action in actions:
            if action.type == "message":
                recipient = self._entities.get(action.to) if action.to else None
                if not recipient:
                    logger.warning("Unknown recipient: %s", action.to)
                    continue
                if not can_message(entity.role, entity.name, recipient.role, recipient.name):
                    logger.warning("Permission denied: %s -> %s", entity.name, action.to)
                    await self._audit(
                        "peer_message_blocked",
                        target=action.to,
                        details={"sender": entity_name, "reason": "permission_denied"},
                        actor=entity_name,
                    )
                    continue
                body = action.text or ""
                await self.router.route(entity_name, action.to, body)
                self._last_routed_actions.append(action.to)
                await self._audit(
                    "peer_message_sent",
                    target=action.to,
                    details={"sender": entity_name, "text": body[:200]},
                    actor=entity_name,
                )
                cc_targets = cc_targets_for(
                    entity.role, entity.name, recipient.role, recipient.name
                )
                cc_body = f"[CC: {entity.name} -> {action.to}] {body}"
                for cc_name in cc_targets:
                    if cc_name not in self._entities:
                        continue
                    await self.router.route(entity_name, cc_name, cc_body)
                    await self._audit(
                        "peer_message_cc_inserted",
                        target=cc_name,
                        details={
                            "sender": entity_name,
                            "recipient": action.to,
                            "text": body[:200],
                        },
                        actor=entity_name,
                    )
            elif action.type == "request_decision":
                if not action.to:
                    continue
                recipient = self._entities.get(action.to)
                if not recipient:
                    logger.warning("Unknown request_decision recipient: %s", action.to)
                    continue
                if not can_request_decision(entity.role, entity.name, action.to):
                    logger.warning("request_decision denied: %s -> %s", entity.name, action.to)
                    await self._audit(
                        "request_decision_blocked",
                        target=action.to,
                        details={"sender": entity_name, "reason": "permission_denied"},
                        actor=entity_name,
                    )
                    continue
                body = f"[DECISION REQUEST] {action.text or ''}"
                await self.router.route(entity_name, action.to, body)
                self._last_routed_actions.append(action.to)
                await self._audit(
                    "request_decision_sent",
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
            elif action.type == "request_payment":
                try:
                    action_id = await self.request_payment(
                        entity_name,
                        amount_cents=action.amount_cents or 0,
                        currency=action.currency or "USD",
                        recipient=action.recipient or "",
                        idempotency_key=action.idempotency_key or "",
                        reason=action.reason or "",
                    )
                    if action_id is not None:
                        self._last_vault_requests.append(action_id)
                except (KeyError, ValueError, PermissionError) as exc:
                    logger.warning("request_payment from %s rejected: %s", entity_name, exc)
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
            elif action.type == "spawn_team":
                if not action.team_name:
                    continue
                if not can_spawn_team(entity.role, entity.name):
                    logger.warning("spawn_team denied: %s (role=%s)", entity.name, entity.role)
                    await self._audit(
                        "entity.spawn_team_denied",
                        target=action.team_name,
                        details={"reason": "role_not_permitted", "role": entity.role},
                        actor=entity_name,
                    )
                    continue
                if self.scheduler is not None and not self.scheduler.can_autospawn(entity_name):
                    logger.warning("spawn_team rate-limited: %s", entity_name)
                    await self._audit(
                        "entity.spawn_rate_limited",
                        target=action.team_name,
                        details={"action_type": "spawn_team", "limit": self.scheduler.spawn_limit},
                        actor=entity_name,
                    )
                    continue
                try:
                    lead = await self.create_team(
                        entity_name,
                        action.team_name,
                        model=action.model or "sonnet",
                        display_name=action.display_name,
                        personality=action.personality,
                    )
                    self._last_spawned_teams.append(lead.name)
                    if self.scheduler is not None:
                        self.scheduler.record_autospawn(entity_name)
                    await self._audit(
                        "entity.spawn_team",
                        target=lead.name,
                        details={"team": action.team_name, "maestro": entity_name},
                        actor=entity_name,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("spawn_team from %s failed: %s", entity_name, exc)
            elif action.type == "spawn_worker":
                # `lead` is optional in the protocol — leads pattern-match
                # the field name "lead" instead of substituting the
                # placeholder, so requiring it produces `{"lead": "lead"}`.
                # Infer it from the actor: a lead spawns under itself; a
                # maestro must specify (we can't guess which team).
                if not action.lead:
                    if entity.role == "lead":
                        action.lead = entity.name
                    else:
                        logger.warning(
                            "spawn_worker from %s missing `lead` field (role=%s)",
                            entity.name,
                            entity.role,
                        )
                        await self._audit(
                            "entity.spawn_worker_denied",
                            target=None,
                            details={"reason": "missing_lead", "role": entity.role},
                            actor=entity_name,
                        )
                        continue
                if not can_spawn_worker(entity.role, entity.name, action.lead):
                    logger.warning("spawn_worker denied: %s -> %s", entity.name, action.lead)
                    await self._audit(
                        "entity.spawn_worker_denied",
                        target=action.lead,
                        details={"reason": "scope_violation", "role": entity.role},
                        actor=entity_name,
                    )
                    continue
                if self.scheduler is not None and not self.scheduler.can_autospawn(entity_name):
                    logger.warning("spawn_worker rate-limited: %s", entity_name)
                    await self._audit(
                        "entity.spawn_rate_limited",
                        target=action.lead,
                        details={
                            "action_type": "spawn_worker",
                            "limit": self.scheduler.spawn_limit,
                        },
                        actor=entity_name,
                    )
                    continue
                try:
                    worker = await self.spawn_worker(
                        action.lead,
                        worker_name=action.worker_name,
                        task_id=action.task_id,
                        display_name=action.display_name,
                        personality=action.personality,
                    )
                    self._last_spawned_workers.append(worker.name)
                    if self.scheduler is not None:
                        self.scheduler.record_autospawn(entity_name)
                    await self._audit(
                        "entity.autonomous_spawn_worker",
                        target=worker.name,
                        details={"lead": action.lead, "task_id": action.task_id},
                        actor=entity_name,
                    )
                except (KeyError, TypeError, RuntimeError) as exc:
                    logger.warning("spawn_worker from %s failed: %s", entity_name, exc)
            elif action.type == "kill_entity":
                if not action.target:
                    continue
                if not can_kill(entity.role, entity.name, action.target, DEFAULT_MAESTRO):
                    logger.warning("kill_entity denied: %s -> %s", entity.name, action.target)
                    await self._audit(
                        "entity.kill_denied",
                        target=action.target,
                        details={"reason": "permission_denied", "role": entity.role},
                        actor=entity_name,
                    )
                    continue
                if action.target not in self._entities:
                    logger.warning("kill_entity target not found: %s", action.target)
                    continue
                try:
                    await self.kill_entity(action.target)
                    self._last_killed_entities.append(action.target)
                    await self._audit(
                        "entity.autonomous_kill",
                        target=action.target,
                        details={"actor_role": entity.role},
                        actor=entity_name,
                    )
                except Exception:
                    logger.exception("kill_entity from %s failed", entity_name)

        return clean_text

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
    ) -> WorkerAgent:
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
        logger.info("Stopped %d entity sessions for restart", len(self._entities))

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
        if self.vault_store is None:
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

        entity = self._entities.get(requester)
        if entity is None:
            raise KeyError(f"Unknown requester {requester!r}")
        if not isinstance(entity, Vault):
            await self._audit(
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
            row = await self.vault_store.create_action(
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
            await self._audit(
                "vault.duplicate_idempotency_key",
                target=requester,
                details={"idempotency_key": idempotency_key, "recipient": recipient},
                actor=requester,
            )
            return None

        await self._audit(
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
        await self._notify(
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
        if self.vault_store is None:
            return None
        row = await self.vault_store.get(action_id)
        if row is None:
            return None
        if row["status"] != "pending":
            return row

        # Legacy generic action — keep the Sprint 6 free-text path alive.
        if row.get("action_type") != "payment":
            approved = await self.vault_store.approve(action_id)
            if approved is not None:
                await self._audit(
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
                self.vault_store,
                vault_name=vault_name,
                amount_cents=amount,
                currency=currency,
                daily_cap_cents=self.vault_daily_cap_cents,
                monthly_cap_cents=self.vault_monthly_cap_cents,
                cap_currencies=self.vault_cap_currencies,
            )
        except ValueError as exc:
            cap_reason = str(exc)
            denied = await self.vault_store.deny(action_id, reason=cap_reason)
            await self._audit(
                "vault.cap_exceeded",
                target=vault_name,
                details={"id": action_id, "reason": cap_reason},
            )
            await self._notify(
                f"[vault denied #{action_id}] {cap_reason}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "denied", "reason": cap_reason},
            )
            return denied

        if not cap.ok:
            denied = await self.vault_store.deny(action_id, reason=cap.reason)
            await self._audit(
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
            await self._notify(
                f"[vault denied #{action_id}] {cap.reason}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "denied", "reason": cap.reason},
            )
            return denied

        provider = self.payment_provider
        if provider is None:
            err = "no payment provider configured"
            failed = await self.vault_store.mark_failed(action_id, err)
            await self._audit(
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
            failed = await self.vault_store.mark_failed(action_id, err)
            await self._audit(
                "vault.failed",
                target=vault_name,
                details={"id": action_id, "reason": err},
            )
            await self._notify(
                f"[vault failed #{action_id}] {err}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "failed", "reason": err},
            )
            return failed

        if not result.ok:
            failed = await self.vault_store.mark_failed(
                action_id,
                result.error or "provider reported failure",
                result.to_payload(),
            )
            await self._audit(
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
            await self._notify(
                f"[vault failed #{action_id}] {result.error or 'provider failure'}",
                kind="vault_action_resolved",
                data={"id": action_id, "status": "failed", "reason": result.error},
            )
            return failed

        completed = await self.vault_store.mark_executed(action_id, result.to_payload())
        await self._audit(
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
        await self._notify(
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
        if self.vault_store is None:
            return None
        denied = await self.vault_store.deny(action_id, reason=reason)
        if denied is None:
            return None
        await self._audit(
            "vault.denied",
            target=denied["vault_name"],
            details={"id": action_id, "reason": reason},
        )
        await self._notify(
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
