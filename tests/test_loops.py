"""Tests for goal seeding (T007 retired LOOP_PROMPTS for native /goal)."""

from hive.process.loops import seed_goal


def test_seed_goal_prefixes_the_slash_command() -> None:
    seeded = seed_goal("build the widget and make the tests pass")
    assert seeded.startswith("/goal ")
    assert "build the widget and make the tests pass" in seeded


def test_seed_goal_keeps_slash_command_at_the_very_start() -> None:
    # A slash command is only recognised when it leads the message.
    seeded = seed_goal("## context block\n\n---\n\nreal task")
    assert seeded.index("/goal ") == 0


def test_loop_prompts_are_gone() -> None:
    import hive.process.loops as loops

    assert not hasattr(loops, "LOOP_PROMPTS")
