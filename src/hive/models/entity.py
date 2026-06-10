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
    # The Turn hit an interactive gate (plan approval, AskUserQuestion, or a
    # permission prompt) and is parked waiting for the user's decision. While
    # GATED the Entity is exempt from idle-kill and the reader timeout is
    # suspended (Ticket 003 / ADR 0004). On the injected decision it resumes
    # the same Turn back to RUNNING.
    GATED = "gated"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


# Valid state transitions: from_state -> set of allowed to_states
_TRANSITIONS: dict[EntityState, set[EntityState]] = {
    EntityState.IDLE: {EntityState.STARTING},
    EntityState.STARTING: {EntityState.RUNNING, EntityState.ERROR},
    EntityState.RUNNING: {
        EntityState.GATED,
        EntityState.COMPLETED,
        EntityState.ERROR,
        EntityState.STOPPED,
    },
    # A gate can resolve (resume the Turn -> RUNNING), error, or be killed.
    EntityState.GATED: {EntityState.RUNNING, EntityState.ERROR, EntityState.STOPPED},
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
    advisor: str = ""


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
    )


def resolve_advisor(model: str, advisor_field: str | None, role: str | None = None) -> str | None:
    """Resolve the advisor model for an entity (Ticket 013, ADR 0009).

    An explicit ``**Advisor**:`` field always wins — a model name turns the
    native advisor on, ``off`` turns it off. With no explicit field the default
    is:

    - **Workers** run one short leaf task where an advisor adds little, so they
      default off (they are also being retired in Phase 3).
    - Otherwise **model-aware**: an advisor only helps when stronger than the
      main model, so a sub-Opus main (Sonnet/Haiku) gets an Opus advisor while
      an Opus (or higher) main gets none — avoiding a same-tier double pass.

    Returns the advisor model, or ``None`` for off.
    """
    if advisor_field and advisor_field.strip():
        value = advisor_field.strip().lower()
        return None if value == "off" else value
    if role == "worker":
        return None
    main = (model or "").lower()
    if "opus" in main or "fable" in main:
        return None
    return "opus"


_AUTO_GENERATED_FRONTMATTER = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def is_auto_generated_personality(path: Path) -> bool:
    """True if the file has a YAML frontmatter block with auto_generated: true.

    Used by ``kill_entity`` to decide whether the personality file at
    this path was authored by the system (safe to delete) or by the
    user (must be preserved). Missing files return False — there's
    nothing to delete.
    """
    if not path.exists():
        return False
    try:
        text = path.read_text()
    except OSError:
        return False
    match = _AUTO_GENERATED_FRONTMATTER.match(text)
    if not match:
        return False
    body = match.group(1)
    for line in body.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "auto_generated":
            return value.strip().lower() == "true"
    return False


PERMISSION_MODES: dict[str, str] = {
    "plan": "plan",
    "edit": "default",
    "auto": "bypassPermissions",
    # `yolo` and `yotree` are sentinels — they map to `--dangerously-skip-permissions`
    # in build_cli_args rather than a `--permission-mode <value>`. yotree additionally
    # requires a worktree to be attached (enforced at the ProcessManager level).
    "yolo": "yolo",
    "yotree": "yotree",
}

# Modes that emit --dangerously-skip-permissions instead of --permission-mode
DANGEROUS_MODES: frozenset[str] = frozenset({"yolo", "yotree"})


@dataclass
class Entity:
    """Base class for all Hive entities (maestro, lead, worker)."""

    name: str
    role: str  # "maestro", "lead", "worker"
    personality_path: Path | None = None
    model: str = "sonnet"
    advisor: str | None = None
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
        self.advisor = config.advisor or self.advisor
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

        if self.permission_mode in DANGEROUS_MODES:
            args.append("--dangerously-skip-permissions")
        elif self.permission_mode != "default":
            args.extend(["--permission-mode", self.permission_mode])

        from hive.process.loops import LOOP_PROMPTS, load_role_jd

        # Identity preamble must be the first appended block so the model
        # reads its own name before any guidance that references it. The
        # role JD avoids placeholders the entity must substitute with its
        # own name (the orchestrator infers `lead` from the actor instead).
        identity_lines = [
            f"You are {self.name}. Your role is {self.role}.",
            "If a hive_action is denied or fails, report the failure honestly. "
            "Do not narrate fictional success.",
        ]
        args.extend(["--append-system-prompt", "\n".join(identity_lines)])

        loop_text = LOOP_PROMPTS.get(self.loop_mode)
        if loop_text:
            args.extend(["--append-system-prompt", loop_text])

        # Role JD encodes the messaging protocol and any role-specific
        # autonomy actions. Loaded from personalities/role-<role>.md so it
        # can be edited without code changes.
        if self.role in ("maestro", "lead", "worker"):
            args.extend(["--append-system-prompt", load_role_jd(self.role)])

        from hive.mcp.config import mcp_servers_enabled

        if mcp_servers_enabled():
            args.extend(["--mcp-config", self.mcp_config_path])

        advisor = resolve_advisor(self.model, self.advisor, self.role)
        if advisor:
            args.extend(["--advisor", advisor])

        return args

    @property
    def uptime_seconds(self) -> float | None:
        """Seconds since entity started running, or None if not running."""
        if self.started_at is None or self.state != EntityState.RUNNING:
            return None
        return (datetime.now(UTC) - self.started_at).total_seconds()

    @property
    def mcp_config_path(self) -> str:
        """Path to the per-entity MCP config file for --mcp-config."""
        # Guard against path traversal characters in the entity name
        if "/" in self.name or ".." in self.name:
            raise ValueError(
                f"Entity name {self.name!r} contains invalid characters ('/' or '..')."
            )
        return f"/tmp/hive-mcp-{self.name}.json"

    def __repr__(self) -> str:
        return f"Entity(name={self.name!r}, role={self.role!r}, state={self.state.value!r})"
