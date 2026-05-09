"""Tests for hive.bus.actions — action parser."""

from hive.bus.actions import Action, parse_actions


class TestParseActions:
    """Test parse_actions() extraction and cleaning."""

    def test_no_actions_returns_original_text(self) -> None:
        text = "Here is my analysis of the codebase."
        clean, actions, _ = parse_actions(text)
        assert clean == text
        assert actions == []

    def test_single_message_action(self) -> None:
        text = (
            "Done with the review.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "Start migration"}]\n'
            "</hive_actions>"
        )
        clean, actions, _ = parse_actions(text)
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
        clean, actions, _ = parse_actions(text)
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
        clean, _, _ = parse_actions(text)
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
        clean, _, _ = parse_actions(text)
        assert "Before the block." in clean
        assert "After the block." in clean

    def test_malformed_json_returns_empty_list(self) -> None:
        text = "Hello.\n\n<hive_actions>\n{not valid json}\n</hive_actions>"
        clean, actions, _ = parse_actions(text)
        assert actions == []
        assert "<hive_actions>" not in clean

    def test_missing_required_fields_skips_action(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n[{"type": "message", "to": "dev.backend"}]\n</hive_actions>'
        )
        _, actions, _ = parse_actions(text)
        assert actions == []

    def test_unknown_action_type_skipped(self) -> None:
        text = (
            "Done.\n\n"
            "<hive_actions>\n"
            '[{"type": "spawn", "to": "dev.backend", "text": "go"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(text)
        assert actions == []

    def test_non_array_json_returns_empty(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n{"type": "message", "to": "x", "text": "y"}\n</hive_actions>'
        )
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].to == "dev.backend"

    def test_orphan_opening_skipped_valid_retry_parsed(self) -> None:
        # Reproduces the real bug: the model first emitted
        # <hive_actions>...</invoke> (wrong close), Claude Code's harness
        # injected a "tool call malformed" retry message, the model retried
        # with the correct close. parse_actions should ignore the orphan
        # opening and pick up the valid retry.
        text = (
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev", "text": "first attempt"}]\n'
            "</invoke>\n"
            "(harness chatter: tool call malformed, please retry)\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev", "text": "retry success"}]\n'
            "</hive_actions>"
        )
        clean, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].text == "retry success"
        assert "<hive_actions>" not in clean
        assert "</invoke>" not in clean

    def test_multiple_well_formed_blocks_merge(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "message", "to": "a", "text": "one"}]\n'
            "</hive_actions>\n"
            "narration in between\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "b", "text": "two"}]\n'
            "</hive_actions>"
        )
        clean, actions, _ = parse_actions(text)
        assert len(actions) == 2
        assert actions[0].to == "a"
        assert actions[1].to == "b"
        assert "<hive_actions>" not in clean
        assert "narration in between" not in clean

    def test_clean_text_strips_full_span_including_orphan(self) -> None:
        text = (
            "Before.\n"
            "<hive_actions>\n"
            "[broken json\n"
            "</invoke>\n"
            "garbage\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "x", "text": "y"}]\n'
            "</hive_actions>\n"
            "After."
        )
        clean, actions, _ = parse_actions(text)
        assert "Before." in clean
        assert "After." in clean
        assert "<hive_actions>" not in clean
        assert "</invoke>" not in clean
        assert "broken json" not in clean
        assert "garbage" not in clean
        assert len(actions) == 1
        assert actions[0].text == "y"


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
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].requested_mode == "yolo"
        assert actions[0].reason is None

    def test_request_mode_change_missing_mode_skipped(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "request_mode_change", "reason": "because"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert len(actions) == 2
        assert actions[0].type == "message"
        assert actions[1].type == "request_mode_change"


class TestSpawnTeamAction:
    """Test parsing spawn_team actions (Sprint 19)."""

    def test_spawn_team_minimal(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert actions[0].model == "opus"

    def test_spawn_team_missing_name_skipped(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_team"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
        assert actions == []

    def test_spawn_team_with_display_name_and_personality(self) -> None:
        """Phase 3: maestros pass display_name + personality blurb when spawning.

        These fields drive the auto-generated personality file written
        by the manager. Both are optional; default behavior unchanged
        when missing.
        """
        text = (
            "<hive_actions>\n"
            "["
            '{"type": "spawn_team", "team_name": "backend", '
            '"display_name": "Backend Eve", '
            '"personality": "Methodical, prefers TDD, runs tight ship."}'
            "]\n"
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].display_name == "Backend Eve"
        assert actions[0].personality == "Methodical, prefers TDD, runs tight ship."

    def test_spawn_team_default_display_name_and_personality_none(self) -> None:
        """When omitted, both fields default to None (same as model)."""
        text = '<hive_actions>\n[{"type": "spawn_team", "team_name": "backend"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
        assert actions[0].display_name is None
        assert actions[0].personality is None


class TestSpawnWorkerAction:
    """Test parsing spawn_worker actions (Sprint 19)."""

    def test_spawn_worker_minimal(self) -> None:
        text = '<hive_actions>\n[{"type": "spawn_worker", "lead": "dev.backend"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert actions[0].worker_name == "migrator"
        assert actions[0].task_id == 42

    def test_spawn_worker_lead_optional(self) -> None:
        """Lead omitted: action is parsed with lead=None.

        The manager fills in `lead` from the actor (a lead spawning under
        itself). Leads can't reliably emit their own dotted name, so the
        protocol no longer requires it.
        """
        text = '<hive_actions>\n[{"type": "spawn_worker"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "spawn_worker"
        assert actions[0].lead is None

    def test_spawn_worker_lead_optional_with_worker_name(self) -> None:
        text = (
            '<hive_actions>\n[{"type": "spawn_worker", "worker_name": "backend"}]\n</hive_actions>'
        )
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].lead is None
        assert actions[0].worker_name == "backend"

    def test_spawn_worker_non_int_task_id_drops_it(self) -> None:
        text = (
            "<hive_actions>\n"
            '[{"type": "spawn_worker", "lead": "dev.backend", "task_id": "abc"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].task_id is None

    def test_spawn_worker_with_display_name_and_personality(self) -> None:
        """Phase 3: leads pass display_name + personality blurb when spawning workers."""
        text = (
            "<hive_actions>\n"
            "["
            '{"type": "spawn_worker", '
            '"display_name": "Migrator Mig", '
            '"personality": "Cautious, never drops a column."}'
            "]\n"
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].display_name == "Migrator Mig"
        assert actions[0].personality == "Cautious, never drops a column."

    def test_spawn_worker_default_display_name_and_personality_none(self) -> None:
        """When omitted, both fields default to None."""
        text = '<hive_actions>\n[{"type": "spawn_worker"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
        assert actions[0].display_name is None
        assert actions[0].personality is None


class TestKillEntityAction:
    """Test parsing kill_entity actions (Sprint 19)."""

    def test_kill_entity(self) -> None:
        text = (
            '<hive_actions>\n[{"type": "kill_entity", "target": "dev.backend.w1"}]\n</hive_actions>'
        )
        _, actions, _ = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].type == "kill_entity"
        assert actions[0].target == "dev.backend.w1"

    def test_kill_entity_missing_target_skipped(self) -> None:
        text = '<hive_actions>\n[{"type": "kill_entity"}]\n</hive_actions>'
        _, actions, _ = parse_actions(text)
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
        _, actions, _ = parse_actions(text)
        assert len(actions) == 3
        types = [a.type for a in actions]
        assert types == ["spawn_team", "spawn_worker", "kill_entity"]


class TestRequestDecisionAction:
    """Test parsing request_decision actions (Sprint 22 — peer messaging)."""

    def test_parse_request_decision_action(self) -> None:
        response = (
            "Some text.\n\n"
            "<hive_actions>\n"
            "["
            '{"type": "request_decision", "to": "dev.backend", '
            '"text": "Should I use JWT or sessions?"}'
            "]\n"
            "</hive_actions>"
        )
        clean, actions, _ = parse_actions(response)
        assert clean == "Some text."
        assert len(actions) == 1
        assert actions[0].type == "request_decision"
        assert actions[0].to == "dev.backend"
        assert actions[0].text == "Should I use JWT or sessions?"

    def test_parse_request_decision_missing_text(self) -> None:
        response = (
            '<hive_actions>\n[{"type": "request_decision", "to": "dev.backend"}]\n</hive_actions>'
        )
        _, actions, _ = parse_actions(response)
        assert actions == []  # missing `text` is rejected

    def test_parse_request_decision_missing_to(self) -> None:
        response = (
            '<hive_actions>\n[{"type": "request_decision", "text": "Decide?"}]\n</hive_actions>'
        )
        _, actions, _ = parse_actions(response)
        assert actions == []  # missing `to` is rejected


class TestRequestPaymentAction:
    """Test parsing request_payment actions (Sprint 25 — vault build-out)."""

    def test_parse_request_payment_action(self) -> None:
        response = (
            "Need to pay vendor.\n\n"
            "<hive_actions>\n"
            "["
            '{"type": "request_payment", '
            '"amount_cents": 5000, '
            '"currency": "usd", '
            '"recipient": "vendor@example.com", '
            '"idempotency_key": "abc-123", '
            '"reason": "October hosting"}'
            "]\n"
            "</hive_actions>"
        )
        clean, actions, _ = parse_actions(response)
        assert clean == "Need to pay vendor."
        assert len(actions) == 1
        a = actions[0]
        assert a.type == "request_payment"
        assert a.amount_cents == 5000
        assert a.currency == "USD"  # normalised to upper
        assert a.recipient == "vendor@example.com"
        assert a.idempotency_key == "abc-123"
        assert a.reason == "October hosting"

    def test_parse_request_payment_missing_amount_skipped(self) -> None:
        response = (
            "<hive_actions>\n"
            '[{"type": "request_payment", "currency": "USD", '
            '"recipient": "r", "idempotency_key": "k", "reason": "x"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(response)
        assert actions == []

    def test_parse_request_payment_negative_amount_skipped(self) -> None:
        response = (
            "<hive_actions>\n"
            '[{"type": "request_payment", "amount_cents": -100, '
            '"currency": "USD", "recipient": "r", "idempotency_key": "k", '
            '"reason": "x"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(response)
        assert actions == []

    def test_parse_request_payment_invalid_currency_skipped(self) -> None:
        response = (
            "<hive_actions>\n"
            '[{"type": "request_payment", "amount_cents": 100, '
            '"currency": "DOLLARS", "recipient": "r", "idempotency_key": "k", '
            '"reason": "x"}]\n'
            "</hive_actions>"
        )
        _, actions, _ = parse_actions(response)
        assert actions == []


class TestParseActionsErrors:
    """parse_actions returns a third tuple element of human-readable
    error strings whenever a block is dropped. The orchestrator routes
    these back to the sender so they can retry — silent drops were the
    bug this is fixing.
    """

    def test_clean_input_returns_no_errors(self) -> None:
        text = (
            "All good.\n\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "hi"}]\n'
            "</hive_actions>"
        )
        _, _, errors = parse_actions(text)
        assert errors == []

    def test_no_actions_block_returns_no_errors(self) -> None:
        _, _, errors = parse_actions("Just plain prose.")
        assert errors == []

    def test_malformed_json_returns_error(self) -> None:
        text = "Hello.\n\n<hive_actions>\n{not valid json}\n</hive_actions>"
        _, actions, errors = parse_actions(text)
        assert actions == []
        assert len(errors) == 1
        assert "Malformed JSON" in errors[0]

    def test_missing_field_returns_error(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n[{"type": "message", "to": "dev.backend"}]\n</hive_actions>'
        )
        _, actions, errors = parse_actions(text)
        assert actions == []
        assert len(errors) == 1
        assert "missing required fields" in errors[0]
        assert "text" in errors[0]

    def test_unknown_action_type_returns_error(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n[{"type": "teleport", "to": "dev.backend"}]\n</hive_actions>'
        )
        _, _, errors = parse_actions(text)
        assert len(errors) == 1
        assert "Unknown action type" in errors[0]
        assert "'teleport'" in errors[0]

    def test_orphan_open_tag_returns_error(self) -> None:
        # Opening tag with no closing tag — entire response after the
        # opening is dropped. The user needs to know.
        text = "Here\n<hive_actions>\nblah blah no close"
        _, actions, errors = parse_actions(text)
        assert actions == []
        assert len(errors) == 1
        assert "no closing" in errors[0]

    def test_non_array_json_returns_error(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n{"type": "message", "to": "x", "text": "y"}\n</hive_actions>'
        )
        _, _, errors = parse_actions(text)
        assert len(errors) == 1
        assert "must be a JSON array" in errors[0]

    def test_multiple_errors_collected(self) -> None:
        text = (
            "Mixed.\n\n<hive_actions>\n"
            "[\n"
            '  {"type": "message", "to": "dev.backend"},\n'
            '  {"type": "teleport"}\n'
            "]\n"
            "</hive_actions>"
        )
        _, actions, errors = parse_actions(text)
        assert actions == []
        assert len(errors) == 2

    def test_partial_success_some_errors(self) -> None:
        # One valid action + one bad action: parser keeps the good one,
        # surfaces error for the bad one.
        text = (
            "Mixed.\n\n<hive_actions>\n"
            "[\n"
            '  {"type": "message", "to": "dev.backend", "text": "hi"},\n'
            '  {"type": "message", "to": "dev.frontend"}\n'
            "]\n"
            "</hive_actions>"
        )
        _, actions, errors = parse_actions(text)
        assert len(actions) == 1
        assert actions[0].to == "dev.backend"
        assert len(errors) == 1
        assert "dev.frontend" in errors[0]
