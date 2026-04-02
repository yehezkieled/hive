"""Maestro entity — top-level orchestration entity."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.models.entity import Entity


@dataclass
class Maestro(Entity):
    """A maestro manages teams and coordinates high-level work."""

    role: str = "maestro"
    teams: list[str] = field(default_factory=list)

    def build_cli_args(self) -> list[str]:
        """Maestros get additional context about their org in their system prompt."""
        args = super().build_cli_args()
        return args
