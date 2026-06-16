"""Tests for entity model and state machine."""

from pathlib import Path

import pytest

from hive.models.entity import (
    Entity,
    EntityState,
    InvalidStateTransitionError,
    parse_personality,
)
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead


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

    def test_load_personality_updates_entity(self, personalities_dir: Path) -> None:
        e = Entity(
            name="dev",
            role="maestro",
            personality_path=personalities_dir / "maestro-dev.md",
        )
        config = e.load_personality()
        assert config is not None
        assert e.model == "sonnet"
        assert e.system_prompt != ""
        assert "Bash" in e.allowed_tools


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
    """Test loop_mode field and --append-system-prompt CLI arg."""

    def test_loop_mode_defaults_to_ralph(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.loop_mode == "ralph"

    def test_set_loop_mode_valid(self) -> None:
        e = Entity(name="test", role="lead")
        e.set_loop_mode("ship-it")
        assert e.loop_mode == "ship-it"

    def test_set_loop_mode_invalid_raises(self) -> None:
        e = Entity(name="test", role="lead")
        with pytest.raises(ValueError, match="Unknown loop mode"):
            e.set_loop_mode("chaos")

    def test_build_cli_args_includes_append_system_prompt(self) -> None:
        e = Entity(name="test", role="lead", loop_mode="ship-it")
        args = e.build_cli_args()
        appended = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert any("Execute immediately" in a for a in appended)

    def test_build_cli_args_includes_ralph_by_default(self) -> None:
        e = Entity(name="test", role="lead")
        args = e.build_cli_args()
        appended = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert any("RALPH" in a for a in appended)


class TestCurrentPriority:
    """Test current_priority field."""

    def test_current_priority_defaults_to_3(self) -> None:
        e = Entity(name="test", role="lead")
        assert e.current_priority == 3


class TestMessagingPromptInjection:
    """Every entity gets identity + loop + role JD as appended prompts.
    The role JD encodes the messaging protocol and any role-specific
    autonomy actions (e.g. spawn_team for maestros, the Workflow leaf
    path for leads).
    """

    def test_maestro_includes_role_jd_with_spawn_team(self) -> None:
        m = Maestro(name="dev")
        args = m.build_cli_args()
        # identity + loop + role JD = 3 --append-system-prompt entries
        indices = [i for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert len(indices) == 3
        appended = [args[i + 1] for i in indices]
        assert any("hive_actions" in a for a in appended)
        assert any("spawn_team" in a for a in appended)

    def test_lead_includes_role_jd_with_workflow_leaf_path(self) -> None:
        """The lead JD (ADR 0010: Workflow leaf engine) rides in as the
        3rd appended block — the JD reframe must not add a 4th.
        """
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        args = lead.build_cli_args()
        indices = [i for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert len(indices) == 3
        appended = [args[i + 1] for i in indices]
        assert any("hive_actions" in a for a in appended)
        assert any("Workflow" in a and "TaskOutput" in a for a in appended)


class TestIdentityPreamble:
    """Every entity must know its own name and role. Without this, a lead
    asked to spawn workers fills the placeholder ``<full.lead.name>`` with
    whatever string it can pattern-match in its prompt — which led to the
    real bug where ``dev.mdcount`` emitted ``"lead": "maestro"``.
    """

    def test_maestro_identity_preamble_includes_name(self) -> None:
        m = Maestro(name="dev")
        args = m.build_cli_args()
        appended = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert any("dev" in a and "maestro" in a.lower() for a in appended)

    def test_lead_identity_preamble_includes_dotted_name(self) -> None:
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        args = lead.build_cli_args()
        appended = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"]
        assert any("dev.backend" in a and "lead" in a.lower() for a in appended)

    def test_identity_preamble_is_first_append(self) -> None:
        """Identity comes before loop/messaging/autonomy so the model reads
        its own name before any guidance that references it."""
        lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
        args = lead.build_cli_args()
        first_idx = next(i for i, a in enumerate(args) if a == "--append-system-prompt")
        assert "dev.backend" in args[first_idx + 1]


class TestSubclasses:
    """Test the Maestro subclass."""

    def test_maestro_default_role(self) -> None:
        m = Maestro(name="dev")
        assert m.role == "maestro"
        assert m.teams == {}


class TestPhaseConfirmation:
    """Ticket 019 (ADR 0019): phase-confirmation gate fields + personality parsing."""

    def test_defaults_unconfirmed_gate_armed(self) -> None:
        """A fresh entity starts unconfirmed with the gate on."""
        e = Entity(name="dev", role="maestro")
        assert e.confirmed_with_user is False
        assert e.phase_confirm is True

    def test_maestro_inherits_defaults(self) -> None:
        m = Maestro(name="dev")
        assert m.confirmed_with_user is False
        assert m.phase_confirm is True

    def test_parse_phase_confirm_off(self, tmp_path: Path) -> None:
        p = tmp_path / "auto.md"
        p.write_text(
            "## Identity\n- **Name**: Otto\n- **Role**: maestro\n"
            "- **Phase Confirm**: off\n\n## System Prompt\nGo.\n"
        )
        assert parse_personality(p).phase_confirm is False

    def test_parse_phase_confirm_absent_defaults_true(self, tmp_path: Path) -> None:
        p = tmp_path / "default.md"
        p.write_text(
            "## Identity\n- **Name**: Otto\n- **Role**: maestro\n\n## System Prompt\nGo.\n"
        )
        assert parse_personality(p).phase_confirm is True

    def test_load_personality_applies_phase_confirm(self, tmp_path: Path) -> None:
        p = tmp_path / "auto.md"
        p.write_text(
            "## Identity\n- **Name**: Otto\n- **Role**: maestro\n"
            "- **Phase Confirm**: off\n\n## System Prompt\nGo.\n"
        )
        m = Maestro(name="otto", personality_path=p)
        m.load_personality()
        assert m.phase_confirm is False
