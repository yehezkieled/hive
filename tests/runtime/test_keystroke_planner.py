"""Tests for KeystrokePlanner — resolved decision -> exact keystrokes.

The one TUI-layout-coupled module (ADR 0001's known sensitivity). For the
plan gate: approve presses Enter on the default (first) row; deny navigates
down to the reject row then Enter. Everything else stays transcript-only.
"""

from __future__ import annotations

import pytest

from hive.runtime.gates import Gate, KeystrokePlanner

_ENTER = "\r"
_DOWN = "\x1b[B"


def _plan_gate() -> Gate:
    return Gate(kind="plan", payload={"plan": "1. do X"})


def test_plan_approve_presses_enter_on_default_row() -> None:
    keys = KeystrokePlanner().plan_keys(_plan_gate(), "approve")
    assert keys == [_ENTER]


def test_plan_deny_navigates_to_reject_row_then_enter() -> None:
    """The plan menu's reject row is the last of yes-auto/yes-manual/no."""
    keys = KeystrokePlanner().plan_keys(_plan_gate(), "deny")
    assert keys == [_DOWN, _DOWN, _ENTER]


def test_unknown_decision_raises() -> None:
    with pytest.raises(ValueError, match="decision"):
        KeystrokePlanner().plan_keys(_plan_gate(), "maybe")


def _ask_gate() -> Gate:
    return Gate(
        kind="ask",
        payload={
            "question": "Which database?",
            "options": ["Postgres", "SQLite", "MySQL"],
        },
    )


def test_ask_first_option_presses_enter_on_default_row() -> None:
    """Option index 0 is already highlighted; just Enter, no navigation."""
    keys = KeystrokePlanner().ask_keys(_ask_gate(), 0)
    assert keys == [_ENTER]


def test_ask_option_index_maps_to_down_times_index_then_enter() -> None:
    """Choosing option N = Down x N then Enter (cortexOS selectOption pattern)."""
    keys = KeystrokePlanner().ask_keys(_ask_gate(), 2)
    assert keys == [_DOWN, _DOWN, _ENTER]


def test_ask_negative_index_raises() -> None:
    with pytest.raises(ValueError, match="option index"):
        KeystrokePlanner().ask_keys(_ask_gate(), -1)


def test_ask_index_out_of_range_raises() -> None:
    """An index past the option list is rejected rather than over-navigating."""
    with pytest.raises(ValueError, match="option index"):
        KeystrokePlanner().ask_keys(_ask_gate(), 3)
