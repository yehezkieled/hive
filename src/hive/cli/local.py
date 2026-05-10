"""Local CLI for testing Hive without Telegram."""

from __future__ import annotations

import asyncio
import logging

from hive.bus.router import MessageRouter
from hive.process.manager import ProcessManager
from hive.telegram.commands import parse_command

logger = logging.getLogger(__name__)


class LocalCLI:
    """Interactive CLI that uses the same command syntax as Telegram."""

    def __init__(
        self,
        process_manager: ProcessManager,
        router: MessageRouter,
        default_maestro: str = "pa",
    ) -> None:
        self.process_manager = process_manager
        self.router = router
        self.default_maestro = default_maestro

    async def run(self) -> None:
        """Run the interactive CLI loop."""
        print("Hive CLI — type commands or plain text (Ctrl+C to exit)")
        print("Commands: /status /health /maestros /org /comms /kill <name>")
        print(f"Default maestro: {self.default_maestro}")
        print()

        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, lambda: input("hive> "))
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            line = line.strip()
            if not line:
                continue

            if line in ("/quit", "/exit"):
                print("Exiting...")
                break

            cmd = parse_command(line, default_maestro=self.default_maestro)
            response = await self._execute(cmd)
            if response:
                print(response)
                print()

    async def _execute(self, cmd) -> str:
        """Execute a command — mirrors TelegramBridge._execute_command."""
        if cmd.name == "empty":
            return ""

        if cmd.name == "status":
            return self._format_status()

        if cmd.name == "health":
            unhealthy = await self.process_manager.health_check()
            if unhealthy:
                return f"Unhealthy: {', '.join(unhealthy)}"
            return "All healthy."

        if cmd.name == "maestros":
            entities = self.process_manager.entities
            maestros = [e for e in entities.values() if e.role == "maestro"]
            if not maestros:
                return "No maestros running."
            return "\n".join(f"- {m.name} ({m.state.value})" for m in maestros)

        if cmd.name == "org":
            return self._format_status()

        if cmd.name == "comms":
            recent = await self.router.store.get_recent(limit=10)
            if not recent:
                return "No messages."
            lines = [
                f"[{m['sender']} -> {m['recipient']}] {m['content'][:80]}" for m in reversed(recent)
            ]
            return "\n".join(lines)

        if cmd.name == "kill":
            if not cmd.target:
                return "Usage: /kill <name>"
            await self.process_manager.kill_entity(cmd.target)
            return f"Killed {cmd.target}."

        if cmd.name == "message":
            if not cmd.target or not cmd.args:
                return "Usage: /m:<name> <message> or just type text"
            try:
                print(f"Sending to {cmd.target}...")
                response = await self.process_manager.send_to_entity(cmd.target, cmd.args)
                await self.router.route("user", cmd.target, cmd.args)
                await self.router.route(cmd.target, "user", response)
                return response or "(no response)"
            except KeyError:
                return f"Entity {cmd.target!r} not found."
            except Exception as e:
                return f"Error: {e}"

        return f"Unknown: /{cmd.name}"

    def _format_status(self) -> str:
        statuses = self.process_manager.get_status()
        if not statuses:
            return "No entities."
        lines = [f"- {s['name']} [{s['role']}] {s['state']} (pid={s['pid']})" for s in statuses]
        return "\n".join(lines)
