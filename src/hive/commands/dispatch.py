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
from typing import TYPE_CHECKING

from hive.commands._helpers import _parse_task_id, _strip_quotes
from hive.commands.datastore_commands import DataStoreCommands
from hive.commands.formatter import Formatter
from hive.commands.result import CommandResult
from hive.models.entity import OFFERED_MODES, EntityState
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

logger = logging.getLogger(__name__)


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
        "kill": (None, "_h_kill"),
        "message": (None, "_h_message"),
        "cost": ("formatter", "cost"),
        "quota": ("formatter", "quota"),
        "task": (None, "_h_task"),
        "tasks": ("formatter", "tasks"),
        "audit": ("formatter", "audit"),
        "mode": (None, "_h_mode"),
        "compact": (None, "_h_compact"),
        "reset": (None, "_h_reset"),
        "model": (None, "_h_model"),
        "vault": ("datastore", "vault"),
        "blueprint": ("datastore", "blueprint"),
        "help": ("formatter", "help"),
        "approve": (None, "_h_approve"),
        "deny": (None, "_h_deny"),
        "files": ("formatter", "files"),
    }

    def __init__(
        self,
        process_manager: ProcessManager,
        token_store: TokenStore | None = None,
        task_store: TaskStore | None = None,
        audit_log: AuditLog | None = None,
        vault_store: VaultStore | None = None,
        mode_request_store: ModeRequestStore | None = None,
        blueprint_store: BlueprintStore | None = None,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.token_store = token_store
        self.task_store = task_store
        self.audit_log = audit_log
        self.vault_store = vault_store
        self.mode_request_store = mode_request_store
        self.blueprint_store = blueprint_store
        self.attachment_store = attachment_store

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

        # Bind the routing table to live handlers once, at construction.
        self._registry: dict[str, Callable[[Command, str], Awaitable[CommandResult]]] = {
            name: getattr(getattr(self, group) if group else self, method)
            for name, (group, method) in self._ROUTES.items()
        }

    async def dispatch(self, text: str, actor: str = "system") -> CommandResult:
        """Parse ``text`` then dispatch — convenience for callers without a Command."""
        cmd = parse_command(text)
        return await self.dispatch_command(cmd, actor=actor)

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
            return CommandResult(
                text="Hive no longer has a chat agent. Use the desk, or /m:<entity> <message>."
            )
        return CommandResult(
            text=await self._send_to_entity(cmd.target, cmd.args),
            routed=True,
            entity=cmd.target or "",
        )

    async def _h_task(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_task(cmd.target, cmd.args, actor=actor))

    async def _h_mode(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_mode(cmd.target, cmd.args))

    async def _h_compact(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_compact(cmd.target))

    async def _h_reset(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_reset(cmd.target))

    async def _h_model(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_model(cmd.target, cmd.args))

    async def _h_approve(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_approve(cmd.target, cmd.args))

    async def _h_deny(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_deny(cmd.target, cmd.args))

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

    async def _send_to_entity(self, entity_name: str, message: str) -> str:
        """Send a message to an entity and return its response."""
        if not message:
            return f"Send what to {entity_name}?"

        try:
            # T007: this is the genuine user/command task path (Telegram and the
            # web decision/message channel both funnel here), so mark it for
            # /goal seeding — the entity's first task becomes its loop goal.
            response = await self.process_manager.send_to_entity(
                entity_name, message, seed_goal=True
            )
            await self.process_manager.router.route("user", entity_name, message)
            await self.process_manager.router.route(entity_name, "user", response)

            routed = self.process_manager._last_routed_actions
            if routed:
                response += f"\n\n--- Sent message to: {', '.join(routed)}"

            return response or "(no response)"
        except KeyError:
            return f"Entity {entity_name!r} not found. Use /status to see available entities."
        except Exception as e:
            logger.exception("Error sending to %s", entity_name)
            return f"Error: {e}"

    async def _execute_mode(self, mode_name: str | None, entity_name: str) -> str:
        """Handle /mode <yolo|yotree> [entity] (T007).

        The offered set is `yolo` / `yotree` only — `edit`/`auto`/`plan` were
        dropped; plan mode is reached via the grill-me skill. The user has root
        authority, so /mode is applied directly — no approval round-trip. Agents
        elevating themselves emit a <hive_actions> request_mode_change instead.
        """
        usage = "Usage: /mode <yolo|yotree> [entity]"
        if not mode_name:
            return usage
        if mode_name not in OFFERED_MODES:
            return f"Unknown mode {mode_name!r}. {usage}"

        target = entity_name.strip()
        if not target:
            return "Name the entity: /mode|/model <value> <entity>."
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        try:
            entity.set_permission_mode(mode_name)
        except ValueError as e:
            return str(e)

        await self.process_manager._persist(entity)
        return f"Mode for {target} set to {mode_name!r} (CLI: --dangerously-skip-permissions)"

    async def _execute_model(self, model_name: str | None, entity_name: str) -> str:
        """Handle /model <opus|sonnet|haiku|opusplan|fable> [entity] (T007).

        `fable` is accepted; selecting an API-billed model appends a one-line
        billing warning (the set is empty today — everything runs plan-billed).
        """
        # billing_warning reads entity_mod.API_BILLED_MODELS at call time, so a
        # test's monkeypatch of that module-level set is honoured either way.
        from hive.models.entity import VALID_MODELS, billing_warning

        if not model_name or model_name not in VALID_MODELS:
            return f"Usage: /model <{'|'.join(sorted(VALID_MODELS))}> [entity]"

        target = entity_name.strip()
        if not target:
            return "Name the entity: /mode|/model <value> <entity>."
        entity = self.process_manager.entities.get(target)
        if entity is None:
            return f"Entity {target!r} not found."

        entity.model = model_name
        await self.process_manager._persist(entity)
        msg = f"Model for {target} set to {model_name!r}."
        warning = billing_warning(model_name)
        return f"{msg}\n{warning}" if warning else msg

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
