"""Team lead entity — manages workers within a team."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.models.entity import Entity


@dataclass
class TeamLead(Entity):
    """A team lead that manages worker agents within a team.

    Named ``maestro.team`` (e.g., "dev.backend"). Tracks its parent
    maestro and the workers it manages.
    """

    role: str = "lead"
    team_name: str = ""
    maestro_name: str = ""
    workers: list[str] = field(default_factory=list)
    max_workers: int = 2
