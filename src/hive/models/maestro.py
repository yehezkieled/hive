"""Maestro entity — top-level orchestration entity."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.models.entity import Entity
from hive.models.team import Team


@dataclass
class Maestro(Entity):
    """A maestro manages teams and coordinates high-level work."""

    role: str = "maestro"
    teams: dict[str, Team] = field(default_factory=dict)

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
