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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hive.config import ALLOW_AUTO_MERGE
from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus
from hive.models.worker import WorkerAgent
from hive.process import git_ops
from hive.telegram.commands import Command, parse_command
from hive.telegram.help_text import format_all, format_one

if TYPE_CHECKING:
    from hive.bus.attachment_store import AttachmentStore
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.knowledge.blueprints import BlueprintStore
    from hive.models.task import Task
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


# Every command the dispatcher executes. The Telegram bridge re-exports
# this (plus its surface-only commands like /heartbeat) as
# ``BRIDGE_COMMANDS`` for the /help drift guard. Keep in sync with the
# if-chain in :meth:`CommandDispatcher.dispatch_command` below.
KNOWN_COMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "health",
        "maestros",
        "org",
        "comms",
        "kill",
        "message",
        "cost",
        "task",
        "tasks",
        "audit",
        "team",
        "teams",
        "worker",
        "agent",
        "mode",
        "loop",
        "priority",
        "swarm",
        "compact",
        "reset",
        "new",
        "cancel",
        "personality",
        "broadcast",
        "model",
        "vault",
        "blueprint",
        "help",
        "approve",
        "deny",
        "commit",
        "pr",
        "merge",
        "files",
        "eval",
        "budget",
        "quota",
    }
)


@dataclass
class CommandResult:
    """Outcome of executing a command — plain text plus optional metadata.

    Surfaces read ``text`` for display. ``metadata`` is a free-form dict
    for transport-specific hints (e.g. a future web UI rendering
    structured data instead of a string). ``routed`` is True when the
    dispatcher already persisted the round-trip through the bus router,
    so transport layers must not log it again.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    routed: bool = False
    entity: str = ""


class CommandDispatcher:
    """Executes parsed commands against ProcessManager + stores.

    Stateless w.r.t. transport — both the Telegram bridge and the web
    write endpoints construct one of these and call :meth:`dispatch` (or
    :meth:`dispatch_command` if they already parsed the input).
    """

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
        if cmd.name == "empty":
            return CommandResult(text="")

        if cmd.name == "status":
            return CommandResult(text=self._format_status())

        if cmd.name == "health":
            unhealthy = await self.process_manager.health_check()
            if unhealthy:
                return CommandResult(text=f"Unhealthy entities: {', '.join(unhealthy)}")
            return CommandResult(text="All entities healthy.")

        if cmd.name == "maestros":
            entities = self.process_manager.entities
            maestros = [e for e in entities.values() if e.role == "maestro"]
            if not maestros:
                return CommandResult(text="No maestros running.")
            lines = [f"- {m.name} ({m.state.value}, model={m.model})" for m in maestros]
            return CommandResult(text="Maestros:\n" + "\n".join(lines))

        if cmd.name == "org":
            return CommandResult(text=self._format_org())

        if cmd.name == "comms":
            recent = await self.process_manager.router.store.get_recent(limit=10)
            if not recent:
                return CommandResult(text="No messages yet.")
            lines = []
            for msg in reversed(recent):
                lines.append(f"[{msg['sender']} -> {msg['recipient']}] {msg['content'][:80]}")
            return CommandResult(text="Recent comms:\n" + "\n".join(lines))

        if cmd.name == "kill":
            if not cmd.target:
                return CommandResult(text="Usage: /kill <entity_name>")
            try:
                await self.process_manager.kill_entity(cmd.target)
                return CommandResult(text=f"Killed {cmd.target}.")
            except Exception as e:
                return CommandResult(text=f"Error killing {cmd.target}: {e}")

        if cmd.name == "message":
            if not cmd.target:
                return CommandResult(text="No target specified.")
            return CommandResult(
                text=await self._send_to_entity(cmd.target, cmd.args),
                routed=True,
                entity=cmd.target or "",
            )

        if cmd.name == "cost":
            return CommandResult(text=await self._format_cost(cmd.args))

        if cmd.name == "quota":
            return CommandResult(text=self._format_quota())

        if cmd.name == "task":
            return CommandResult(text=await self._execute_task(cmd.target, cmd.args, actor=actor))

        if cmd.name == "tasks":
            return CommandResult(text=await self._format_tasks_list())

        if cmd.name == "audit":
            return CommandResult(text=await self._format_audit(cmd.args))

        if cmd.name == "team":
            # /t:dev.backend <msg> routes to the lead entity directly;
            # /team create|list|kill is a structural subcommand.
            if cmd.target and "." in (cmd.target or ""):
                return CommandResult(
                    text=await self._send_to_entity(cmd.target, cmd.args),
                    routed=True,
                    entity=cmd.target or "",
                )
            return CommandResult(text=await self._execute_team(cmd.target, cmd.args))

        if cmd.name == "teams":
            return CommandResult(text=self._format_teams())

        if cmd.name == "worker":
            return CommandResult(text=await self._execute_worker(cmd.target, cmd.args))

        if cmd.name == "agent":
            return CommandResult(
                text=await self._send_to_entity(cmd.target or "", cmd.args),
                routed=True,
                entity=cmd.target or "",
            )

        if cmd.name == "mode":
            return CommandResult(text=await self._execute_mode(cmd.target, cmd.args))

        if cmd.name == "loop":
            return CommandResult(text=await self._execute_loop(cmd.target, cmd.args))

        if cmd.name == "priority":
            return CommandResult(
                text=await self._execute_priority(cmd.target, cmd.args, actor=actor)
            )

        if cmd.name == "swarm":
            return CommandResult(text=await self._execute_swarm(cmd.target, cmd.args))

        if cmd.name == "compact":
            return CommandResult(text=await self._execute_compact(cmd.target))

        if cmd.name == "reset":
            return CommandResult(text=await self._execute_reset(cmd.target))

        if cmd.name == "cancel":
            return CommandResult(text="Nothing to cancel.")

        if cmd.name == "new":
            return CommandResult(text=await self._execute_new(cmd.target, cmd.args, actor=actor))

        if cmd.name == "personality":
            return CommandResult(text=await self._execute_personality(cmd.target, cmd.args))

        if cmd.name == "broadcast":
            return CommandResult(text=await self._execute_broadcast(cmd.args))

        if cmd.name == "model":
            return CommandResult(text=await self._execute_model(cmd.target, cmd.args))

        if cmd.name == "vault":
            return CommandResult(text=await self._execute_vault(cmd.target, cmd.args))

        if cmd.name == "blueprint":
            return CommandResult(text=await self._execute_blueprint(cmd.target, cmd.args))

        if cmd.name == "help":
            return CommandResult(text=self._execute_help(cmd.target))

        if cmd.name == "approve":
            return CommandResult(text=await self._execute_approve(cmd.target, cmd.args))

        if cmd.name == "deny":
            return CommandResult(text=await self._execute_deny(cmd.target, cmd.args))

        if cmd.name == "commit":
            return CommandResult(text=await self._execute_commit(cmd.target, cmd.args))

        if cmd.name == "pr":
            return CommandResult(text=await self._execute_pr(cmd.target, cmd.args))

        if cmd.name == "merge":
            return CommandResult(text=await self._execute_merge(cmd.target))

        if cmd.name == "files":
            return CommandResult(text=await self._execute_files(cmd.args))

        if cmd.name == "eval":
            return CommandResult(text=await self._execute_eval(cmd.target))

        if cmd.name == "budget":
            return CommandResult(text=await self._execute_budget(cmd.target))

        return CommandResult(text=f"Unknown command: /{cmd.name}")

    # ------------------------------------------------------------------
    # Per-command execution helpers (extracted from TelegramBridge)
    # ------------------------------------------------------------------

    def _execute_help(self, name: str | None) -> str:
        """Format a /help response — grouped listing or per-command detail."""
        if name:
            return format_one(name)
        return format_all()

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

    def _format_quota(self) -> str:
        """Render the on-demand /quota response using the manager's monitor."""
        from hive.config import HIVE_QUOTA_POLL_SECONDS
        from hive.runtime.quota_monitor import format_quota_text

        monitor = self.process_manager.quota_monitor
        reading = monitor.get_quota() if monitor is not None else None
        return format_quota_text(
            reading,
            now=datetime.now(UTC),
            stale_after_seconds=HIVE_QUOTA_POLL_SECONDS * 2,
        )

    async def _format_cost(self, args: str) -> str:
        """Format a /cost report over an optional time window (default 24h)."""
        if self.token_store is None:
            return "Token tracking not configured."

        window = _parse_window(args)
        since = datetime.now(UTC) - window.delta
        totals = await self.token_store.totals(since=since)

        calls = int(totals.get("call_count", 0))
        if calls == 0:
            return f"No token usage in the last {window.label}."

        in_tok = int(totals.get("input_tokens", 0))
        out_tok = int(totals.get("output_tokens", 0))
        cache_create = int(totals.get("cache_creation_input_tokens", 0))
        cache_read = int(totals.get("cache_read_input_tokens", 0))
        cost = float(totals.get("cost_usd", 0) or 0)

        return (
            f"Tokens (last {window.label}, {calls} call(s)):\n"
            f"  input:  {in_tok:,}\n"
            f"  output: {out_tok:,}\n"
            f"  cache create: {cache_create:,}\n"
            f"  cache read:   {cache_read:,}\n"
            f"  ${cost:.4f} equivalent API cost (covered by Max subscription)"
        )

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

    async def _format_audit(self, args: str) -> str:
        """Format a /audit report — the last N events, optionally prefix-filtered."""
        if self.audit_log is None:
            return "Audit log not configured."

        prefix, limit = _parse_audit_args(args)
        events = await self.audit_log.recent(limit=limit, action_prefix=prefix)
        if not events:
            scope = f"{prefix}*" if prefix else "all"
            return f"No audit events ({scope}, limit {limit})."

        lines = [_format_audit_row(event) for event in events]
        header = f"Audit (last {len(events)}):"
        return header + "\n" + "\n".join(lines)

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

    async def _execute_files(self, args: str) -> str:
        """Handle /files [N] — list the most recent uploads (default 20, max 100)."""
        if self.attachment_store is None:
            return "Attachments not configured."

        limit = 20
        raw = (args or "").strip()
        if raw:
            try:
                limit = int(raw.split()[0])
            except ValueError:
                return "Usage: /files [N]"
            if limit < 1:
                return "Usage: /files [N] — N must be >= 1."
            limit = min(limit, 100)

        rows = await self.attachment_store.list_recent(limit=limit)
        if not rows:
            return "No attachments yet."

        lines = [f"Recent attachments (last {len(rows)}):"]
        for r in rows:
            ts = r.created_at.strftime("%Y-%m-%d %H:%M")
            forwarded = r.forwarded_to or "—"
            mime = r.mime_type or "?"
            size = _format_bytes(r.size_bytes)
            name = r.original_name or Path(r.file_path).name
            lines.append(f"  #{r.id} {ts} {r.source} →{forwarded} {mime} {size} {name}")
        return "\n".join(lines)

    async def _format_tasks_list(self) -> str:
        """Format the open (pending + in-progress) tasks for /tasks."""
        if self.task_store is None:
            return "Task tracking not configured."

        pending = await self.task_store.list(status=TaskStatus.PENDING)
        in_progress = await self.task_store.list(status=TaskStatus.IN_PROGRESS)
        open_tasks: list[Task] = pending + in_progress
        if not open_tasks:
            return "No open tasks."

        lines = [_format_task_row(t) for t in open_tasks]
        return "Open tasks:\n" + "\n".join(lines)

    async def _send_to_entity(self, entity_name: str, message: str) -> str:
        """Send a message to an entity and return its response."""
        if not message:
            return f"Send what to {entity_name}?"

        try:
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
            return self._format_teams()

        if sub == "kill":
            name = args.strip()
            if not name:
                return "Usage: /team kill <name>"
            await self.process_manager.kill_team(self.default_maestro, name)
            return f"Team {name!r} killed."

        return f"Unknown team subcommand: {subcommand}"

    async def _execute_worker(self, subcommand: str | None, args: str) -> str:
        """Dispatch a /worker subcommand (spawn | kill)."""
        if not subcommand:
            return "Usage: /worker spawn <team> [name] | /worker kill <name>"

        sub = subcommand.lower()

        if sub == "spawn":
            parts = args.strip().split(None, 1)
            if not parts:
                return "Usage: /worker spawn <team> [name]"
            team_name = parts[0]
            worker_name = parts[1] if len(parts) > 1 else None
            lead_name = f"{self.default_maestro}.{team_name}"
            try:
                worker = await self.process_manager.spawn_worker(lead_name, worker_name)
                return f"Worker {worker.name} spawned."
            except (KeyError, TypeError) as e:
                return f"Error: {e}"

        if sub == "kill":
            name = args.strip()
            if not name:
                return "Usage: /worker kill <name>"
            await self.process_manager.kill_entity(name)
            return f"Worker {name} killed."

        return f"Unknown worker subcommand: {subcommand}"

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

    async def _execute_vault(self, subcommand: str | None, args: str) -> str:
        """Handle /vault approve|deny|status|log."""
        if self.vault_store is None:
            return "Vault store not configured."

        if not subcommand:
            return "Usage: /vault approve|deny|status|log"

        sub = subcommand.lower()

        if sub == "approve":
            action_id = _parse_task_id(args)
            if action_id is None:
                return "Usage: /vault approve <id>"
            result = await self.process_manager.approve_vault_action(action_id)
            if result is None:
                return f"Action #{action_id} not found."
            status = result["status"]
            if status == "completed":
                ref = (
                    (result.get("execution_result") or {}).get("reference")
                    if isinstance(result.get("execution_result"), dict)
                    else None
                )
                tail = f" (ref {ref})" if ref else ""
                return f"Action #{action_id} executed{tail}."
            if status == "failed":
                reason = result.get("denial_reason") or "provider failure"
                return f"Action #{action_id} failed: {reason}"
            if status == "denied":
                reason = result.get("denial_reason") or "denied"
                return f"Action #{action_id} denied: {reason}"
            if status == "approved":
                return f"Action #{action_id} approved."
            return f"Action #{action_id} {status}."

        if sub == "deny":
            action_id = _parse_task_id(args)
            if action_id is None:
                return "Usage: /vault deny <id>"
            reason = None
            parts = args.strip().split(None, 1)
            if len(parts) > 1:
                reason = parts[1].strip() or None
            result = await self.process_manager.deny_vault_action(action_id, reason=reason)
            if result is None:
                return f"Action #{action_id} not found or already resolved."
            return f"Action #{action_id} denied."

        if sub == "status":
            vault_name = args.strip() or "vault"
            pending = await self.vault_store.pending(vault_name)
            if not pending:
                return "No pending vault actions."
            lines = [f"- #{a['id']} [{a['requester']}] {a['description']}" for a in pending]
            return f"Pending actions ({len(pending)}):\n" + "\n".join(lines)

        if sub == "log":
            vault_name = args.strip() or "vault"
            log = await self.vault_store.log(vault_name)
            if not log:
                return "No vault actions recorded."
            lines = [f"- #{a['id']} {a['status']} {a['description'][:50]}" for a in log]
            return f"Vault log ({len(log)}):\n" + "\n".join(lines)

        return f"Unknown vault subcommand: {subcommand}"

    async def _execute_blueprint(self, subcommand: str | None, args: str) -> str:
        """Handle /blueprint save|search|list."""
        if self.blueprint_store is None:
            return "Blueprints not configured."
        if subcommand is None:
            return "Usage: /blueprint save|search|list"

        if subcommand == "save":
            title = _strip_quotes(args)
            if not title:
                return 'Usage: /blueprint save "title" body text'
            parts = title.split("\n", 1)
            bp_title = parts[0]
            bp_body = parts[1] if len(parts) > 1 else bp_title
            bp_id = await self.blueprint_store.save(bp_title, bp_body, [])
            return f"Blueprint #{bp_id} saved: {bp_title}"

        if subcommand == "search":
            query = args.strip()
            if not query:
                return "Usage: /blueprint search <query>"
            results = await self.blueprint_store.search(query)
            if not results:
                return f"No blueprints matching {query!r}."
            lines = [f"Semantic matches for {query!r}:"]
            for r in results:
                dist = r.get("distance", 0.0)
                lines.append(f"  #{r['id']} {r['title']}  (distance={dist:.3f})")
            return "\n".join(lines)

        if subcommand == "list":
            items = await self.blueprint_store.list_all()
            if not items:
                return "No blueprints saved."
            lines = ["All blueprints:"]
            for bp in items[:20]:
                lines.append(f"  #{bp['id']} {bp['title']}")
            return "\n".join(lines)

        return f"Unknown blueprint subcommand: {subcommand}"

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

    def _worktree_for(self, entity_name: str):  # type: ignore[no-untyped-def]
        """Return (entity, worktree_path) or (None, None) if entity has no worktree.

        Kept private and untyped to avoid a public dependency on
        WorkerAgent — /commit and /pr only need the path, not the class.
        """
        entity = self.process_manager.entities.get(entity_name)
        if entity is None:
            return None, None
        worktree = getattr(entity, "worktree_path", None)
        return entity, worktree

    async def _execute_commit(self, entity_name: str | None, args: str) -> str:
        """Handle /commit <entity> "<message>" — stage+commit in entity's worktree."""
        if not entity_name:
            return 'Usage: /commit <entity> "<message>"'
        entity, worktree = self._worktree_for(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."
        if worktree is None:
            return f"Entity {entity_name!r} has no worktree attached."
        message = _strip_quotes(args).strip()
        if not message:
            return 'Usage: /commit <entity> "<message>"'

        ok, summary = await git_ops.commit(worktree, message)
        if not ok:
            return summary
        if self.audit_log is not None:
            await self.audit_log.record(
                actor="user",
                action="git.commit",
                target=entity_name,
                details={"message": message[:200]},
            )
        return f"Committed in {entity_name}:\n{summary}"

    async def _execute_pr(self, entity_name: str | None, args: str) -> str:
        """Handle /pr <entity> ["<title>"] — push branch and open a PR via gh."""
        if not entity_name:
            return 'Usage: /pr <entity> ["<title>"]'
        entity, worktree = self._worktree_for(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."
        if worktree is None:
            return f"Entity {entity_name!r} has no worktree attached."

        branch = await git_ops.current_branch(worktree)
        if not branch:
            return "Cannot determine current branch (detached HEAD?)."

        ok, push_out = await git_ops.push(worktree, branch)
        if not ok:
            return push_out

        title = _strip_quotes(args).strip() or None
        ok, pr_out = await git_ops.gh_pr_create(worktree, title)
        if not ok:
            return pr_out
        if self.audit_log is not None:
            await self.audit_log.record(
                actor="user",
                action="git.pr_create",
                target=entity_name,
                details={"branch": branch, "title": title},
            )
        return f"PR opened from {entity_name} (branch {branch}):\n{pr_out}"

    async def _execute_merge(self, entity_name: str | None) -> str:
        """Handle /merge <entity> — squash-merge the PR for the entity's branch.

        Off by default; requires ``HIVE_ALLOW_AUTO_MERGE=1``. The user
        running the command is the approval authority.
        """
        if not ALLOW_AUTO_MERGE:
            return (
                "merge is disabled. Set HIVE_ALLOW_AUTO_MERGE=1 in the "
                "environment and restart Hive to enable /merge."
            )
        if not entity_name:
            return "Usage: /merge <entity>"
        entity, worktree = self._worktree_for(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."
        if worktree is None:
            return f"Entity {entity_name!r} has no worktree attached."

        ok, output = await git_ops.gh_pr_merge(worktree)
        if not ok:
            return output
        if self.audit_log is not None:
            await self.audit_log.record(
                actor="user",
                action="git.pr_merge",
                target=entity_name,
                details={},
            )
        return f"Merged PR for {entity_name}:\n{output}"

    # ------------------------------------------------------------------
    # Formatting helpers (read-only views of process_manager state)
    # ------------------------------------------------------------------

    def _format_teams(self) -> str:
        """Format all teams across all maestros for /teams output."""
        entities = self.process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        if not maestros:
            return "No maestros registered."

        lines = []
        for m in maestros:
            if not m.teams:
                lines.append(f"{m.name}: no teams")
                continue
            for team_name, team in m.teams.items():
                worker_count = len(team.workers)
                lead_status = "active" if team.lead and team.lead in entities else "none"
                lines.append(f"{m.name}.{team_name}: lead={lead_status}, workers={worker_count}")
        return "Teams:\n" + "\n".join(lines) if lines else "No teams."

    def _format_org(self) -> str:
        """Format a tree view of the organization for /org."""
        entities = self.process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        if not maestros:
            return "No entities running."

        lines = []
        for m in sorted(maestros, key=lambda x: x.name):
            lines.append(f"{m.name} [maestro] {m.state.value}")
            for team_name, team in m.teams.items():
                lines.append(f"  {team_name} [team]")
                if team.lead and team.lead in entities:
                    lead = entities[team.lead]
                    lines.append(f"    {team.lead} [lead] {lead.state.value}")
                for wn in team.workers:
                    if wn in entities:
                        w = entities[wn]
                        task_info = ""
                        if isinstance(w, WorkerAgent) and w.task_id:
                            task_info = f" (task #{w.task_id})"
                        lines.append(f"    {wn} [worker] {w.state.value}{task_info}")
        return "\n".join(lines) if lines else "No entities running."

    def _format_status(self) -> str:
        """Format entity status for display."""
        statuses = self.process_manager.get_status()
        if not statuses:
            return "No entities running."

        lines = []
        for s in statuses:
            uptime = f", uptime={int(s['uptime'])}s" if s["uptime"] else ""
            pid = f", pid={s['pid']}" if s["pid"] else ""
            lines.append(
                f"- {s['name']} [{s['role']}] {s['state']} (model={s['model']}{pid}{uptime})"
            )
        return "Entities:\n" + "\n".join(lines)


# ---------------------------------------------------------------------- #
# Module-level helpers                                                    #
# ---------------------------------------------------------------------- #


class _Window:
    """Time window for /cost queries — a delta + a human label."""

    __slots__ = ("delta", "label")

    def __init__(self, delta: timedelta, label: str) -> None:
        self.delta = delta
        self.label = label


def _parse_window(raw: str) -> _Window:
    """Parse /cost argument into a time window. Defaults to 24h on unrecognised input."""
    raw = (raw or "").strip().lower()
    windows = {
        "24h": _Window(timedelta(hours=24), "24h"),
        "7d": _Window(timedelta(days=7), "7d"),
        "30d": _Window(timedelta(days=30), "30d"),
    }
    return windows.get(raw, windows["24h"])


def _strip_quotes(text: str) -> str:
    """Strip a single matching pair of leading/trailing quotes (if any)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _parse_task_id(raw: str) -> int | None:
    """Extract the first integer token from a /task done|cancel args string."""
    raw = (raw or "").strip()
    if not raw:
        return None
    first = raw.split()[0]
    try:
        return int(first)
    except ValueError:
        return None


def _format_task_row(task: Task) -> str:
    """Render a single task as a one-liner for /tasks output."""
    return f"- #{task.id} [{task.status.value}] p{task.priority} {task.title}"


def _parse_audit_args(raw: str) -> tuple[str | None, int]:
    """Parse /audit args into (prefix, limit).

    Accepts a category prefix (``entity``, ``command``, ``task``) or
    nothing. Limit defaults to 20.
    """
    raw = (raw or "").strip().lower()
    if not raw:
        return None, 20
    known_prefixes = {"entity", "command", "task"}
    if raw in known_prefixes:
        return f"{raw}.", 20
    return None, 20


def _format_audit_row(event: dict) -> str:
    """Render one audit event as a one-liner for /audit output."""
    ts = event["timestamp"]
    actor = event["actor"]
    action = event["action"]
    target = event["target"] or "-"
    return f"{ts:%H:%M:%S} {actor} {action} {target}"


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


def _format_bytes(n: int | None) -> str:
    """Pretty-print a byte count (B/KB/MB/GB) for /files output."""
    if n is None:
        return "?"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    return f"{n / (1024 * 1024 * 1024):.1f}GB"
