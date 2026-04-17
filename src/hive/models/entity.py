"""Base entity model with state machine for all Hive entities."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


class EntityState(enum.Enum):
    """Lifecycle states for a Hive entity."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


# Valid state transitions: from_state -> set of allowed to_states
_TRANSITIONS: dict[EntityState, set[EntityState]] = {
    EntityState.IDLE: {EntityState.STARTING},
    EntityState.STARTING: {EntityState.RUNNING, EntityState.ERROR},
    EntityState.RUNNING: {EntityState.COMPLETED, EntityState.ERROR, EntityState.STOPPED},
    EntityState.COMPLETED: {EntityState.IDLE},
    EntityState.ERROR: {EntityState.IDLE},
    EntityState.STOPPED: {EntityState.IDLE},
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, from_state: EntityState, to_state: EntityState) -> None:
        super().__init__(f"Cannot transition from {from_state.value} to {to_state.value}")
        self.from_state = from_state
        self.to_state = to_state


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
    )


PERMISSION_MODES: dict[str, str] = {
    "plan": "plan",
    "edit": "default",
    "auto": "bypassPermissions",
}


@dataclass
class Entity:
    """Base class for all Hive entities (maestro, lead, worker)."""

    name: str
    role: str  # "maestro", "lead", "worker"
    personality_path: Path | None = None
    model: str = "sonnet"
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    state: EntityState = field(default=EntityState.IDLE)
    pid: int | None = None
    started_at: datetime | None = None
    system_prompt: str = ""
    session_id: str | None = None
    permission_mode: str = "default"
    loop_mode: str = "ralph"
    current_priority: int = 3
    last_activity_at: datetime | None = None

    def transition_to(self, new_state: EntityState) -> None:
        """Transition to a new state, raising InvalidStateTransitionError if not allowed."""
        allowed = _TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(self.state, new_state)
        self.state = new_state

        if new_state == EntityState.RUNNING:
            self.started_at = datetime.now(UTC)
        elif new_state in (EntityState.COMPLETED, EntityState.ERROR, EntityState.STOPPED):
            self.pid = None

    def set_permission_mode(self, mode_name: str) -> None:
        """Set permission_mode from a user-facing name (plan/edit/auto)."""
        cli_value = PERMISSION_MODES.get(mode_name)
        if cli_value is None:
            raise ValueError(
                f"Unknown permission mode {mode_name!r}. Valid: {', '.join(PERMISSION_MODES)}"
            )
        self.permission_mode = cli_value

    def set_loop_mode(self, mode: str) -> None:
        """Set loop_mode, validating against known loop prompts."""
        from hive.process.loops import LOOP_PROMPTS

        if mode not in LOOP_PROMPTS:
            raise ValueError(f"Unknown loop mode {mode!r}. Valid: {', '.join(LOOP_PROMPTS)}")
        self.loop_mode = mode

    def load_personality(self) -> PersonalityConfig | None:
        """Load and apply personality config from the markdown file."""
        if self.personality_path is None or not self.personality_path.exists():
            return None

        config = parse_personality(self.personality_path)
        self.model = config.model or self.model
        self.allowed_tools = config.allowed_tools or self.allowed_tools
        self.disallowed_tools = config.disallowed_tools or self.disallowed_tools
        self.system_prompt = config.system_prompt
        return config

    def build_cli_args(self) -> list[str]:
        """Build claude -p command line arguments for this entity."""
        args = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            self.model,
        ]

        if self.system_prompt:
            args.extend(["--system-prompt", self.system_prompt])

        if self.allowed_tools:
            args.extend(["--allowedTools", *self.allowed_tools])

        if self.disallowed_tools:
            args.extend(["--disallowedTools", *self.disallowed_tools])

        if self.permission_mode != "default":
            args.extend(["--permission-mode", self.permission_mode])

        from hive.process.loops import LOOP_PROMPTS, MESSAGING_PROMPT

        loop_text = LOOP_PROMPTS.get(self.loop_mode)
        if loop_text:
            args.extend(["--append-system-prompt", loop_text])

        # Maestros and leads can send inter-agent messages
        if self.role in ("maestro", "lead"):
            args.extend(["--append-system-prompt", MESSAGING_PROMPT])

        return args

    @property
    def uptime_seconds(self) -> float | None:
        """Seconds since entity started running, or None if not running."""
        if self.started_at is None or self.state != EntityState.RUNNING:
            return None
        return (datetime.now(UTC) - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return f"Entity(name={self.name!r}, role={self.role!r}, state={self.state.value!r})"
