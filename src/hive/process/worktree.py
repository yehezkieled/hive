"""Git worktree management for isolated agent work."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Creates and cleans up git worktrees for agents."""

    def __init__(self, repo_path: Path, worktree_dir: Path) -> None:
        self.repo_path = repo_path
        self.worktree_dir = worktree_dir
        self.worktree_dir.mkdir(parents=True, exist_ok=True)

    async def create(self, name: str, branch: str | None = None) -> Path:
        """Create a git worktree for an agent.

        Returns the path to the new worktree.
        """
        wt_path = self.worktree_dir / name

        if wt_path.exists():
            logger.warning("Worktree %s already exists at %s", name, wt_path)
            return wt_path

        cmd = ["git", "worktree", "add"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.append(str(wt_path))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0 and branch and "already exists" in stderr.decode():
            # Idempotent re-create: ``remove()`` never deletes the branch,
            # so a name reused after a kill hits "a branch named '...'
            # already exists" on ``-b``. Attach to the surviving branch
            # instead of failing.
            proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "add",
                str(wt_path),
                branch,
                cwd=str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"Failed to create worktree {name}: {error}")

        logger.info("Created worktree: %s at %s", name, wt_path)
        return wt_path

    async def remove(self, name: str) -> None:
        """Remove a git worktree."""
        wt_path = self.worktree_dir / name

        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "remove",
            str(wt_path),
            "--force",
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if proc.returncode != 0:
            # Try manual cleanup if git worktree remove fails
            if wt_path.exists():
                import shutil

                shutil.rmtree(wt_path)

        logger.info("Removed worktree: %s", name)

    async def managed_worktrees(self) -> list[dict[str, str]]:
        """List only the worktrees Hive manages — those directly under
        ``worktree_dir``.

        The load-bearing safety filter (Ticket 025, ADR 0016): the main
        checkout and any worktree outside ``worktree_dir`` (e.g. the
        developer's own ``.claude/worktrees/`` sessions, or Claude Code's
        leaf-agent worktrees) are excluded, so reconciliation can never
        touch them. Every sweep consumes this, never raw ``list_worktrees``.
        """
        base = self.worktree_dir.resolve()
        managed: list[dict[str, str]] = []
        for wt in await self.list_worktrees():
            path = wt.get("path")
            if path and Path(path).resolve().parent == base:
                managed.append(wt)
        return managed

    async def is_dirty(self, name: str) -> bool:
        """True if the worktree ``name`` holds uncommitted work.

        Reads ``git status --porcelain`` in ``worktree_dir/name``; any output
        (modified, staged, or untracked files) means dirty. Drives the
        never-delete-dirty orphan policy (Ticket 025, ADR 0016). A missing or
        unreadable worktree is treated as **not** dirty — there is nothing to
        protect.
        """
        wt_path = self.worktree_dir / name
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=str(wt_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return False
        return bool(stdout.decode().strip())

    async def prune(self) -> list[str]:
        """Prune stale git worktree admin records (working dir gone).

        Crash matrix #5: a partial cleanup can leave a worktree's git
        metadata pointing at a directory that no longer exists. ``git
        worktree prune -v`` clears those records. Returns the verbose lines
        git reported (empty when nothing was prunable).
        """
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "prune",
            "-v",
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        # ``git worktree prune -v`` reports each removed record on stderr,
        # not stdout — read both so nothing is missed.
        combined = stdout.decode() + stderr.decode()
        pruned = [line for line in combined.splitlines() if line.strip()]
        if pruned:
            logger.info("Pruned %d stale worktree record(s)", len(pruned))
        return pruned

    async def list_worktrees(self) -> list[dict[str, str]]:
        """List all git worktrees."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "list",
            "--porcelain",
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}

        for line in stdout.decode().splitlines():
            if not line.strip():
                if current:
                    worktrees.append(current)
                    current = {}
            elif line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line == "bare":
                current["bare"] = "true"

        if current:
            worktrees.append(current)

        return worktrees
