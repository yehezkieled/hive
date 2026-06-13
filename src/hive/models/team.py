"""Team model — groups a TeamLead and its Workers under a Maestro."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Team:
    """A team within a maestro's organization.

    Naming convention: the lead is ``maestro.team``. For example, maestro
    "dev" with team "backend" has lead "dev.backend".
    """

    name: str
    maestro: str
    lead: str | None = None
    workers: list[str] = field(default_factory=list)
