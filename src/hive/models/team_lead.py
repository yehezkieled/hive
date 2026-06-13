"""Team lead entity — manages workers within a team."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hive.models.entity import Entity


@dataclass
class TeamLead(Entity):
    """A team lead that orchestrates leaf work within a team.

    Named ``maestro.team`` (e.g., "dev.backend"). Tracks its parent
    maestro; leaf work fans out through the Workflow tool (leaf agents),
    not persistent Workers (retired, Ticket 018).

    Runs in its own git worktree (the worktree floor, Ticket 015,
    ADR 0010) so leaf agents launched from its session never write to
    the live checkout.
    """

    role: str = "lead"
    team_name: str = ""
    maestro_name: str = ""
    workers: list[str] = field(default_factory=list)
    max_workers: int = 2
    worktree_path: Path | None = None
