"""Telegram bridge — connects Telegram Bot API to the Hive orchestrator."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus
from hive.models.worker import WorkerAgent
from hive.telegram.commands import parse_command

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.models.task import Task
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


class TelegramBridge:
    """Bridges Telegram messages to the Hive orchestrator.

    Uses python-telegram-bot for async Bot API polling.
    Only accepts messages from allowed user IDs.
    """

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: list[int],
        process_manager: ProcessManager,
        default_maestro: str = "dev",
        token_store: TokenStore | None = None,
        task_store: TaskStore | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids
        self.process_manager = process_manager
        self.default_maestro = default_maestro
        self.token_store = token_store
        self.task_store = task_store
        self.audit_log = audit_log
        self._app: Application | None = None

    async def start(self) -> None:
        """Build and start the Telegram bot application."""
        self._app = Application.builder().token(self.bot_token).build()

        # Handle all text messages
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        # Handle commands (anything starting with /)
        self._app.add_handler(MessageHandler(filters.COMMAND, self._handle_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        logger.info("Telegram bridge started, polling for updates")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()  # type: ignore[union-attr]
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bridge stopped")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle an incoming Telegram message."""
        if update.message is None or update.message.text is None:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            logger.warning("Unauthorized message from user %d", user_id)
            return

        text = update.message.text
        logger.info("Received from Telegram (user=%d): %s", user_id, text[:100])

        # Parse command
        cmd = parse_command(text, default_maestro=self.default_maestro)

        # Handle command
        actor = f"user:{user_id}"
        response = await self._execute_command(cmd, actor=actor)

        # Audit every command — over-logging is cheaper to filter later.
        if self.audit_log is not None and cmd.name != "empty":
            await self.audit_log.record(
                actor=actor,
                action=f"command.{cmd.name}",
                target=cmd.target,
                details={"args": (cmd.args or "")[:200]},
            )

        # Send response back
        if response:
            # Telegram messages have a 4096 char limit
            for chunk in _chunk_text(response, 4096):
                await update.message.reply_text(chunk)

    async def _execute_command(self, cmd, actor: str = "system") -> str:
        """Execute a parsed command and return the response text."""
        if cmd.name == "empty":
            return ""

        if cmd.name == "status":
            return self._format_status()

        if cmd.name == "health":
            unhealthy = await self.process_manager.health_check()
            if unhealthy:
                return f"Unhealthy entities: {', '.join(unhealthy)}"
            return "All entities healthy."

        if cmd.name == "maestros":
            entities = self.process_manager.entities
            maestros = [e for e in entities.values() if e.role == "maestro"]
            if not maestros:
                return "No maestros running."
            lines = [f"- {m.name} ({m.state.value}, model={m.model})" for m in maestros]
            return "Maestros:\n" + "\n".join(lines)

        if cmd.name == "org":
            return self._format_org()

        if cmd.name == "comms":
            recent = await self.process_manager.router.store.get_recent(limit=10)
            if not recent:
                return "No messages yet."
            lines = []
            for msg in reversed(recent):
                lines.append(f"[{msg['sender']} -> {msg['recipient']}] {msg['content'][:80]}")
            return "Recent comms:\n" + "\n".join(lines)

        if cmd.name == "kill":
            if not cmd.target:
                return "Usage: /kill <entity_name>"
            try:
                await self.process_manager.kill_entity(cmd.target)
                return f"Killed {cmd.target}."
            except Exception as e:
                return f"Error killing {cmd.target}: {e}"

        if cmd.name == "message":
            if not cmd.target:
                return "No target specified."
            return await self._send_to_entity(cmd.target, cmd.args)

        if cmd.name == "cost":
            return await self._format_cost(cmd.args)

        if cmd.name == "task":
            return await self._execute_task(cmd.target, cmd.args, actor=actor)

        if cmd.name == "tasks":
            return await self._format_tasks_list()

        if cmd.name == "audit":
            return await self._format_audit(cmd.args)

        if cmd.name == "team":
            # /team create|list|kill vs /t:dev.backend <msg>
            if cmd.target and "." in (cmd.target or ""):
                # /t:dev.backend <msg> — route message to the lead entity
                return await self._send_to_entity(cmd.target, cmd.args)
            return await self._execute_team(cmd.target, cmd.args)

        if cmd.name == "teams":
            return self._format_teams()

        if cmd.name == "worker":
            return await self._execute_worker(cmd.target, cmd.args)

        if cmd.name == "agent":
            # /a:dev.backend.w1 <msg> — message a worker directly
            return await self._send_to_entity(cmd.target or "", cmd.args)

        if cmd.name == "mode":
            return await self._execute_mode(cmd.target, cmd.args)

        if cmd.name == "loop":
            return await self._execute_loop(cmd.target, cmd.args)

        if cmd.name == "priority":
            return await self._execute_priority(cmd.target, cmd.args, actor=actor)

        if cmd.name == "swarm":
            return await self._execute_swarm(cmd.target, cmd.args)

        if cmd.name == "compact":
            return await self._execute_compact(cmd.target)

        if cmd.name == "reset":
            return await self._execute_reset(cmd.target)

        if cmd.name == "new":
            return await self._execute_new(cmd.target, cmd.args)

        if cmd.name == "personality":
            return await self._execute_personality(cmd.target, cmd.args)

        if cmd.name == "broadcast":
            return await self._execute_broadcast(cmd.args)

        if cmd.name == "model":
            return await self._execute_model(cmd.target, cmd.args)

        return f"Unknown command: /{cmd.name}"

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
            # Log the exchange
            await self.process_manager.router.route("user", entity_name, message)
            await self.process_manager.router.route(entity_name, "user", response)
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
                lead = await self.process_manager.create_team(
                    self.default_maestro, name
                )
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
                worker = await self.process_manager.spawn_worker(
                    lead_name, worker_name
                )
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
        """Handle /mode <plan|edit|auto> [entity]."""
        if not mode_name:
            return "Usage: /mode <plan|edit|auto> [entity]"

        target = entity_name.strip() if entity_name else self.default_maestro
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        try:
            entity.set_permission_mode(mode_name)
        except ValueError as e:
            return str(e)

        await self.process_manager._persist(entity)
        return (
            f"Mode for {target} set to {mode_name!r} "
            f"(CLI: --permission-mode {entity.permission_mode})"
        )

    async def _execute_loop(self, loop_name: str | None, entity_name: str) -> str:
        """Handle /loop <ralph|yolo|plan-act-observe|build-test-refine> [entity]."""
        if not loop_name:
            return "Usage: /loop <ralph|yolo|plan-act-observe|build-test-refine> [entity]"

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

        # Parse priority level: "P0" -> 0, "P1" -> 1, etc.
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

        task = await self.task_store.create(
            title=title, created_by=actor, priority=priority
        )
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

        # Find the team
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

    async def _execute_new(self, entity_type: str | None, args: str) -> str:
        """Handle /new maestro <name> [model]."""
        if not entity_type or entity_type.lower() != "maestro":
            return "Usage: /new maestro <name> [model]"

        parts = args.strip().split(None, 1)
        if not parts:
            return "Usage: /new maestro <name> [model]"

        name = parts[0]
        model = parts[1] if len(parts) > 1 else "sonnet"

        try:
            maestro = await self.process_manager.register_maestro(
                name, model=model
            )
            return f"Maestro {maestro.name!r} registered (model={model})."
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"

    async def _execute_personality(
        self, subcommand: str | None, args: str
    ) -> str:
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
                response = await self.process_manager.send_to_entity(
                    name, message
                )
                results.append(f"{name}: {response[:80]}")
            except Exception as e:
                results.append(f"{name}: Error — {e}")

        return (
            f"Broadcast to {len(results)} entities:\n"
            + "\n".join(results)
        )

    async def _execute_model(
        self, model_name: str | None, entity_name: str
    ) -> str:
        """Handle /model <opus|sonnet|haiku> [entity]."""
        valid_models = {"opus", "sonnet", "haiku"}
        if not model_name or model_name not in valid_models:
            return (
                f"Usage: /model <{'|'.join(sorted(valid_models))}> [entity]"
            )

        target = entity_name.strip() if entity_name else self.default_maestro
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        entity.model = model_name
        await self.process_manager._persist(entity)
        return f"Model for {target} set to {model_name!r}."

    async def _execute_compact(self, entity_name: str | None) -> str:
        """Handle /compact <entity> — summarize context, reset session with summary."""
        if not entity_name:
            return "Usage: /compact <entity>"

        entity = self.process_manager.entities.get(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."

        if not entity.session_id:
            return f"Entity {entity_name!r} has no active session to compact."

        try:
            summary = await self.process_manager.send_to_entity(
                entity_name,
                "Summarize your entire conversation context in 3 concise bullet points. "
                "Include key decisions, current state, and next steps.",
            )
        except Exception as e:
            return f"Error getting summary: {e}"

        # Kill the entity (clears session_id)
        await self.process_manager.kill_entity(entity_name)

        # Re-register the entity so it can receive messages again
        self.process_manager._entities[entity_name] = entity
        self.process_manager.router.register(entity_name)
        entity.session_id = None
        entity.state = EntityState.IDLE

        # Seed the new session with the summary
        try:
            await self.process_manager.send_to_entity(
                entity_name,
                f"Here is your prior context (compacted):\n{summary}\n\nContinue from here.",
            )
        except Exception as e:
            return f"Compacted but failed to seed new session: {e}"

        return f"Compacted {entity_name}. Summary:\n{summary}"

    async def _execute_reset(self, entity_name: str | None) -> str:
        """Handle /reset <entity> — kill entity, clear session, ready for fresh start."""
        if not entity_name:
            return "Usage: /reset <entity>"

        entity = self.process_manager.entities.get(entity_name)
        if entity is None:
            return f"Entity {entity_name!r} not found."

        await self.process_manager.kill_entity(entity_name)

        # Re-register so it can receive messages again (fresh session)
        self.process_manager._entities[entity_name] = entity
        self.process_manager.router.register(entity_name)
        entity.session_id = None
        entity.state = EntityState.IDLE
        await self.process_manager._persist(entity)

        return f"Reset {entity_name}. Session cleared, ready for fresh start."

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
                lines.append(
                    f"{m.name}.{team_name}: lead={lead_status}, workers={worker_count}"
                )
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


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of max_len characters."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


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

    Accepts: bare, a category prefix (e.g. ``entity``, ``command``, ``task``),
    or nothing. Limit defaults to 20.
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
