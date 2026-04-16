"""Vault entity — security-gated, approval-required operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.models.entity import Entity


@dataclass
class Vault(Entity):
    """Security-critical entity with locked-down permissions.

    Cannot run Bash, Write, or Edit. Cannot be killed by non-user actors.
    All actions require user approval via Telegram.
    """

    role: str = "vault"
    disallowed_tools: list[str] = field(
        default_factory=lambda: ["Bash", "Write", "Edit"]
    )
