"""Personality-file parsing and application for an Entity (ADR 0006 collaborator)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive.models.entity import Entity


@dataclass
class PersonalityConfig:
    """Parsed personality configuration from a markdown file."""

    name: str
    role: str
    model: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    constraints: str = ""
    advisor: str = ""
    # Ticket 019 (ADR 0019): per-maestro opt-out for the phase-confirmation gate.
    # Default on; ``**Phase Confirm**: off`` in the personality disables it.
    phase_confirm: bool = True


def parse_personality(path: Path) -> PersonalityConfig:
    """Parse a personality markdown file into a PersonalityConfig.

    Expected format:
        # Entity: Name
        ## Identity
        - **Name**: Dev
        - **Role**: maestro
        - **Model**: sonnet
        ## System Prompt
        <prompt text>
        ## Tools
        - allowedTools: Bash Read Write
    """
    text = path.read_text()

    def extract_field(pattern: str, default: str = "") -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else default

    name = extract_field(r"\*\*Name\*\*:\s*(.+)")
    role = extract_field(r"\*\*Role\*\*:\s*(.+)")
    model = extract_field(r"\*\*Model\*\*:\s*(.+)")
    advisor = extract_field(r"\*\*Advisor\*\*:\s*(.+)")
    # Ticket 019 (ADR 0019): phase-confirmation gate opt-out. Absent → on.
    phase_confirm_field = extract_field(r"\*\*Phase Confirm\*\*:\s*(.+)")
    phase_confirm = phase_confirm_field.strip().lower() not in ("off", "false", "no")

    # Extract system prompt: everything between ## System Prompt and the next ##
    prompt_match = re.search(
        r"## System Prompt\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL | re.IGNORECASE
    )
    system_prompt = prompt_match.group(1).strip() if prompt_match else ""

    # Extract tools
    allowed_str = extract_field(r"allowedTools:\s*(.+)")
    disallowed_str = extract_field(r"disallowedTools:\s*(.+)")
    allowed_tools = [t.strip() for t in allowed_str.split() if t.strip()] if allowed_str else []
    disallowed_tools = (
        [t.strip() for t in disallowed_str.split() if t.strip()] if disallowed_str else []
    )

    # Extract constraints
    constraints_match = re.search(
        r"## Constraints\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL | re.IGNORECASE
    )
    constraints = constraints_match.group(1).strip() if constraints_match else ""

    return PersonalityConfig(
        name=name,
        role=role,
        model=model,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        constraints=constraints,
        advisor=advisor,
        phase_confirm=phase_confirm,
    )


class PersonalityLoader:
    """Applies an Entity's personality markdown to the Entity.

    Collaborator of ``Entity`` per ADR 0006: holds a back-reference and
    mutates the entity's fields through it; ``Entity.load_personality`` is a
    thin delegation.
    """

    def __init__(self, entity: Entity) -> None:
        self._entity = entity

    def load(self) -> PersonalityConfig | None:
        """Load and apply personality config from the entity's markdown file."""
        entity = self._entity
        if entity.personality_path is None or not entity.personality_path.exists():
            return None

        config = parse_personality(entity.personality_path)
        entity.model = config.model or entity.model
        entity.advisor = config.advisor or entity.advisor
        entity.allowed_tools = config.allowed_tools or entity.allowed_tools
        entity.disallowed_tools = config.disallowed_tools or entity.disallowed_tools
        entity.system_prompt = config.system_prompt
        # Ticket 019 (ADR 0019): apply the phase-confirmation opt-out (bool — a
        # plain assign, not `or`, so an explicit ``off`` overrides the default).
        entity.phase_confirm = config.phase_confirm
        return config
