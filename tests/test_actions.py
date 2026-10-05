"""Tests for hive.bus.actions — action parser."""

from hive.bus.actions import parse_actions


class TestParseActions:
    """Test parse_actions() extraction and cleaning."""

    def test_no_actions_returns_original_text(self) -> None:
        text = "Here is my analysis of the codebase."
        clean, actions, _ = parse_actions(text)
        assert clean == text
        assert actions == []

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

    def test_no_actions_block_returns_no_errors(self) -> None:
        _, _, errors = parse_actions("Just plain prose.")
        assert errors == []

    def test_malformed_json_returns_error(self) -> None:
        text = "Hello.\n\n<hive_actions>\n{not valid json}\n</hive_actions>"
        _, actions, errors = parse_actions(text)
        assert actions == []
        assert len(errors) == 1
        assert "Malformed JSON" in errors[0]

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


class TestParseErrorFeedbackNotReparseable:
    """Parse-error feedback strings must not contain parseable
    ``<hive_actions>`` tag substrings.

    Background: when ``parse_actions`` failed, the previous help text
    quoted the literal ``<hive_actions>`` and ``</hive_actions>`` tag
    names. The orchestrator routes that text back to the entity as a
    system feedback message, the entity's terminal screen-echoes it,
    and on the next turn ``parse_actions`` re-scans the screen text,
    finds the substrings inside its own help, tries to parse the prose
    between them as JSON, fails, generates the same help message —
    a self-sustaining loop firing every ~2h in prod.

    Fix invariant: every error string returned by ``parse_actions``
    must be neutralised so that feeding the full error list back
    through ``parse_actions`` produces zero errors.
    """

    def test_orphan_open_error_has_no_parseable_tags(self) -> None:
        # No-close path: error string itself contained
        # `<hive_actions>` and `</hive_actions>` literally.
        text = "Here\n<hive_actions>\nblah blah no close"
        _, _, errors = parse_actions(text)
        assert len(errors) == 1
        assert "<hive_actions>" not in errors[0]
        assert "</hive_actions>" not in errors[0]

    def test_malformed_json_error_has_no_parseable_tags(self) -> None:
        text = "Hello.\n\n<hive_actions>\n{not valid json}\n</hive_actions>"
        _, _, errors = parse_actions(text)
        assert len(errors) == 1
        assert "<hive_actions>" not in errors[0]
        assert "</hive_actions>" not in errors[0]

    def test_non_array_error_has_no_parseable_tags(self) -> None:
        text = (
            'Done.\n\n<hive_actions>\n{"type": "message", "to": "x", "text": "y"}\n</hive_actions>'
        )
        _, _, errors = parse_actions(text)
        assert len(errors) == 1
        assert "<hive_actions>" not in errors[0]
        assert "</hive_actions>" not in errors[0]

    def test_feeding_errors_back_produces_no_errors(self) -> None:
        # The regression test for the every-2h loop. Compose the
        # feedback text the way the orchestrator does (errors joined
        # into a single body), feed it back into parse_actions, and
        # assert no parse errors fire — i.e. the loop can no longer
        # form.
        triggers = [
            "Here\n<hive_actions>\nno close at all",
            "Bad.\n\n<hive_actions>\n{not valid json}\n</hive_actions>",
            (
                'Done.\n\n<hive_actions>\n{"type": "message", "to": "x", '
                '"text": "y"}\n</hive_actions>'
            ),
        ]
        all_errors: list[str] = []
        for trigger in triggers:
            _, _, errors = parse_actions(trigger)
            all_errors.extend(errors)
        assert all_errors, "fixture should produce errors to feed back"
        feedback = "\n".join(f"- {err}" for err in all_errors)
        _, actions_back, errors_back = parse_actions(feedback)
        assert actions_back == []
        assert errors_back == []


class TestRetiredActions:
    """The Entity-runtime actions retired at the cut-over (ADR 0033)."""

    def test_message_spawn_team_kill_and_decision_are_unknown(self) -> None:
        for body in (
            '{"type": "message", "to": "dev", "text": "hi"}',
            '{"type": "spawn_team", "name": "backend"}',
            '{"type": "kill_entity", "target": "dev.backend"}',
            '{"type": "request_decision", "to": "user", "text": "which?"}',
        ):
            _, actions, errors = parse_actions(f"<hive_actions>\n[{body}]\n</hive_actions>")
            assert actions == []
            assert errors
