"""CommandDispatcher — surface-agnostic command execution.

Both the Telegram bridge and (Sprint 15+) web endpoints route parsed
commands here. Each command path returns a :class:`CommandResult` (plain
text + optional metadata); the calling surface formats the result for
its transport.

Extracted from :class:`hive.telegram.bridge.TelegramBridge` in Sprint 15
so the web write surface can reuse the same execution paths.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hive.commands._helpers import _parse_task_id, _strip_quotes
from hive.commands.datastore_commands import DataStoreCommands
from hive.commands.formatter import Formatter
from hive.commands.git_commands import GitCommands
from hive.commands.result import CommandResult
from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.project import Project, ProjectOwnershipError
from hive.models.task import TaskStatus
from hive.telegram.commands import Command, parse_command

if TYPE_CHECKING:
    from hive.bus.attachment_store import AttachmentStore
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.knowledge.blueprints import BlueprintStore
    from hive.process.manager import ProcessManager
    from hive.process.scheduler import PriorityScheduler

logger = logging.getLogger(__name__)


# Interactive `/new maestro` flow — multi-turn Q&A keyed by actor.
# Pending state lives in-memory on the dispatcher; expires after this
# inactivity window so abandoned flows don't block the actor's plain
# text from routing to the default maestro.
_PENDING_NEW_TIMEOUT = timedelta(minutes=10)
_NEW_MAESTRO_QUESTIONS: tuple[str, ...] = (
    "What's this maestro for? (e.g., 'manage my personal-assistant project')",
    "Communication style — terse, verbose, formal, casual?",
)


@dataclass
class _PendingNewMaestro:
    """In-flight `/new maestro` flow for one actor."""

    name: str
    model: str
    answers: list[str] = field(default_factory=list)
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))


# ``KNOWN_COMMANDS`` (the set of commands the dispatcher executes) is now
# *derived* from ``CommandDispatcher._ROUTES`` — the single source of truth —
# and defined just after the class below. The Telegram bridge re-exports it
# (plus surface-only commands like /heartbeat) as ``BRIDGE_COMMANDS`` for the
# /help drift guard.


class CommandDispatcher:
    """Executes parsed commands against ProcessManager + stores.

    Stateless w.r.t. transport — both the Telegram bridge and the web
    write endpoints construct one of these and call :meth:`dispatch` (or
    :meth:`dispatch_command` if they already parsed the input).
    """

    # Single source of truth for routing. Maps a command name to
    # ``(group_attr | None, handler_method)`` — ``None`` means the handler
    # lives on the facade itself; a string names a collaborator attribute.
    # Every handler has the uniform shape ``async (cmd, actor) -> CommandResult``.
    # ``KNOWN_COMMANDS`` is derived from these keys, so adding a command is
    # one handler method + one entry here (no separate if-chain / set to sync).
    _ROUTES: dict[str, tuple[str | None, str]] = {
        "empty": (None, "_h_empty"),
        "status": ("formatter", "status"),
        "health": ("formatter", "health"),
        "maestros": ("formatter", "maestros"),
        "org": ("formatter", "org"),
        "comms": ("formatter", "comms"),
        "kill": (None, "_h_kill"),
        "message": (None, "_h_message"),
        "cost": ("formatter", "cost"),
        "quota": ("formatter", "quota"),
        "task": (None, "_h_task"),
        "tasks": ("formatter", "tasks"),
        "audit": ("formatter", "audit"),
        "team": (None, "_h_team"),
        "teams": ("formatter", "teams"),
        "project": (None, "_h_project"),
        "agent": (None, "_h_agent"),
        "mode": (None, "_h_mode"),
        "loop": (None, "_h_loop"),
        "priority": (None, "_h_priority"),
        "swarm": (None, "_h_swarm"),
        "compact": (None, "_h_compact"),
        "reset": (None, "_h_reset"),
        "cancel": (None, "_h_cancel"),
        "new": (None, "_h_new"),
        "personality": (None, "_h_personality"),
        "broadcast": (None, "_h_broadcast"),
        "model": (None, "_h_model"),
        "vault": ("datastore", "vault"),
        "blueprint": ("datastore", "blueprint"),
        "help": ("formatter", "help"),
        "approve": (None, "_h_approve"),
        "deny": (None, "_h_deny"),
        "commit": ("git", "commit"),
        "pr": ("git", "pr"),
        "merge": ("git", "merge"),
        "files": ("formatter", "files"),
        "eval": (None, "_h_eval"),
        "budget": (None, "_h_budget"),
    }

    def __init__(
        self,
        process_manager: ProcessManager,
        default_maestro: str = "otter",
        token_store: TokenStore | None = None,
        task_store: TaskStore | None = None,
        audit_log: AuditLog | None = None,
        vault_store: VaultStore | None = None,
        mode_request_store: ModeRequestStore | None = None,
        blueprint_store: BlueprintStore | None = None,
        attachment_store: AttachmentStore | None = None,
        scheduler: PriorityScheduler | None = None,
        personalities_dir: Path | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.default_maestro = default_maestro
        self.token_store = token_store
        self.task_store = task_store
        self.audit_log = audit_log
        self.vault_store = vault_store
        self.mode_request_store = mode_request_store
        self.blueprint_store = blueprint_store
        self.attachment_store = attachment_store
        self.scheduler = scheduler
        self.personalities_dir = personalities_dir or Path("personalities")
        self._pending_new: dict[str, _PendingNewMaestro] = {}

        # Collaborator groups (ADR 0006). Read-only views go to the Formatter,
        # which takes only the read-only stores — no vault/mode_request/blueprint.
        self.formatter = Formatter(
            self.process_manager,
            token_store=self.token_store,
            audit_log=self.audit_log,
            task_store=self.task_store,
            attachment_store=self.attachment_store,
        )
        self.datastore = DataStoreCommands(
            self.process_manager,
            vault_store=self.vault_store,
            blueprint_store=self.blueprint_store,
        )
        self.git = GitCommands(self.process_manager, audit_log=self.audit_log)

        # Bind the routing table to live handlers once, at construction.
        self._registry: dict[str, Callable[[Command, str], Awaitable[CommandResult]]] = {
            name: getattr(getattr(self, group) if group else self, method)
            for name, (group, method) in self._ROUTES.items()
        }

    async def dispatch(self, text: str, actor: str = "system") -> CommandResult:
        """Parse ``text`` then dispatch — convenience for callers without a Command.

        Honors any pending `/new maestro` flow for the actor: plain text
        advances the flow; a slash command cancels it and is then
        executed normally (`/cancel` reports the abort and stops there).
        """
        pending = self._pending_new.get(actor)
        if pending is not None and self._pending_expired(pending):
            del self._pending_new[actor]
            pending = None

        if pending is not None:
            stripped = text.strip()
            if stripped.startswith("/"):
                cancelled = self._pending_new.pop(actor)
                cmd = parse_command(stripped, default_maestro=self.default_maestro)
                if cmd.name == "cancel":
                    return CommandResult(text=f"Cancelled /new maestro {cancelled.name}.")
                return await self.dispatch_command(cmd, actor=actor)
            return CommandResult(text=await self._advance_new_flow(actor, stripped))

        cmd = parse_command(text, default_maestro=self.default_maestro)
        return await self.dispatch_command(cmd, actor=actor)

    @staticmethod
    def _pending_expired(pending: _PendingNewMaestro) -> bool:
        return datetime.now(UTC) - pending.last_active > _PENDING_NEW_TIMEOUT

    async def dispatch_command(self, cmd: Command, actor: str = "system") -> CommandResult:
        """Execute a parsed command and return a :class:`CommandResult`."""
        handler = self._registry.get(cmd.name)
        if handler is None:
            return CommandResult(text=f"Unknown command: /{cmd.name}")
        return await handler(cmd, actor)

    # ------------------------------------------------------------------
    # Registry handlers — uniform ``async (cmd, actor) -> CommandResult``.
    # Each wraps a ``_format_*`` / ``_execute_*`` body (or inline logic) and
    # is bound into ``self._registry`` via ``_ROUTES``. Order matches _ROUTES.
    # ------------------------------------------------------------------

    async def _h_empty(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text="")

    async def _h_kill(self, cmd: Command, actor: str) -> CommandResult:
        if not cmd.target:
            return CommandResult(text="Usage: /kill <entity_name>")
        try:
            await self.process_manager.kill_entity(cmd.target)
            return CommandResult(text=f"Killed {cmd.target}.")
        except Exception as e:
            return CommandResult(text=f"Error killing {cmd.target}: {e}")

    async def _h_message(self, cmd: Command, actor: str) -> CommandResult:
        if not cmd.target:
            return CommandResult(text="No target specified.")
        return CommandResult(
            text=await self._send_to_entity(cmd.target, cmd.args),
            routed=True,
            entity=cmd.target or "",
        )

    async def _h_task(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_task(cmd.target, cmd.args, actor=actor))

    async def _h_team(self, cmd: Command, actor: str) -> CommandResult:
        # /t:dev.backend <msg> routes to the lead entity directly;
        # /team create|list|kill is a structural subcommand.
        if cmd.target and "." in (cmd.target or ""):
            return CommandResult(
                text=await self._send_to_entity(cmd.target, cmd.args),
                routed=True,
                entity=cmd.target or "",
            )
        return CommandResult(text=await self._execute_team(cmd.target, cmd.args))

    async def _h_project(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_project(cmd.target, cmd.args))

    async def _h_agent(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(
            text=await self._send_to_entity(cmd.target or "", cmd.args),
            routed=True,
            entity=cmd.target or "",
        )

    async def _h_mode(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_mode(cmd.target, cmd.args))

    async def _h_loop(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_loop(cmd.target, cmd.args))

    async def _h_priority(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_priority(cmd.target, cmd.args, actor=actor))

    async def _h_swarm(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_swarm(cmd.target, cmd.args))

    async def _h_compact(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_compact(cmd.target))

    async def _h_reset(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_reset(cmd.target))

    async def _h_cancel(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text="Nothing to cancel.")

    async def _h_new(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_new(cmd.target, cmd.args, actor=actor))

    async def _h_personality(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_personality(cmd.target, cmd.args))

    async def _h_broadcast(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_broadcast(cmd.args))

    async def _h_model(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_model(cmd.target, cmd.args))

    async def _h_approve(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_approve(cmd.target, cmd.args))

    async def _h_deny(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_deny(cmd.target, cmd.args))

    async def _h_eval(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_eval(cmd.target))

    async def _h_budget(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_budget(cmd.target))

    # ------------------------------------------------------------------
    # Per-command execution helpers (extracted from TelegramBridge)
    # ------------------------------------------------------------------

    async def _execute_approve(self, subcommand: str | None, args: str) -> str:
        """Handle /approve mode <id> — approve a pending mode-elevation request.

        With no subcommand, lists pending requests addressed to the user.
        """
        if self.mode_request_store is None:
            return "Mode-request store not configured."

        if not subcommand:
            pending = await self.mode_request_store.list_pending("user")
            if not pending:
                return "No pending mode requests."
            lines = ["Pending mode requests:"]
            for r in pending:
                reason = r.get("reason") or "(no reason)"
                lines.append(f"  #{r['id']} {r['requester']} -> {r['requested_mode']} ({reason})")
            return "\n".join(lines)

        sub = subcommand.lower()
        if sub == "gate":
            parts = args.split()
            req_id = _parse_task_id(parts[0]) if parts else None
            if req_id is None:
                return "Usage: /approve gate <id> [option]"
            # Optional option index for an AskUserQuestion gate (Ticket 003 #23).
            # Plan gates are a binary approve, so the option is omitted there.
            chosen_option: int | None = None
            if len(parts) > 1:
                try:
                    chosen_option = int(parts[1])
                except ValueError:
                    return "Usage: /approve gate <id> [option]"
            row = await self.process_manager.approve_gate(req_id, chosen_option=chosen_option)
            if row is None:
                return f"Gate #{req_id} not found or already resolved."
            return f"Approved gate #{row['id']}: {row['requester']} resumes its turn."
        if sub != "mode":
            return "Usage: /approve mode <id>"
        req_id = _parse_task_id(args)
        if req_id is None:
            return "Usage: /approve mode <id>"
        row = await self.process_manager.approve_mode_request(req_id)
        if row is None:
            return f"Request #{req_id} not found or already resolved."
        return (
            f"Approved #{row['id']}: {row['requester']} -> {row['requested_mode']}. "
            f"Entity will use the elevated mode on its next spawn."
        )

    async def _execute_deny(self, subcommand: str | None, args: str) -> str:
        """Handle /deny mode <id> [reason] — deny a pending mode request."""
        if self.mode_request_store is None:
            return "Mode-request store not configured."

        if not subcommand:
            return "Usage: /deny mode <id> [reason]"

        sub = subcommand.lower()
        if sub == "gate":
            parts = args.strip().split(None, 1)
            if not parts:
                return "Usage: /deny gate <id> [reason]"
            try:
                req_id = int(parts[0])
            except ValueError:
                return "Usage: /deny gate <id> [reason]"
            reason = parts[1].strip() if len(parts) > 1 else None
            row = await self.process_manager.deny_gate(req_id, reason=reason)
            if row is None:
                return f"Gate #{req_id} not found or already resolved."
            return f"Denied gate #{row['id']}: {row['requester']} keeps planning."
        if sub != "mode":
            return "Usage: /deny mode <id> [reason]"

        parts = args.strip().split(None, 1)
        if not parts:
            return "Usage: /deny mode <id> [reason]"
        try:
            req_id = int(parts[0])
        except ValueError:
            return "Usage: /deny mode <id> [reason]"
        reason = parts[1].strip() if len(parts) > 1 else None

        row = await self.process_manager.deny_mode_request(req_id, reason=reason)
        if row is None:
            return f"Request #{req_id} not found or already resolved."
        return f"Denied #{row['id']}: {row['requester']} -> {row['requested_mode']}."

    async def _execute_task(
        self,
        subcommand: str | None,
        args: str,
        actor: str = "system",
    ) -> str:
        """Dispatch a /task subcommand (add | done | cancel)."""
        if self.task_store is None:
            return "Task tracking not configured."

        if not subcommand:
            return "Usage: /task add <title> | /task done <id> | /task cancel <id>"

        sub = subcommand.lower()

        if sub == "add":
            title = _strip_quotes(args).strip()
            if not title:
                return 'Usage: /task add "title"'
            task = await self.task_store.create(title=title, created_by=actor)
            if self.audit_log is not None:
                await self.audit_log.record(
                    actor=actor,
                    action="task.create",
                    target=str(task.id),
                    details={"title": task.title},
                )
            return f"Task #{task.id} added: {task.title}"

        if sub in ("done", "cancel"):
            task_id = _parse_task_id(args)
            if task_id is None:
                return f"Usage: /task {sub} <id>"
            existing = await self.task_store.get(task_id)
            if existing is None:
                return f"Task #{task_id} not found."
            new_status = TaskStatus.COMPLETED if sub == "done" else TaskStatus.CANCELLED
            await self.task_store.update_status(task_id, new_status)
            if self.audit_log is not None:
                await self.audit_log.record(
                    actor=actor,
                    action="task.update_status",
                    target=str(task_id),
                    details={"status": new_status.value},
                )
            return f"Task #{task_id} {new_status.value}."

        return f"Unknown task subcommand: {subcommand}"

    async def _execute_eval(self, maestro_name: str | None) -> str:
        """Handle /eval [maestro] — fire one scheduler tick for a single maestro.

        Without a target, defaults to ``self.default_maestro``. Returns
        the facts prompt the maestro just received so the user can see
        what input drove the autonomous decisions.
        """
        if self.scheduler is None:
            return "Scheduler not configured."
        target = (maestro_name or self.default_maestro).strip()
        if target not in self.process_manager.entities:
            return f"Maestro {target!r} not found."
        try:
            facts = await self.scheduler.run_once_for(target)
        except Exception as e:
            logger.exception("eval failed for %s", target)
            return f"Error: {e}"
        return f"Eval fired for {target}.\n\nFacts sent:\n{facts}"

    async def _execute_budget(self, maestro_name: str | None) -> str:
        """Handle /budget [maestro] — print the facts prompt without sending.

        Debug aid so the user can see exactly what the scheduler would
        feed the maestro. Does not consume a spawn-budget slot or trigger
        the maestro.
        """
        if self.scheduler is None:
            return "Scheduler not configured."
        target = (maestro_name or self.default_maestro).strip()
        if target not in self.process_manager.entities:
            return f"Maestro {target!r} not found."
        facts = await self.scheduler.build_facts_prompt(target)
        return facts

    async def _send_to_entity(self, entity_name: str, message: str) -> str:
        """Send a message to an entity and return its response."""
        if not message:
            return f"Send what to {entity_name}?"

        try:
            # Ticket 029: this is the user-sourced path. If the entity was parked
            # waiting on a decision from the user, this reply unparks it (cleared
            # before the turn runs, so a re-ask within the turn can re-arm it).
            await self.process_manager.clear_awaiting_decision(entity_name)
            response = await self.process_manager.send_to_entity(entity_name, message)
            await self.process_manager.router.route("user", entity_name, message)
            await self.process_manager.router.route(entity_name, "user", response)

            routed = self.process_manager._last_routed_actions
            if routed:
                response += f"\n\n--- Sent message to: {', '.join(routed)}"

            return response or "(no response)"
        except KeyError:
            return f"Entity {entity_name!r} not found. Use /maestros to see available entities."
        except Exception as e:
            logger.exception("Error sending to %s", entity_name)
            return f"Error: {e}"

    async def _execute_team(self, subcommand: str | None, args: str) -> str:
        """Dispatch a /team subcommand (create | list | kill)."""
        if not subcommand:
            return "Usage: /team create <name> | /team list | /team kill <name>"

        sub = subcommand.lower()

        if sub == "create":
            name = args.strip()
            if not name:
                return "Usage: /team create <name>"
            try:
                lead = await self.process_manager.create_team(self.default_maestro, name)
                return f"Team {name!r} created. Lead: {lead.name}"
            except (KeyError, TypeError, ValueError) as e:
                return f"Error: {e}"

        if sub == "list":
            return self.formatter._format_teams()

        if sub == "kill":
            name = args.strip()
            if not name:
                return "Usage: /team kill <name>"
            await self.process_manager.kill_team(self.default_maestro, name)
            return f"Team {name!r} killed."

        return f"Unknown team subcommand: {subcommand}"

    async def _execute_project(self, subcommand: str | None, args: str) -> str:
        """Handle /project new|assign|list — the ownership registry (Ticket 024)."""
        store = self.process_manager.project_store
        if store is None:
            return "Project registry unavailable."
        sub = (subcommand or "list").lower()

        if sub == "list":
            projects = await store.all()
            if not projects:
                return "No projects registered."
            lines = [
                f"- {p.name} → {p.root_path} "
                f"({'owner: ' + p.owning_maestro if p.owning_maestro else 'ownerless'})"
                for p in projects
            ]
            return "Projects:\n" + "\n".join(lines)

        if sub == "new":
            parts = args.split()
            if len(parts) < 2:
                return "Usage: /project new <name> <path> [maestro]"
            name, path = parts[0], parts[1]
            maestro = parts[2] if len(parts) > 2 else None
            await store.upsert(Project(name=name, root_path=Path(path)))
            if maestro:
                try:
                    await store.assign(name, maestro)
                except ProjectOwnershipError as e:
                    return f"Project {name!r} created, but assign failed: {e}"
                return f"Project {name!r} created at {path}, assigned to {maestro!r}."
            return f"Project {name!r} created at {path} (ownerless)."

        if sub == "assign":
            parts = args.split()
            if len(parts) < 2:
                return "Usage: /project assign <name> <maestro>"
            name, maestro = parts[0], parts[1]
            try:
                await store.assign(name, maestro)
            except ProjectOwnershipError as e:
                return f"Error: {e}"
            return f"Project {name!r} assigned to {maestro!r}."

        return f"Unknown project subcommand: {subcommand}"

    async def _execute_mode(self, mode_name: str | None, entity_name: str) -> str:
        """Handle /mode <plan|edit|auto|yolo|yotree> [entity].

        The user has root authority, so /mode from the user surface is
        applied directly — no approval round-trip. Agents wanting to
        elevate themselves emit a <hive_actions> request_mode_change
        which routes through ProcessManager.request_mode_change and
        surfaces as an approval row.
        """
        if not mode_name:
            return "Usage: /mode <plan|edit|auto|yolo|yotree> [entity]"

        target = entity_name.strip() if entity_name else self.default_maestro
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        try:
            entity.set_permission_mode(mode_name)
        except ValueError as e:
            return str(e)

        await self.process_manager._persist(entity)
        if entity.permission_mode in {"yolo", "yotree"}:
            return f"Mode for {target} set to {mode_name!r} (CLI: --dangerously-skip-permissions)"
        return (
            f"Mode for {target} set to {mode_name!r} "
            f"(CLI: --permission-mode {entity.permission_mode})"
        )

    async def _execute_loop(self, loop_name: str | None, entity_name: str) -> str:
        """Handle /loop <ralph|ship-it|plan-act-observe|build-test-refine> [entity]."""
        if not loop_name:
            return "Usage: /loop <ralph|ship-it|plan-act-observe|build-test-refine> [entity]"

        target = entity_name.strip() if entity_name else self.default_maestro
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        try:
            entity.set_loop_mode(loop_name)
        except ValueError as e:
            return str(e)

        await self.process_manager._persist(entity)
        return f"Loop for {target} set to {loop_name!r}."

    async def _execute_priority(
        self, priority_str: str | None, args: str, actor: str = "system"
    ) -> str:
        """Handle /priority P0 'fix prod bug' — create a high-priority task."""
        if self.task_store is None:
            return "Task tracking not configured."

        if not priority_str:
            return "Usage: /priority <P0-P4> <task title>"

        cleaned = priority_str.upper().lstrip("P")
        try:
            priority = int(cleaned)
        except ValueError:
            return f"Invalid priority: {priority_str!r}. Use P0-P4."
        if priority < 0 or priority > 4:
            return f"Priority must be P0-P4, got P{priority}."

        title = _strip_quotes(args).strip()
        if not title:
            return 'Usage: /priority P0 "task title"'

        task = await self.task_store.create(title=title, created_by=actor, priority=priority)
        if self.audit_log is not None:
            await self.audit_log.record(
                actor=actor,
                action="task.create",
                target=str(task.id),
                details={"title": title, "priority": priority},
            )
        return f"Task #{task.id} added at P{priority}: {title}"

    async def _execute_swarm(self, team_name: str | None, goal: str) -> str:
        """Handle /swarm <team> <goal> — send goal to all workers in a team."""
        if not team_name:
            return "Usage: /swarm <team> <goal>"
        if not goal:
            return "Usage: /swarm <team> <goal>"

        maestro = self.process_manager.entities.get(self.default_maestro)
        if not isinstance(maestro, Maestro):
            return "Default maestro not found."

        team = maestro.get_team(team_name)
        if team is None:
            return f"Team {team_name!r} not found."

        if not team.workers:
            return f"Team {team_name!r} has no workers."

        results = []
        for worker_name in team.workers:
            try:
                response = await self.process_manager.send_to_entity(worker_name, goal)
                results.append(f"{worker_name}: {response[:100]}")
            except Exception as e:
                results.append(f"{worker_name}: Error — {e}")

        return f"Swarm ({len(results)} workers):\n" + "\n".join(results)

    async def _execute_new(self, entity_type: str | None, args: str, actor: str = "system") -> str:
        """Handle /new maestro <name> [model].

        With a personality file already at ``personalities_dir/<name>.md``,
        register the maestro immediately. Otherwise kick off an
        interactive Q&A keyed by ``actor`` — subsequent plain-text
        messages from the same actor are interpreted as answers.
        """
        if not entity_type or entity_type.lower() != "maestro":
            return "Usage: /new maestro <name> [model]"

        parts = args.strip().split(None, 1)
        if not parts:
            return "Usage: /new maestro <name> [model]"

        name = parts[0]
        model = parts[1] if len(parts) > 1 else "opus"

        path = self.personalities_dir / f"{name}.md"
        if path.exists():
            try:
                maestro = await self.process_manager.register_maestro(
                    name, model=model, personality_path=path
                )
                return f"Maestro {maestro.name!r} registered (model={maestro.model})."
            except (ValueError, RuntimeError) as e:
                return f"Error: {e}"

        self._pending_new[actor] = _PendingNewMaestro(name=name, model=model)
        return _NEW_MAESTRO_QUESTIONS[0]

    async def _advance_new_flow(self, actor: str, answer: str) -> str:
        """Record one answer; either ask the next question or finalize."""
        pending = self._pending_new[actor]
        pending.answers.append(answer)
        pending.last_active = datetime.now(UTC)

        if len(pending.answers) < len(_NEW_MAESTRO_QUESTIONS):
            return _NEW_MAESTRO_QUESTIONS[len(pending.answers)]

        del self._pending_new[actor]
        return await self._finalize_new_maestro(pending)

    async def _finalize_new_maestro(self, pending: _PendingNewMaestro) -> str:
        """Render the personality MD, write it, and register the maestro."""
        purpose, style = pending.answers
        md = _render_personality_md(
            name=pending.name, purpose=purpose, style=style, model=pending.model
        )
        self.personalities_dir.mkdir(parents=True, exist_ok=True)
        path = self.personalities_dir / f"{pending.name}.md"
        path.write_text(md)
        try:
            maestro = await self.process_manager.register_maestro(
                pending.name, model=pending.model, personality_path=path
            )
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        return f"Maestro {maestro.name!r} registered (model={pending.model})."

    async def _execute_personality(self, subcommand: str | None, args: str) -> str:
        """Handle /personality reload <entity>."""
        if not subcommand or subcommand.lower() != "reload":
            return "Usage: /personality reload <entity>"

        entity_name = args.strip() or self.default_maestro
        entity = self.process_manager.entities.get(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."

        config = entity.load_personality()
        if config is None:
            return f"No personality file for {entity_name!r}."

        await self.process_manager._persist(entity)
        return f"Reloaded personality for {entity_name}."

    async def _execute_broadcast(self, message: str) -> str:
        """Handle /broadcast <message> — send to all entities."""
        if not message.strip():
            return "Usage: /broadcast <message>"

        entities = self.process_manager.entities
        if not entities:
            return "No entities to broadcast to."

        results = []
        for name in entities:
            try:
                response = await self.process_manager.send_to_entity(name, message)
                results.append(f"{name}: {response[:80]}")
            except Exception as e:
                results.append(f"{name}: Error — {e}")

        return f"Broadcast to {len(results)} entities:\n" + "\n".join(results)

    async def _execute_model(self, model_name: str | None, entity_name: str) -> str:
        """Handle /model <opus|sonnet|haiku|opusplan> [entity]."""
        valid_models = {"opus", "sonnet", "haiku", "opusplan"}
        if not model_name or model_name not in valid_models:
            return f"Usage: /model <{'|'.join(sorted(valid_models))}> [entity]"

        target = entity_name.strip() if entity_name else self.default_maestro
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        entity.model = model_name
        await self.process_manager._persist(entity)
        return f"Model for {target} set to {model_name!r}."

    async def _execute_compact(self, entity_name: str | None) -> str:
        """Handle /compact <entity> — delegate to ProcessManager.compact_entity()."""
        if not entity_name:
            return "Usage: /compact <entity>"
        try:
            summary = await self.process_manager.compact_entity(entity_name)
            return f"Compacted {entity_name}. Summary:\n{summary}"
        except KeyError:
            return f"Entity {entity_name!r} not found."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error compacting {entity_name}: {e}"

    async def _execute_reset(self, entity_name: str | None) -> str:
        """Handle /reset <entity> — kill entity, clear session, ready for fresh start."""
        if not entity_name:
            return "Usage: /reset <entity>"

        entity = self.process_manager.entities.get(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."

        await self.process_manager.kill_entity(entity_name)

        self.process_manager._entities[entity_name] = entity
        self.process_manager.router.register(entity_name)
        entity.session_id = None
        entity.state = EntityState.IDLE
        await self.process_manager._persist(entity)

        return f"Reset {entity_name}. Session cleared, ready for fresh start."


# Derived single source of truth: every command the dispatcher executes.
# ``empty`` is a parser artifact (blank input), not a user-facing command, so
# it is excluded. The Telegram bridge re-exports this (plus surface-only
# commands like /heartbeat) as ``BRIDGE_COMMANDS`` for the /help drift guard.
KNOWN_COMMANDS: frozenset[str] = frozenset(CommandDispatcher._ROUTES) - {"empty"}


# ---------------------------------------------------------------------- #
# Module-level helpers                                                    #
# ---------------------------------------------------------------------- #


def _render_personality_md(name: str, purpose: str, style: str, model: str) -> str:
    """Render a maestro personality markdown file from interactive answers.

    Templated (deterministic) so the output is stable and testable; LLM
    authoring is a Phase 2.5 follow-up. Format mirrors the existing
    `personalities/_template.md` so :func:`parse_personality` reads it.
    """
    title = name[:1].upper() + name[1:] if name else name
    return (
        f"# Maestro: {title}\n\n"
        "## Identity\n"
        f"- **Name**: {name}\n"
        "- **Role**: maestro\n"
        f"- **Model**: {model}\n\n"
        "## System Prompt\n"
        f"{title} is a maestro for: {purpose}.\n"
        f"Communication style: {style}.\n"
        "Plain English, short sentences. Delegate eagerly and form\n"
        "small focused teams rather than overloading one entity.\n"
        "Report failures honestly — never narrate fictional success.\n\n"
        "## Tools\n"
        "- allowedTools: Bash Read Write Edit Grep Glob\n\n"
        "## Constraints\n"
        "- Ask for clarification rather than guessing on ambiguous requirements.\n"
        "- Report errors honestly; do not hide failures.\n\n"
        "## Permission modes\n"
        "- Default mode is `edit` — safe for prompts and most code edits.\n"
        "- Prefer `yotree` (elevated + sandboxed worktree) for code-heavy work.\n"
        "- Use `yolo` only for trivial scripted tasks where a worktree is overhead.\n"
    )
