"""CommandResult — the outcome of executing a command.

Lives in its own module so command-group collaborators (formatter,
datastore_commands, git_commands) can import it without forming an import
cycle back through dispatch.py (which imports them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Outcome of executing a command — plain text plus optional metadata.

    Surfaces read ``text`` for display. ``metadata`` is a free-form dict
    for transport-specific hints (e.g. a future web UI rendering
    structured data instead of a string). ``routed`` is True when the
    dispatcher already persisted the round-trip through the bus router,
    so transport layers must not log it again.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    routed: bool = False
    entity: str = ""
