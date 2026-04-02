"""Worker agent entity — executes tasks assigned by team leads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hive.models.entity import Entity


@dataclass
class WorkerAgent(Entity):
    """A worker agent that executes specific tasks in an isolated worktree."""

    role: str = "worker"
    worktree_path: Path | None = None

    def build_cli_args(self) -> list[str]:
        """Workers get worktree-specific arguments."""
        args = super().build_cli_args()
        if self.worktree_path:
            args.extend(["--add-dir", str(self.worktree_path)])
        return args
