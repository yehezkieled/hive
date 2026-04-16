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
from hive.models.worker import WorkerAgent


class TestEntityState:
    """Test state machine transitions."""

    def test_idle_to_starting(self) -> None:
        e = Entity(name="test", role="worker")
        assert e.state == EntityState.IDLE
        e.transition_to(EntityState.STARTING)
        assert e.state == EntityState.STARTING

    def test_starting_to_running(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        assert e.state == EntityState.RUNNING
        assert e.started_at is not None

    def test_running_to_completed(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.COMPLETED)
        assert e.state == EntityState.COMPLETED
        assert e.pid is None

    def test_running_to_error(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.ERROR)
        assert e.state == EntityState.ERROR

    def test_running_to_stopped(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.STOPPED)
        assert e.state == EntityState.STOPPED

    def test_completed_to_idle(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        e.transition_to(EntityState.COMPLETED)
        e.transition_to(EntityState.IDLE)
        assert e.state == EntityState.IDLE

    def test_invalid_transition_raises(self) -> None:
        e = Entity(name="test", role="worker")
        with pytest.raises(InvalidStateTransitionError):
            e.transition_to(EntityState.RUNNING)  # can't skip STARTING

    def test_invalid_backward_transition(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        with pytest.raises(InvalidStateTransitionError):
            e.transition_to(EntityState.STARTING)  # can't go back

    def test_starting_to_error(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.ERROR)
        assert e.state == EntityState.ERROR


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
        p.write_text("""# Worker

## Identity
- **Name**: Coder
- **Role**: worker
- **Model**: haiku

## System Prompt
You write code.
""")
        config = parse_personality(p)
        assert config.name == "Coder"
        assert config.role == "worker"
        assert config.model == "haiku"
        assert config.system_prompt == "You write code."
        assert config.allowed_tools == []


class TestEntityCLIArgs:
    """Test CLI argument building."""

    def test_basic_args(self) -> None:
        e = Entity(name="test", role="worker", model="sonnet")
        args = e.build_cli_args()
        assert "claude" in args
        assert "-p" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--verbose" in args
        assert "--model" in args
        assert "sonnet" in args

    def test_args_with_system_prompt(self) -> None:
        e = Entity(name="test", role="worker", system_prompt="You are helpful.")
        args = e.build_cli_args()
        assert "--system-prompt" in args
        idx = args.index("--system-prompt")
        assert args[idx + 1] == "You are helpful."

    def test_args_with_tools(self) -> None:
        e = Entity(
            name="test",
            role="worker",
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
        e = Entity(name="test", role="worker")
        assert e.uptime_seconds is None

    def test_uptime_when_running(self) -> None:
        e = Entity(name="test", role="worker")
        e.transition_to(EntityState.STARTING)
        e.transition_to(EntityState.RUNNING)
        uptime = e.uptime_seconds
        assert uptime is not None
        assert uptime >= 0


class TestEntitySessionId:
    """Test session_id field for --resume support."""

    def test_session_id_defaults_to_none(self) -> None:
        e = Entity(name="test", role="worker")
        assert e.session_id is None

    def test_session_id_can_be_set(self) -> None:
        e = Entity(name="test", role="worker")
        e.session_id = "abc-123"
        assert e.session_id == "abc-123"


class TestSubclasses:
    """Test Maestro and WorkerAgent subclasses."""

    def test_maestro_default_role(self) -> None:
        m = Maestro(name="dev")
        assert m.role == "maestro"
        assert m.teams == {}

    def test_worker_with_worktree(self) -> None:
        w = WorkerAgent(name="coder", worktree_path=Path("/tmp/wt"))
        assert w.role == "worker"
        args = w.build_cli_args()
        assert "--add-dir" in args
        assert "/tmp/wt" in args
