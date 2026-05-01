"""Telegram command parser for Hive."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Command:
    """A parsed Hive command."""

    name: str
    target: str | None = None
    args: str = ""


def parse_command(text: str, default_maestro: str = "dev") -> Command:
    """Parse a Telegram message into a Command.

    Examples:
        /status          -> Command("status")
        /health          -> Command("health")
        /maestros        -> Command("maestros")
        /org             -> Command("org")
        /comms           -> Command("comms")
        /m:dev do X      -> Command("message", "dev", "do X")
        /t:dev.backend   -> Command("team", "dev.backend")
        /kill dev        -> Command("kill", "dev")
        /compact dev     -> Command("compact", "dev")
        /reset dev       -> Command("reset", "dev")
        /task add "foo"  -> Command("task", "add", '"foo"')
        /task done 5     -> Command("task", "done", "5")
        /tasks           -> Command("tasks")
        /cost 7d         -> Command("cost", args="7d")
        /audit entity    -> Command("audit", args="entity")
        plain text       -> Command("message", default_maestro, "plain text")
    """
    text = text.strip()

    if not text:
        return Command(name="empty")

    # Not a command — plain message to default maestro
    if not text.startswith("/"):
        return Command(name="message", target=default_maestro, args=text)

    # /m:<name> <message> — message a specific maestro
    m_match = re.match(r"^/m:(\w+)\s*(.*)", text, re.DOTALL)
    if m_match:
        return Command(name="message", target=m_match.group(1), args=m_match.group(2).strip())

    # /t:<maestro>.<team> <message> — message a specific team
    t_match = re.match(r"^/t:([\w.]+)\s*(.*)", text, re.DOTALL)
    if t_match:
        return Command(name="team", target=t_match.group(1), args=t_match.group(2).strip())

    # /a:<maestro>.<team>.<agent> <message> — message a specific agent
    a_match = re.match(r"^/a:([\w.]+)\s*(.*)", text, re.DOTALL)
    if a_match:
        return Command(name="agent", target=a_match.group(1), args=a_match.group(2).strip())

    # Commands with a target argument: /kill dev, /compact dev, /reset dev.
    # /task uses the target slot for its subcommand (add|done|cancel), with
    # the rest of the line becoming the args (title or id).
    targeted_commands = {
        "kill",
        "compact",
        "reset",
        "mode",
        "loop",
        "priority",
        "task",
        "team",
        "worker",
        "swarm",
        "new",
        "personality",
        "model",
        "vault",
        "blueprint",
        "help",
        "approve",
        "deny",
        "commit",
        "pr",
        "merge",
        "heartbeat",
        "eval",
        "budget",
    }
    cmd_match = re.match(r"^/(\w+)\s+(.*)", text, re.DOTALL)
    if cmd_match:
        cmd_name = cmd_match.group(1).lower()
        cmd_args = cmd_match.group(2).strip()
        if cmd_name in targeted_commands:
            # First word is the target, rest is args
            parts = cmd_args.split(None, 1)
            target = parts[0] if parts else None
            args = parts[1] if len(parts) > 1 else ""
            return Command(name=cmd_name, target=target, args=args)
        # Other commands with args
        return Command(name=cmd_name, args=cmd_args)

    # Simple commands: /status, /health, /maestros, /org, /comms
    simple_match = re.match(r"^/(\w+)$", text)
    if simple_match:
        return Command(name=simple_match.group(1).lower())

    # Fallback
    return Command(name="unknown", args=text)
