"""Project registry model (Ticket 024).

A ``Project`` is a registry record mapping a project name to its root path on
disk and, optionally, the maestro that owns it. The invariant is
``1 project <-> <=1 maestro``: a project has at most one owning maestro, and a
maestro owns at most one project. Ownership changes are mediated by
``ProjectStore.assign``, which raises ``ProjectOwnershipError`` on a violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Project:
    """A registered project and its (optional) owning maestro."""

    name: str
    root_path: Path
    owning_maestro: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectOwnershipError(Exception):
    """Raised when an assignment would violate the 1-project<->1-maestro rule."""
