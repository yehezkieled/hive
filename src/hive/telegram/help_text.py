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
    display: str | None = None


# Category ordering for /help listing output
CATEGORIES = (
    "Status",
    "Messaging",
    "Tasks",
    "Session",
    "Resources",
    "Security",
    "Knowledge",
    "Admin",
)


HELP_TEXT: dict[str, HelpEntry] = {
    # Status — alphabetical: audit, health, heartbeat, status, wake
    "audit": HelpEntry(
        category="Status",
        usage="/audit [prefix]",
        description="Show the last 20 audit events, filtered by action prefix.",
        examples=("/audit", "/audit task"),
    ),
    "health": HelpEntry(
        category="Status",
        usage="/health",
        description="List any entities in an unhealthy state.",
        examples=("/health",),
    ),
    "heartbeat": HelpEntry(
        category="Status",
        usage="/heartbeat on|off|status|<minutes>",
        description="Send periodic agent-status pings to Telegram.",
        examples=(
            "/heartbeat on",
            "/heartbeat 60",
            "/heartbeat off",
            "/heartbeat status",
        ),
    ),
    "status": HelpEntry(
        category="Status",
        usage="/status",
        description="Show each entity's role, state, and PID.",
        examples=("/status",),
    ),
    "wake": HelpEntry(
        category="Status",
        usage="/wake",
        description="Show whether firstmate is alive; a button restarts its session if it is not.",
        examples=("/wake",),
    ),
    # Messaging — message (the /a: addressing form folds in here)
    "message": HelpEntry(
        category="Messaging",
        usage="/m:<entity> <text> | /a:<entity> <text>",
        description="Send a message to an entity (e.g. the vault). Plain text has no recipient.",
        display="m:",
        examples=(
            "/m:vault status of pending payments",
            "/a:vault status",
        ),
    ),
    # Tasks — alphabetical: task, tasks
    "task": HelpEntry(
        category="Tasks",
        usage="/task add|done|cancel <args>",
        description="Manage the task queue: add, done, or cancel tasks.",
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
        examples=("/tasks",),
    ),
    # Session — alphabetical: compact, kill, loop, mode, reset
    "compact": HelpEntry(
        category="Session",
        usage="/compact <entity>",
        description="Summarize an entity's context to free tokens without losing progress.",
        examples=("/compact vault",),
    ),
    "kill": HelpEntry(
        category="Session",
        usage="/kill <entity>",
        description="Stop an entity's subprocess, which can be respawned by sending a message.",
        examples=("/kill vault",),
    ),
    "mode": HelpEntry(
        category="Session",
        usage="/mode yolo|yotree <entity>",
        description="Set an entity's permission mode (plan mode is via the grill-me skill).",
        examples=("/mode yolo vault",),
    ),
    "reset": HelpEntry(
        category="Session",
        usage="/reset <entity>",
        description="Kill an entity and clear its session, starting fresh on next message.",
        examples=("/reset vault",),
    ),
    # Resources — alphabetical: cost, files, model
    "cost": HelpEntry(
        category="Resources",
        usage="/cost [24h|7d|30d]",
        description="Show token usage and equivalent API cost (covered by the Max subscription).",
        examples=("/cost", "/cost 7d"),
    ),
    "files": HelpEntry(
        category="Resources",
        usage="/files [N]",
        description="List the most recent uploads (default 20, max 100).",
        examples=("/files", "/files 5"),
    ),
    "model": HelpEntry(
        category="Resources",
        usage="/model opus|sonnet|haiku|opusplan|fable [entity]",
        description="Change entity model; an API-billed model prints a billing warning.",
        examples=(
            "/model opus vault",
            "/model sonnet vault",
        ),
    ),
    "quota": HelpEntry(
        category="Resources",
        usage="/quota",
        description="Show plan-quota utilization for the 5h and 7d windows, with reset times.",
        examples=("/quota",),
    ),
    # Security — alphabetical: approve, deny, vault
    "approve": HelpEntry(
        category="Security",
        usage="/approve [mode <id>]",
        description="Approve a pending mode-elevation request (yolo/yotree).",
        examples=("/approve", "/approve mode 7"),
    ),
    "deny": HelpEntry(
        category="Security",
        usage="/deny mode <id> [reason]",
        description="Deny a pending mode-elevation request.",
        examples=('/deny mode 7 "stick to edit for docs"',),
    ),
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
    # Knowledge — single entry
    "blueprint": HelpEntry(
        category="Knowledge",
        usage="/blueprint save|search|list <args>",
        description="Manage semantic blueprints retrieved automatically into agent prompts.",
        examples=(
            '/blueprint save "deploy rollout" "Use blue/green..."',
            "/blueprint search rollout",
            "/blueprint list",
        ),
    ),
    # Admin — help
    "help": HelpEntry(
        category="Admin",
        usage="/help [command]",
        description="Show this help. `/help <command>` shows detail for one command.",
        examples=("/help", "/help vault"),
    ),
}


def format_all() -> str:
    """Format the full /help listing, grouped by category."""
    lines = [
        f"Hive Telegram — {len(HELP_TEXT)} commands across {len(CATEGORIES)} categories."
        f" Use /help <command> for detail.",
        "",
    ]
    for category in CATEGORIES:
        entries = [(name, e) for name, e in HELP_TEXT.items() if e.category == category]
        if not entries:
            continue
        lines.append(f"{category}:")
        for name, entry in sorted(entries):
            shown = entry.display or name
            lines.append(f"  /{shown} — {entry.description}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_one(name: str) -> str:
    """Format detail help for a single command, or a hint if unknown."""
    name = name.strip().lstrip("/").lower()
    entry = HELP_TEXT.get(name)
    if entry is None:
        return f"Unknown command: /{name}. Try /help for the full list."

    shown = entry.display or name
    lines = [
        f"/{shown} — {entry.category}",
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
