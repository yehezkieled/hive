"""Loop prompt fragments injected into entity system prompts via --append-system-prompt."""

from __future__ import annotations

LOOP_PROMPTS: dict[str, str] = {
    "ralph": (
        "Follow the RALPH loop: Read requirements, Ask clarifying questions, "
        "List approach options, Plan steps, Halt for review before executing."
    ),
    "yolo": "Execute immediately without stopping for confirmation. Ship it.",
    "plan-act-observe": (
        "Follow the Plan-Act-Observe cycle: Plan your next step, "
        "Act on it, Observe the result, repeat."
    ),
    "build-test-refine": (
        "Follow Build-Test-Refine: Build the feature, Test it, Refine based on results."
    ),
}
