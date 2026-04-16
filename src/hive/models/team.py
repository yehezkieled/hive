"""Team model — groups a TeamLead and its WorkerAgents under a Maestro."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Team:
    """A team within a maestro's organization.

    Naming convention: lead is ``maestro.team``, workers are
    ``maestro.team.worker``. For example, maestro "dev" with team
    "backend" has lead "dev.backend" and workers "dev.backend.w1".
    """

    name: str
    maestro: str
    lead: str | None = None
    workers: list[str] = field(default_factory=list)
