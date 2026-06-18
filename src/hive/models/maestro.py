"""Maestro entity — top-level orchestration entity."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.config import DEFAULT_MAESTRO
from hive.models.entity import Entity
from hive.models.team import Team


@dataclass
class Maestro(Entity):
    """A maestro manages teams and coordinates high-level work."""

    role: str = "maestro"
    teams: dict[str, Team] = field(default_factory=dict)

    @property
    def is_pa(self) -> bool:
        """True iff this maestro is the PA — Hive's default route (Ticket 033).

        The PA owns no project and may read any project but write only
        ownerless ones; every other maestro owns exactly one project. This
        property is the single source of truth for that distinction (keyed on
        the configured default-maestro name) — both the ownership write-fence
        (Ticket 024) and prompt assembly read it.
        """
        return self.name == DEFAULT_MAESTRO

    def create_team(self, team_name: str) -> Team:
        """Create a new team under this maestro.

        Raises ValueError if a team with this name already exists.
        """
        if team_name in self.teams:
            raise ValueError(f"Team {team_name!r} already exists under {self.name}")
        team = Team(name=team_name, maestro=self.name)
        self.teams[team_name] = team
        return team

    def get_team(self, team_name: str) -> Team | None:
        """Get a team by name, or None if it doesn't exist."""
        return self.teams.get(team_name)

    def remove_team(self, team_name: str) -> None:
        """Remove a team from this maestro's organization."""
        self.teams.pop(team_name, None)

    def build_cli_args(self) -> list[str]:
        """Maestros get additional context about their org in their system prompt."""
        args = super().build_cli_args()
        return args
