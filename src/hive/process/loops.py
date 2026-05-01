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

AUTONOMY_PROMPT = (
    "You can also grow or shrink the org via the same <hive_actions> "
    "block. Action types available to you:\n\n"
    "- spawn_team (maestro only): "
    '{"type": "spawn_team", "team_name": "<short-name>"}. '
    "Creates a new team in your org. The lead is registered as "
    "<your-name>.<team_name>; you can then message it to give it work.\n"
    "- spawn_worker (maestro or lead): "
    '{"type": "spawn_worker", "lead": "<full.lead.name>", '
    '"worker_name": "<optional>", "task_id": <optional-int>}. '
    "Adds a worker under that lead's team. Auto-names workers w1, w2, "
    "... if worker_name is omitted.\n"
    "- kill_entity (maestro or lead): "
    '{"type": "kill_entity", "target": "<full.entity.name>"}. '
    "Removes an entity from your scope. Cannot kill yourself or the "
    "default maestro.\n\n"
    "Spawn deliberately — there is a per-evaluation rate limit. "
    "When work arrives, prefer spawning a focused team over piling "
    "tasks on an existing entity."
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
