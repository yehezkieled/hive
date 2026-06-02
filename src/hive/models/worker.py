"""Worker entity — executes tasks assigned by team leads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hive.models.entity import Entity


@dataclass
class Worker(Entity):
    """A Worker that executes specific tasks in an isolated worktree.

    Named ``maestro.team.worker`` (e.g., "dev.backend.w1"). Tracks its
    parent lead and the task it's currently working on.
    """

    role: str = "worker"
    team_name: str = ""
    lead_name: str = ""
    worktree_path: Path | None = None
    task_id: int | None = None

    def build_cli_args(self) -> list[str]:
        """Workers get worktree-specific arguments."""
        args = super().build_cli_args()
        if self.worktree_path:
            args.extend(["--add-dir", str(self.worktree_path)])
        return args
