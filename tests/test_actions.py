"""Tests for hive.bus.actions — action parser."""

from hive.bus.actions import Action, parse_actions


class TestParseActions:
    """Test parse_actions() extraction and cleaning."""

    def test_no_actions_returns_original_text(self) -> None:
        text = "Here is my analysis of the codebase."
        clean, actions = parse_actions(text)
        assert clean == text
        assert actions == []

    def test_single_message_action(self) -> None:
        text = (
            "Done with the review.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "Start migration"}]\n'
            "</hive_actions>"
        )
        clean, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0] == Action(type="message", to="dev.backend", text="Start migration")

    def test_multiple_actions(self) -> None:
        text = (
            "Work complete.\n\n"
            "<hive_actions>\n"
            "[\n"
            '  {"type": "message", "to": "dev.backend", "text": "Start migration"},\n'
            '  {"type": "message", "to": "dev.frontend", "text": "Update the UI"}\n'
            "]\n"
            "</hive_actions>"
        )
        clean, actions = parse_actions(text)
        assert len(actions) == 2
        assert actions[0].to == "dev.backend"
        assert actions[1].to == "dev.frontend"

    def test_clean_text_strips_block(self) -> None:
        text = (
            "Analysis done.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "hi"}]\n'
            "</hive_actions>"
        )
        clean, _ = parse_actions(text)
        assert "<hive_actions>" not in clean
        assert "</hive_actions>" not in clean

    def test_clean_text_preserves_surrounding(self) -> None:
        text = (
            "Before the block.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "x", "text": "y"}]\n'
            "</hive_actions>\n\n"
            "After the block."
        )
        clean, _ = parse_actions(text)
        assert "Before the block." in clean
        assert "After the block." in clean

    def test_malformed_json_returns_empty_list(self) -> None:
        text = "Hello.\n\n<hive_actions>\n{not valid json}\n</hive_actions>"
        clean, actions = parse_actions(text)
        assert actions == []
        assert "<hive_actions>" not in clean

    def test_missing_required_fields_skips_action(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n[{"type": "message", "to": "dev.backend"}]\n</hive_actions>'
        )
        _, actions = parse_actions(text)
        assert actions == []

    def test_unknown_action_type_skipped(self) -> None:
        text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "spawn", "to": "dev.backend", "text": "go"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert actions == []

    def test_non_array_json_returns_empty(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n{"type": "message", "to": "x", "text": "y"}\n</hive_actions>'
        )
        _, actions = parse_actions(text)
        assert actions == []

    def test_mixed_valid_and_invalid_actions(self) -> None:
        text = (
            "Done.\n\n"
            "<hive_actions>\n"
            "[\n"
            '  {"type": "message", "to": "dev.backend", "text": "valid"},\n'
            '  {"type": "spawn", "to": "x", "text": "invalid type"},\n'
            '  {"type": "message", "text": "missing to field"}\n'
            "]\n"
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].to == "dev.backend"


class TestRequestModeChangeAction:
    """Test parsing request_mode_change actions."""

    def test_request_mode_change_with_reason(self) -> None:
        text = (
            "Need elevation.\n\n"
            "<hive_actions>\n"
            "["
            '{"type": "request_mode_change", '
            '"requested_mode": "yotree", '
            '"reason": "refactor session manager"}'
            "]\n"
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "request_mode_change"
        assert actions[0].requested_mode == "yotree"
        assert actions[0].reason == "refactor session manager"

    def test_request_mode_change_without_reason(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "request_mode_change", "requested_mode": "yolo"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].requested_mode == "yolo"
        assert actions[0].reason is None

    def test_request_mode_change_missing_mode_skipped(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "request_mode_change", "reason": "because"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert actions == []

    def test_mixed_message_and_mode_request(self) -> None:
        text = (
            "<hive_actions>\n"
            "[\n"
            '  {"type": "message", "to": "dev", "text": "status"},\n'
            '  {"type": "request_mode_change", "requested_mode": "yotree"}\n'
            "]\n"
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 2
        assert actions[0].type == "message"
        assert actions[1].type == "request_mode_change"


class TestSpawnTeamAction:
    """Test parsing spawn_team actions (Sprint 19)."""

    def test_spawn_team_minimal(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "spawn_team"
        assert actions[0].team_name == "backend"
        assert actions[0].model is None

    def test_spawn_team_with_model(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "spawn_team", "team_name": "backend", "model": "opus"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert actions[0].model == "opus"

    def test_spawn_team_missing_name_skipped(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_team"}]\n</hive_actions>'
        _, actions = parse_actions(text)
        assert actions == []


class TestSpawnWorkerAction:
    """Test parsing spawn_worker actions (Sprint 19)."""

    def test_spawn_worker_minimal(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "spawn_worker"
        assert actions[0].lead == "dev.backend"
        assert actions[0].worker_name is None
        assert actions[0].task_id is None

    def test_spawn_worker_with_optional_fields(self) -> None:
        text = (
            "<hive_actions>\n"
            "["
            '{"type": "spawn_worker", "lead": "dev.backend", '
            '"worker_name": "migrator", "task_id": 42}'
            "]\n"
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert actions[0].worker_name == "migrator"
        assert actions[0].task_id == 42

    def test_spawn_worker_lead_optional(self) -> None:
        """Lead omitted: action is parsed with lead=None.

        The manager fills in `lead` from the actor (a lead spawning under
        itself). Leads can't reliably emit their own dotted name, so the
        protocol no longer requires it.
        """
        text = '<hive_actions>\n[{"type": "spawn_worker"}]\n</hive_actions>'
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "spawn_worker"
        assert actions[0].lead is None

    def test_spawn_worker_lead_optional_with_worker_name(self) -> None:
        text = (
            '<hive_actions>\n[{"type": "spawn_worker", "worker_name": "backend"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].lead is None
        assert actions[0].worker_name == "backend"

    def test_spawn_worker_non_int_task_id_drops_it(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "spawn_worker", "lead": "dev.backend", "task_id": "abc"}]\n'
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].task_id is None


class TestKillEntityAction:
    """Test parsing kill_entity actions (Sprint 19)."""

    def test_kill_entity(self) -> None:
        text = (
            '<hive_actions>\n[{"type": "kill_entity", "target": "dev.backend.w1"}]\n</hive_actions>'
        )
        _, actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "kill_entity"
        assert actions[0].target == "dev.backend.w1"

    def test_kill_entity_missing_target_skipped(self) -> None:
        text = '<hive_actions>\n[{"type": "kill_entity"}]\n</hive_actions>'
        _, actions = parse_actions(text)
        assert actions == []

    def test_mixed_autonomy_actions(self) -> None:
        """All Sprint 19 action types parse together."""
        text = (
            "<hive_actions>\n"
            "[\n"
            '  {"type": "spawn_team", "team_name": "backend"},\n'
            '  {"type": "spawn_worker", "lead": "dev.backend"},\n'
            '  {"type": "kill_entity", "target": "dev.frontend.w1"}\n'
            "]\n"
            "</hive_actions>"
        )
        _, actions = parse_actions(text)
        assert len(actions) == 3
        types = [a.type for a in actions]
        assert types == ["spawn_team", "spawn_worker", "kill_entity"]
