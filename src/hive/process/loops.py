"""Goal seeding for an entity's first turn (native ``/goal``)."""

from __future__ import annotations


def seed_goal(prompt: str) -> str:
    """Wrap an entity's first-turn prompt as Claude Code's native ``/goal`` (T007).

    Replaces the retired ``LOOP_PROMPTS`` framework. Prefixing ``/goal `` puts
    the slash command at the very start of the message so Claude Code enters its
    completion-condition loop (Haiku evaluator) with the turn's content as the
    goal. Applied once, on the first turn of an activation — see
    ``message_dispatcher.send_to_entity``.
    """
    return f"/goal {prompt}"


def unseed_goal(prompt: str) -> str:
    """Inverse of ``seed_goal`` for harnesses with no native ``/goal`` command.

    The dispatcher seeds ``/goal`` without knowing which harness will run the turn
    (selection happens per turn, in ``HarnessRuntime``); a harness that cannot
    interpret the slash command gets the goal text on its own.
    """
    return prompt.removeprefix("/goal ")
