"""Loop prompt fragments injected into entity system prompts via --append-system-prompt."""

from __future__ import annotations

MESSAGING_PROMPT = (
    "You can send messages to other entities in the Hive by including a "
    "<hive_actions> block at the end of your response. Format:\n\n"
    "<hive_actions>\n"
    '[{"type": "message", "to": "entity.name", "text": "your message"}]\n'
    "</hive_actions>\n\n"
    "Only use this when you need to delegate work, report results, or "
    "coordinate with another entity. The orchestrator will validate "
    "permissions and deliver the message."
)

LOOP_PROMPTS: dict[str, str] = {
    "ralph": (
        "Follow the RALPH loop: Read requirements, Ask clarifying questions, "
        "List approach options, Plan steps, Halt for review before executing."
    ),
    "ship-it": "Execute immediately without stopping for confirmation. Ship it.",
    "plan-act-observe": (
        "Follow the Plan-Act-Observe cycle: Plan your next step, "
        "Act on it, Observe the result, repeat."
    ),
    "build-test-refine": (
        "Follow Build-Test-Refine: Build the feature, Test it, Refine based on results."
    ),
}
