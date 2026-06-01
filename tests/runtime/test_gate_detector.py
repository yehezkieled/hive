"""Tests for GateDetector — structural detection of unanswered gates.

Detection reads the parsed .jsonl transcript ONLY (no screen-scraping). The
plan gate appears as either an `attachment.type == "plan_mode"` or an
`ExitPlanMode` tool_use with no matching tool_result. The bare string
"ExitPlanMode" inside `deferred_tools_delta` must NOT false-positive.
"""

from __future__ import annotations

from hive.runtime.gates import Gate, GateDetector


def _assistant_with_blocks(blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "sessionId": "sess-1",
        "uuid": "uuid-a",
        "message": {"role": "assistant", "content": blocks},
    }


def _tool_result_user(tool_use_id: str, content: str = "ok") -> dict:
    return {
        "type": "user",
        "sessionId": "sess-1",
        "uuid": "uuid-u",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        },
    }


def test_detect_returns_none_on_plain_completed_turn() -> None:
    """A normal turn (text only, no open tool_use) is not a gate."""
    entries = [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _assistant_with_blocks([{"type": "text", "text": "done"}]),
    ]
    assert GateDetector().detect(entries) is None


def test_detect_plan_gate_from_exitplanmode_tool_use_without_result() -> None:
    """An ExitPlanMode tool_use with no matching tool_result is a plan gate."""
    entries = [
        _assistant_with_blocks(
            [
                {"type": "text", "text": "Here is my plan."},
                {
                    "type": "tool_use",
                    "id": "tu-plan-1",
                    "name": "ExitPlanMode",
                    "input": {"plan": "1. do X\n2. do Y"},
                },
            ]
        ),
    ]
    gate = GateDetector().detect(entries)
    assert isinstance(gate, Gate)
    assert gate.kind == "plan"
    assert "do X" in gate.payload["plan"]


def test_detect_plan_gate_from_plan_mode_attachment() -> None:
    """An attachment.type == 'plan_mode' is a plan gate."""
    entries = [
        {
            "type": "user",
            "message": {"role": "user", "content": "go"},
            "attachment": {"type": "plan_mode", "plan": "the plan text"},
        },
    ]
    gate = GateDetector().detect(entries)
    assert isinstance(gate, Gate)
    assert gate.kind == "plan"


def test_detect_returns_none_when_exitplanmode_has_matching_result() -> None:
    """If the ExitPlanMode tool_use has a matching tool_result, it's answered."""
    entries = [
        _assistant_with_blocks(
            [
                {
                    "type": "tool_use",
                    "id": "tu-plan-2",
                    "name": "ExitPlanMode",
                    "input": {"plan": "stuff"},
                },
            ]
        ),
        _tool_result_user("tu-plan-2"),
    ]
    assert GateDetector().detect(entries) is None


def test_bare_exitplanmode_string_in_deferred_tools_delta_does_not_false_positive() -> None:
    """The bare string 'ExitPlanMode' inside deferred_tools_delta is NOT a gate.

    research.md: the string appears inside a deferred_tools_delta payload on
    ordinary turns; matching it structurally (tool_use block) avoids the
    false positive that a substring search would hit.
    """
    entries = [
        {
            "type": "system",
            "subtype": "deferred_tools_delta",
            "deferred_tools_delta": {
                "added": ["ExitPlanMode", "AskUserQuestion"],
            },
        },
        _assistant_with_blocks([{"type": "text", "text": "regular answer"}]),
    ]
    assert GateDetector().detect(entries) is None


def _ask_tool_use(tool_use_id: str = "tu-ask-1") -> dict:
    """An AskUserQuestion tool_use with the real Claude Code input shape.

    The input carries ``questions[].options[].label`` — the option list the
    detector reads so the chosen index is known without screen-scraping.
    """
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": "AskUserQuestion",
        "input": {
            "questions": [
                {
                    "question": "Which database should we use?",
                    "header": "Database",
                    "options": [
                        {"label": "Postgres"},
                        {"label": "SQLite"},
                        {"label": "MySQL"},
                    ],
                }
            ]
        },
    }


def test_detect_ask_gate_from_askuserquestion_tool_use_without_result() -> None:
    """An AskUserQuestion tool_use with no matching tool_result is an ask gate."""
    entries = [
        _assistant_with_blocks(
            [
                {"type": "text", "text": "I need a decision."},
                _ask_tool_use(),
            ]
        ),
    ]
    gate = GateDetector().detect(entries)
    assert isinstance(gate, Gate)
    assert gate.kind == "ask"


def test_ask_gate_payload_carries_question_and_option_list() -> None:
    """The option list comes from the tool_use INPUT, not the screen.

    The payload exposes the question text and the ordered option labels so the
    bridge can surface them and map a chosen option to its index.
    """
    entries = [_assistant_with_blocks([_ask_tool_use()])]
    gate = GateDetector().detect(entries)
    assert isinstance(gate, Gate)
    assert gate.payload["question"] == "Which database should we use?"
    assert gate.payload["options"] == ["Postgres", "SQLite", "MySQL"]


def test_detect_returns_none_when_askuserquestion_has_matching_result() -> None:
    """If the AskUserQuestion tool_use has a matching tool_result, it's answered."""
    entries = [
        _assistant_with_blocks([_ask_tool_use("tu-ask-2")]),
        _tool_result_user("tu-ask-2"),
    ]
    assert GateDetector().detect(entries) is None
