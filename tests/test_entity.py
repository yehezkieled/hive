"""Tests for entity model and state machine."""

from pathlib import Path

import pytest

from hive.models.entity import (
    Entity,
    EntityState,
    InvalidStateTransitionError,
    default_permission_mode,
    parse_personality,
)
from hive.models.vault import Vault


class TestDefaultPermissionMode:
    """T007 one source of truth for the spawn permission mode."""

    def test_maestro_is_yolo(self) -> None:
        assert default_permission_mode("maestro", is_git_repo=True) == "yolo"
        assert default_permission_mode("maestro", is_git_repo=False) == "yolo"

    def test_lead_without_git_repo_falls_back_to_yolo(self) -> None:
        # yotree needs a git worktree; without one the lead falls back to yolo.
        assert default_permission_mode("lead", is_git_repo=False) == "yolo"

    def test_other_roles_default_to_yolo(self) -> None:
        assert default_permission_mode("vault", is_git_repo=False) == "yolo"


class TestEntityState:
    """Test state machine transitions."""

    def test_idle_to_starting(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.state == EntityState.IDLE
        e.transition_to(EntityState.STARTING)
        assert e.state == EntityState.STARTING

    def test_starting_to_running(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        assert e.state == EntityState.RUNNING
        assert e.started_at is not None

    def test_running_to_completed(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.COMPLETED)
        assert e.state == EntityState.COMPLETED
        assert e.pid is None

    def test_running_to_error(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.ERROR)
        assert e.state == EntityState.ERROR

    def test_running_to_stopped(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.STOPPED)
        assert e.state == EntityState.STOPPED

    def test_completed_to_idle(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.COMPLETED)
        e.transition_to(EntityState.IDLE)
        assert e.state == EntityState.IDLE

    def test_invalid_transition_raises(self) -> None:
        e = Entity(name="test", role="lead")
        with pytest.raises(InvalidStateTransitionError):
            e.transition_to(EntityState.RUNNING)  # can't skip STARTING

    def test_invalid_backward_transition(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        with pytest.raises(InvalidStateTransitionError):
            e.transition_to(EntityState.STARTING)  # can't go back

    def test_starting_to_error(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.ERROR)
        assert e.state == EntityState.ERROR

    def test_running_to_gated(self) -> None:
        """A Turn that hits an interactive gate parks in GATED."""
        e = Entity(name="test", role="maestro")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.GATED)
        assert e.state == EntityState.GATED

    def test_gated_back_to_running_on_resume(self) -> None:
        """After the decision is injected the same Turn resumes in RUNNING."""
        e = Entity(name="test", role="maestro")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.GATED)
        e.transition_to(EntityState.RUNNING)
        assert e.state == EntityState.RUNNING

    def test_cannot_gate_from_idle(self) -> None:
        """GATED is only reachable from a live (RUNNING) Turn."""
        e = Entity(name="test", role="maestro")
        with pytest.raises(InvalidStateTransitionError):
            e.transition_to(EntityState.GATED)


class TestPersonalityParsing:
    """Test personality markdown file parsing."""

    def test_parse_full_personality(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("""# Maestro: Dev

## Identity
- **Name**: Dev
- **Role**: maestro
- **Model**: sonnet

## System Prompt
You are Dev, a software engineering maestro.
You lead development teams.

## Tools
- allowedTools: Bash Read Write Edit
- disallowedTools: WebSearch

## Constraints
Never push to main directly.
""")
        config = parse_personality(p)
        assert config.name == "Dev"
        assert config.role == "maestro"
        assert config.model == "sonnet"
        assert "software engineering maestro" in config.system_prompt
        assert config.allowed_tools == ["Bash", "Read", "Write", "Edit"]
        assert config.disallowed_tools == ["WebSearch"]
        assert "Never push" in config.constraints

    def test_parse_minimal_personality(self, tmp_path: Path) -> None:
        p = tmp_path / "minimal.md"
        p.write_text("""# Lead

## Identity
- **Name**: Coder
- **Role**: lead
- **Model**: haiku

## System Prompt
You write code.
""")
        config = parse_personality(p)
        assert config.name == "Coder"
        assert config.role == "lead"
        assert config.model == "haiku"
        assert config.system_prompt == "You write code."
        assert config.allowed_tools == []


class TestEntityCLIArgs:
    """Test CLI argument building."""

    def test_basic_args(self) -> None:
        e = Entity(name="test", role="lead", model="sonnet")
        args = e.build_cli_args()
        assert "claude" in args
        assert "-p" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--verbose" in args
        assert "--model" in args
        assert "sonnet" in args

    def test_args_with_system_prompt(self) -> None:
        e = Entity(name="test", role="lead", system_prompt="You are helpful.")
        args = e.build_cli_args()
        assert "--system-prompt" in args
        idx = args.index("--system-prompt")
        assert args[idx + 1] == "You are helpful."

    def test_args_with_tools(self) -> None:
        e = Entity(
            name="test",
            role="lead",
            allowed_tools=["Bash", "Read"],
            disallowed_tools=["WebSearch"],
        )
        args = e.build_cli_args()
        assert "--allowedTools" in args
        assert "--disallowedTools" in args


class TestEntityUptime:
    """Test uptime tracking."""

    def test_uptime_none_when_idle(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.uptime_seconds is None

    def test_uptime_when_running(self) -> None:
        e = Entity(name="test", role="lead")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        uptime = e.uptime_seconds
        assert uptime is not None
        assert uptime >= 0


class TestEntitySessionId:
    """Test session_id field for --resume support."""

    def test_session_id_defaults_to_none(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.session_id is None

    def test_session_id_can_be_set(self) -> None:
        e = Entity(name="test", role="lead")
        e.session_id = "abc-123"
        assert e.session_id == "abc-123"


class TestPermissionMode:
    """Test permission_mode field and --permission-mode CLI arg."""

    def test_permission_mode_defaults_to_default(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.permission_mode == "default"

    def test_set_permission_mode_plan(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_permission_mode("plan")
        assert e.permission_mode == "plan"

    def test_set_permission_mode_auto(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_permission_mode("auto")
        assert e.permission_mode == "bypassPermissions"

    def test_set_permission_mode_edit(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_permission_mode("edit")
        assert e.permission_mode == "default"

    def test_set_permission_mode_invalid_raises(self) -> None:
        e = Entity(name="test", role="lead")
        with pytest.raises(ValueError, match="Unknown permission mode"):
            e.set_permission_mode("turbo")

    def test_build_cli_args_includes_permission_mode(self) -> None:
        e = Entity(name="test", role="lead", permission_mode="plan")
        args = e.build_cli_args()
        assert "--permission-mode" in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "plan"

    def test_build_cli_args_omits_default_permission_mode(self) -> None:
        e = Entity(name="test", role="lead")
        args = e.build_cli_args()
        assert "--permission-mode" not in args

    def test_set_permission_mode_yolo(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_permission_mode("yolo")
        assert e.permission_mode == "yolo"

    def test_set_permission_mode_yotree(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_permission_mode("yotree")
        assert e.permission_mode == "yotree"

    def test_yolo_emits_dangerous_flag(self) -> None:
        e = Entity(name="test", role="lead", permission_mode="yolo")
        args = e.build_cli_args()
        assert "--dangerously-skip-permissions" in args
        assert "--permission-mode" not in args

    def test_yotree_emits_dangerous_flag(self) -> None:
        e = Entity(name="test", role="lead", permission_mode="yotree")
        args = e.build_cli_args()
        assert "--dangerously-skip-permissions" in args
        assert "--permission-mode" not in args

    def test_plan_mode_still_uses_permission_mode_flag(self) -> None:
        """Regression guard: only yolo/yotree use the dangerous flag."""
        e = Entity(name="test", role="lead", permission_mode="plan")
        args = e.build_cli_args()
        assert "--dangerously-skip-permissions" not in args
        assert "--permission-mode" in args


class TestLoopMode:
    """T007 retired the LOOP_PROMPTS injection (native /goal replaces it).

    The ``loop_mode`` field stays as a dormant persisted column for backward
    compatibility, but no loop framework is appended to the CLI args anymore.
    """

    def test_loop_mode_field_persists_but_is_dormant(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.loop_mode == "ralph"  # persisted default, no longer injected

    def test_set_loop_mode_is_removed(self) -> None:
        e = Entity(name="test", role="lead")
        assert not hasattr(e, "set_loop_mode")

    def test_build_cli_args_injects_no_loop_prompt(self) -> None:
        e = Entity(name="test", role="lead", loop_mode="ship-it")
        args = e.build_cli_args()
        appended = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert not any("Execute immediately" in a for a in appended)
        assert not any("RALPH" in a for a in appended)


class TestCurrentPriority:
    """Test current_priority field."""

    def test_current_priority_defaults_to_3(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.current_priority == 3


class TestMessagingPromptInjection:
    """Every entity gets identity + role JD as appended prompts (T007 retired
    the loop block). The role JD encodes the messaging protocol and any
    role-specific autonomy actions (spawn_team for maestros, the Workflow leaf
    path for leads).
    """


class TestIdentityPreamble:
    """Every entity must know its own name and role. Without this, a lead
    asked to spawn workers fills the placeholder ``<full.lead.name>`` with
    whatever string it can pattern-match in its prompt — which led to the
    real bug where ``dev.mdcount`` emitted ``"lead": "maestro"``.
    """

    def test_identity_preamble_is_first_append(self) -> None:
        """Identity comes before loop/messaging/autonomy so the model reads
        its own name before any guidance that references it."""
        lead = Vault(name="dev.backend")
        args = lead.build_cli_args()
        first_idx = next(i for i, a in enumerate(args) if a == "--append-system-prompt")
        assert "dev.backend" in args[first_idx + 1]


class TestSubclasses:
    """Test the Maestro subclass."""


class TestPhaseConfirmation:
    """Ticket 019 (ADR 0019): phase-confirmation gate fields + personality parsing."""


class TestMaestroIsPa:
    """The PA Maestro is the default route (Ticket 033). ``is_pa`` is the
    single source of truth for that structural role, keyed on the configured
    default-maestro name."""
