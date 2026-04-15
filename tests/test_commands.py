"""Tests for Telegram command parser."""

from hive.telegram.commands import parse_command


def test_status_command() -> None:
    cmd = parse_command("/status")
    assert cmd.name == "status"
    assert cmd.target is None


def test_health_command() -> None:
    cmd = parse_command("/health")
    assert cmd.name == "health"


def test_maestros_command() -> None:
    cmd = parse_command("/maestros")
    assert cmd.name == "maestros"


def test_org_command() -> None:
    cmd = parse_command("/org")
    assert cmd.name == "org"


def test_comms_command() -> None:
    cmd = parse_command("/comms")
    assert cmd.name == "comms"


def test_message_to_maestro() -> None:
    cmd = parse_command("/m:dev build a REST API")
    assert cmd.name == "message"
    assert cmd.target == "dev"
    assert cmd.args == "build a REST API"


def test_message_to_maestro_no_args() -> None:
    cmd = parse_command("/m:dev")
    assert cmd.name == "message"
    assert cmd.target == "dev"
    assert cmd.args == ""


def test_team_command() -> None:
    cmd = parse_command("/t:dev.backend check logs")
    assert cmd.name == "team"
    assert cmd.target == "dev.backend"
    assert cmd.args == "check logs"


def test_agent_command() -> None:
    cmd = parse_command("/a:dev.backend.coder1 fix the bug")
    assert cmd.name == "agent"
    assert cmd.target == "dev.backend.coder1"
    assert cmd.args == "fix the bug"


def test_kill_command() -> None:
    cmd = parse_command("/kill dev")
    assert cmd.name == "kill"
    assert cmd.target == "dev"


def test_compact_command() -> None:
    cmd = parse_command("/compact dev")
    assert cmd.name == "compact"
    assert cmd.target == "dev"


def test_reset_command() -> None:
    cmd = parse_command("/reset dev")
    assert cmd.name == "reset"
    assert cmd.target == "dev"


def test_plain_text_goes_to_default_maestro() -> None:
    cmd = parse_command("hello there")
    assert cmd.name == "message"
    assert cmd.target == "dev"
    assert cmd.args == "hello there"


def test_plain_text_custom_default() -> None:
    cmd = parse_command("hello", default_maestro="pa")
    assert cmd.target == "pa"


def test_empty_string() -> None:
    cmd = parse_command("")
    assert cmd.name == "empty"


def test_whitespace_only() -> None:
    cmd = parse_command("   ")
    assert cmd.name == "empty"


def test_mode_command() -> None:
    cmd = parse_command("/mode plan dev")
    assert cmd.name == "mode"
    assert cmd.target == "plan"
    assert cmd.args == "dev"


def test_multiline_message() -> None:
    cmd = parse_command("/m:dev line1\nline2\nline3")
    assert cmd.name == "message"
    assert cmd.target == "dev"
    assert "line1" in cmd.args
    assert "line3" in cmd.args


def test_task_add_quoted_title() -> None:
    cmd = parse_command('/task add "fix the thing"')
    assert cmd.name == "task"
    assert cmd.target == "add"
    assert cmd.args == '"fix the thing"'


def test_task_add_unquoted_title() -> None:
    cmd = parse_command("/task add write the docs")
    assert cmd.name == "task"
    assert cmd.target == "add"
    assert cmd.args == "write the docs"


def test_task_done_by_id() -> None:
    cmd = parse_command("/task done 5")
    assert cmd.name == "task"
    assert cmd.target == "done"
    assert cmd.args == "5"


def test_task_cancel_by_id() -> None:
    cmd = parse_command("/task cancel 7")
    assert cmd.name == "task"
    assert cmd.target == "cancel"
    assert cmd.args == "7"


def test_tasks_list_command() -> None:
    cmd = parse_command("/tasks")
    assert cmd.name == "tasks"
    assert cmd.target is None
    assert cmd.args == ""


def test_cost_bare() -> None:
    cmd = parse_command("/cost")
    assert cmd.name == "cost"
    assert cmd.target is None
    assert cmd.args == ""


def test_cost_with_window() -> None:
    cmd = parse_command("/cost 7d")
    assert cmd.name == "cost"
    assert cmd.args == "7d"


def test_audit_bare() -> None:
    cmd = parse_command("/audit")
    assert cmd.name == "audit"
    assert cmd.target is None
    assert cmd.args == ""


def test_audit_with_prefix() -> None:
    cmd = parse_command("/audit entity")
    assert cmd.name == "audit"
    assert cmd.args == "entity"
