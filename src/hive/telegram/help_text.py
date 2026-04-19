"""Telegram command help text — single source of truth for /help output.

Every command handled in `bridge._execute_command` has an entry here.
A drift-prevention test (`tests/test_help.py`) asserts every command
dispatched in bridge.py has a matching HELP_TEXT entry and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpEntry:
    """One command's help documentation."""

    category: str
    usage: str
    description: str
    examples: tuple[str, ...] = ()


# Category ordering for /help listing output
CATEGORIES = (
    "Status",
    "Organization",
    "Messaging",
    "Tasks",
    "Session",
    "Resources",
    "Security",
    "Knowledge",
    "Git",
    "Admin",
)


HELP_TEXT: dict[str, HelpEntry] = {
    # Status
    "status": HelpEntry(
        category="Status",
        usage="/status",
        description="Show each entity's role, state, and PID.",
    ),
    "health": HelpEntry(
        category="Status",
        usage="/health",
        description="List any entities in an unhealthy state.",
    ),
    "comms": HelpEntry(
        category="Status",
        usage="/comms",
        description="Show the last 10 inter-entity messages.",
    ),
    "audit": HelpEntry(
        category="Status",
        usage="/audit [prefix]",
        description=(
            "Show the last 20 audit events, optionally filtered by action prefix "
            "(e.g. entity, task, command)."
        ),
        examples=("/audit", "/audit task"),
    ),
    # Organization
    "maestros": HelpEntry(
        category="Organization",
        usage="/maestros",
        description="List registered maestros with their model and state.",
    ),
    "org": HelpEntry(
        category="Organization",
        usage="/org",
        description="Show the full org tree (maestros -> teams -> workers).",
    ),
    "team": HelpEntry(
        category="Organization",
        usage="/team create|list|kill [args]",
        description=(
            "Manage teams under a maestro. `create` spawns a new team lead, `kill` removes a team."
        ),
        examples=(
            "/team create dev.backend",
            "/team list dev",
            "/team kill dev.backend",
        ),
    ),
    "teams": HelpEntry(
        category="Organization",
        usage="/teams",
        description="List all teams across all maestros.",
    ),
    "worker": HelpEntry(
        category="Organization",
        usage="/worker spawn|kill <team> [task_id]",
        description="Spawn or kill a worker in a team. Workers run in an isolated git worktree.",
        examples=(
            "/worker spawn dev.backend",
            "/worker kill dev.backend.w1",
        ),
    ),
    "new": HelpEntry(
        category="Organization",
        usage="/new maestro <name>",
        description="Register a new maestro from personalities/<name>.md.",
        examples=("/new maestro pa",),
    ),
    # Messaging
    "message": HelpEntry(
        category="Messaging",
        usage="/m:<maestro> <text> | <text>",
        description=(
            "Send a message to a maestro. Plain text with no /command prefix "
            "routes to the default maestro."
        ),
        examples=(
            "/m:dev please audit the token usage",
            "hello dev",
        ),
    ),
    "agent": HelpEntry(
        category="Messaging",
        usage="/a:<maestro>.<team>.<worker> <text>",
        description="Send a message directly to a worker.",
        examples=("/a:dev.backend.w1 run pytest",),
    ),
    "broadcast": HelpEntry(
        category="Messaging",
        usage="/broadcast <text>",
        description="Send the same message to every registered entity.",
        examples=("/broadcast sprint demo in 5 minutes",),
    ),
    # Tasks
    "task": HelpEntry(
        category="Tasks",
        usage="/task add|done|cancel <args>",
        description=(
            "Manage the task queue. `add` accepts a quoted title; `done`/`cancel` take a task id."
        ),
        examples=(
            '/task add "Add /help command"',
            "/task done 5",
            "/task cancel 6",
        ),
    ),
    "tasks": HelpEntry(
        category="Tasks",
        usage="/tasks",
        description="List pending and in-progress tasks.",
    ),
    "priority": HelpEntry(
        category="Tasks",
        usage='/priority P0|P1|P2|P3|P4 "<title>"',
        description="Create a task at the given priority (P0 = urgent, P4 = backlog).",
        examples=('/priority P0 "fix prod outage"',),
    ),
    "swarm": HelpEntry(
        category="Tasks",
        usage="/swarm <maestro.team> <goal>",
        description="Send the same goal to every worker in a team.",
        examples=("/swarm dev.backend finish the /help command",),
    ),
    # Session
    "mode": HelpEntry(
        category="Session",
        usage="/mode plan|edit|auto|yolo|yotree <entity>",
        description=(
            "Set an entity's permission mode. yolo/yotree require approval "
            "unless the entity is directly user-owned."
        ),
        examples=(
            "/mode plan dev",
            "/mode yotree dev.backend",
        ),
    ),
    "loop": HelpEntry(
        category="Session",
        usage="/loop ralph|ship-it|plan-act-observe|build-test-refine <entity>",
        description="Set the workflow loop framework for an entity.",
        examples=("/loop ralph dev", "/loop ship-it dev"),
    ),
    "compact": HelpEntry(
        category="Session",
        usage="/compact <entity>",
        description=(
            "Summarize an entity's context and restart it with the summary "
            "seeded — frees tokens without losing progress."
        ),
    ),
    "reset": HelpEntry(
        category="Session",
        usage="/reset <entity>",
        description=(
            "Kill an entity and clear its session id. Next message starts a fresh Claude session."
        ),
    ),
    "kill": HelpEntry(
        category="Session",
        usage="/kill <entity>",
        description=(
            "Stop an entity's subprocess. Entity stays registered and can be "
            "respawned by sending a message."
        ),
    ),
    # Resources
    "cost": HelpEntry(
        category="Resources",
        usage="/cost [24h|7d|30d]",
        description="Show token usage and equivalent API cost (covered by the Max subscription).",
        examples=("/cost", "/cost 7d"),
    ),
    "model": HelpEntry(
        category="Resources",
        usage="/model opus|sonnet|haiku|opusplan [entity]",
        description=(
            "Change entity model. opusplan plans with Opus and executes with Sonnet."
        ),
        examples=(
            "/model opus dev",
            "/model sonnet dev.backend",
            "/model haiku dev.backend.w1",
            "/model opusplan dev.backend",
        ),
    ),
    # Security
    "vault": HelpEntry(
        category="Security",
        usage="/vault approve|deny|status|log <id>",
        description="Manage payment approvals. Vault actions always require manual approval.",
        examples=(
            "/vault status",
            "/vault approve 3",
            "/vault log",
        ),
    ),
    "approve": HelpEntry(
        category="Security",
        usage="/approve [mode <id>]",
        description=(
            "Approve a pending mode-elevation request (yolo/yotree). "
            "With no id, lists pending requests addressed to you."
        ),
        examples=("/approve", "/approve mode 7"),
    ),
    "deny": HelpEntry(
        category="Security",
        usage="/deny mode <id> [reason]",
        description="Deny a pending mode-elevation request.",
        examples=('/deny mode 7 "stick to edit for docs"',),
    ),
    # Knowledge
    "blueprint": HelpEntry(
        category="Knowledge",
        usage="/blueprint save|search|list <args>",
        description=(
            "Manage semantic blueprints — reusable pattern docs retrieved "
            "automatically into agent prompts."
        ),
        examples=(
            '/blueprint save "deploy rollout" "Use blue/green..."',
            "/blueprint search rollout",
            "/blueprint list",
        ),
    ),
    # Git
    "commit": HelpEntry(
        category="Git",
        usage='/commit <entity> "<message>"',
        description="Stage all changes in an entity's worktree and commit with the given message.",
        examples=('/commit dev.backend.w1 "add retry logic"',),
    ),
    "pr": HelpEntry(
        category="Git",
        usage='/pr <entity> ["<title>"]',
        description=(
            "Push the entity's branch to origin and open a pull request with `gh pr create`. "
            "Title defaults to the last commit's subject."
        ),
        examples=("/pr dev.backend.w1", '/pr dev.backend.w1 "retry on transient errors"'),
    ),
    "merge": HelpEntry(
        category="Git",
        usage="/merge <entity>",
        description=(
            "Squash-merge the PR for the entity's branch. Disabled unless "
            "HIVE_ALLOW_AUTO_MERGE=1 is set in the environment."
        ),
        examples=("/merge dev.backend.w1",),
    ),
    # Admin
    "personality": HelpEntry(
        category="Admin",
        usage="/personality reload <entity>",
        description="Re-read a personality .md file and apply it to the entity.",
        examples=("/personality reload dev",),
    ),
    "help": HelpEntry(
        category="Admin",
        usage="/help [command]",
        description="Show this help. `/help <command>` shows detail for one command.",
        examples=("/help", "/help vault"),
    ),
}


def format_all() -> str:
    """Format the full /help listing, grouped by category."""
    lines = ["Hive Telegram commands — use `/help <command>` for detail.", ""]
    for category in CATEGORIES:
        entries = [(name, e) for name, e in HELP_TEXT.items() if e.category == category]
        if not entries:
            continue
        lines.append(f"{category}:")
        for name, entry in sorted(entries):
            lines.append(f"  /{name} — {entry.description}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_one(name: str) -> str:
    """Format detail help for a single command, or a hint if unknown."""
    name = name.strip().lstrip("/").lower()
    entry = HELP_TEXT.get(name)
    if entry is None:
        return f"Unknown command: /{name}. Try /help for the full list."

    lines = [
        f"/{name} — {entry.category}",
        "",
        f"Usage: {entry.usage}",
        "",
        entry.description,
    ]
    if entry.examples:
        lines.append("")
        lines.append("Examples:")
        for ex in entry.examples:
            lines.append(f"  {ex}")
    return "\n".join(lines)
