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
