"""DataStoreCommands — the data-store command group (Ticket 045).

Vault (hard-money approvals, ADR 0017) and blueprint commands. Constructed
with a ``ProcessManager`` + the vault/blueprint stores it operates on. Follows
ADR 0006 composition (dependency-injected, like the Formatter — touches no
facade-private state). This is a code-clarity split, **not** a security
boundary: vault ownership/approval policy lives in the vault store + approval
flow, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.commands._helpers import _parse_task_id, _strip_quotes
from hive.commands.result import CommandResult

if TYPE_CHECKING:
    from hive.bus.vault_store import VaultStore
    from hive.knowledge.blueprints import BlueprintStore
    from hive.process.manager import ProcessManager
    from hive.telegram.commands import Command


class DataStoreCommands:
    """Vault + blueprint command handlers."""

    def __init__(
        self,
        process_manager: ProcessManager,
        vault_store: VaultStore | None = None,
        blueprint_store: BlueprintStore | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.vault_store = vault_store
        self.blueprint_store = blueprint_store

    # ------------------------------------------------------------------
    # Registry handlers — uniform ``async (cmd, actor) -> CommandResult``.
    # ------------------------------------------------------------------

    async def vault(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_vault(cmd.target, cmd.args))

    async def blueprint(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_blueprint(cmd.target, cmd.args))

    # ------------------------------------------------------------------
    # Bodies (moved verbatim from CommandDispatcher; Ticket 045)
    # ------------------------------------------------------------------

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
