"""Team lead entity — manages workers within a team. Stub for Sprint 3."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.models.entity import Entity


@dataclass
class TeamLead(Entity):
    """A team lead that manages worker agents. Full implementation in Sprint 3."""

    role: str = "lead"
    workers: list[str] = field(default_factory=list)
