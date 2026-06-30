"""GitCommands — the git command group (Ticket 045).

/commit, /pr, /merge — operate on an entity's worktree (the worktree floor).
Constructed with a ``ProcessManager`` (to resolve an entity's worktree) + the
audit log (to record git actions). Follows ADR 0006 composition with
dependency injection (touches no facade-private state).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.commands._helpers import _strip_quotes
from hive.commands.result import CommandResult
from hive.config import ALLOW_AUTO_MERGE
from hive.process import git_ops

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.process.manager import ProcessManager
    from hive.telegram.commands import Command


class GitCommands:
    """/commit, /pr, /merge against an entity's worktree."""

    def __init__(self, process_manager: ProcessManager, audit_log: AuditLog | None = None) -> None:
        self.process_manager = process_manager
        self.audit_log = audit_log

    # ------------------------------------------------------------------
    # Registry handlers — uniform ``async (cmd, actor) -> CommandResult``.
    # ------------------------------------------------------------------

    async def commit(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_commit(cmd.target, cmd.args))

    async def pr(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_pr(cmd.target, cmd.args))

    async def merge(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_merge(cmd.target))

    # ------------------------------------------------------------------
    # Bodies (moved verbatim from CommandDispatcher; Ticket 045)
    # ------------------------------------------------------------------

    def _worktree_for(self, entity_name: str):  # type: ignore[no-untyped-def]
        """Return (entity, worktree_path) or (None, None) if entity has no worktree.

        Kept private and untyped to avoid a public dependency on a specific
        entity class — /commit and /pr only need the path, not the type.
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
