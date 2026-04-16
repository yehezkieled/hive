"""Tests for loop prompt definitions."""

from hive.process.loops import LOOP_PROMPTS


def test_loop_prompts_has_expected_keys() -> None:
    assert set(LOOP_PROMPTS.keys()) == {
        "ralph",
        "yolo",
        "plan-act-observe",
        "build-test-refine",
    }


def test_all_loop_prompts_are_nonempty_strings() -> None:
    for name, prompt in LOOP_PROMPTS.items():
        assert isinstance(prompt, str), f"{name} is not a string"
        assert len(prompt) > 10, f"{name} prompt too short"
