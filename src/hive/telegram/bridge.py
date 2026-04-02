"""Telegram bridge — connects Telegram Bot API to the Hive orchestrator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from hive.telegram.commands import parse_command

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


class TelegramBridge:
    """Bridges Telegram messages to the Hive orchestrator.

    Uses python-telegram-bot for async Bot API polling.
    Only accepts messages from allowed user IDs.
    """

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: list[int],
        process_manager: ProcessManager,
        default_maestro: str = "dev",
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids
        self.process_manager = process_manager
        self.default_maestro = default_maestro
        self._app: Application | None = None

    async def start(self) -> None:
        """Build and start the Telegram bot application."""
        self._app = Application.builder().token(self.bot_token).build()

        # Handle all text messages
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        # Handle commands (anything starting with /)
        self._app.add_handler(MessageHandler(filters.COMMAND, self._handle_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        logger.info("Telegram bridge started, polling for updates")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()  # type: ignore[union-attr]
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bridge stopped")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle an incoming Telegram message."""
        if update.message is None or update.message.text is None:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            logger.warning("Unauthorized message from user %d", user_id)
            return

        text = update.message.text
        logger.info("Received from Telegram (user=%d): %s", user_id, text[:100])

        # Parse command
        cmd = parse_command(text, default_maestro=self.default_maestro)

        # Handle command
        response = await self._execute_command(cmd)

        # Send response back
        if response:
            # Telegram messages have a 4096 char limit
            for chunk in _chunk_text(response, 4096):
                await update.message.reply_text(chunk)

    async def _execute_command(self, cmd) -> str:
        """Execute a parsed command and return the response text."""
        if cmd.name == "empty":
            return ""

        if cmd.name == "status":
            return self._format_status()

        if cmd.name == "health":
            unhealthy = await self.process_manager.health_check()
            if unhealthy:
                return f"Unhealthy entities: {', '.join(unhealthy)}"
            return "All entities healthy."

        if cmd.name == "maestros":
            entities = self.process_manager.entities
            maestros = [e for e in entities.values() if e.role == "maestro"]
            if not maestros:
                return "No maestros running."
            lines = [f"- {m.name} ({m.state.value}, model={m.model})" for m in maestros]
            return "Maestros:\n" + "\n".join(lines)

        if cmd.name == "org":
            return self._format_status()

        if cmd.name == "comms":
            recent = await self.process_manager.router.store.get_recent(limit=10)
            if not recent:
                return "No messages yet."
            lines = []
            for msg in reversed(recent):
                lines.append(f"[{msg['sender']} -> {msg['recipient']}] {msg['content'][:80]}")
            return "Recent comms:\n" + "\n".join(lines)

        if cmd.name == "kill":
            if not cmd.target:
                return "Usage: /kill <entity_name>"
            try:
                await self.process_manager.kill_entity(cmd.target)
                return f"Killed {cmd.target}."
            except Exception as e:
                return f"Error killing {cmd.target}: {e}"

        if cmd.name == "message":
            if not cmd.target:
                return "No target specified."
            return await self._send_to_entity(cmd.target, cmd.args)

        return f"Unknown command: /{cmd.name}"

    async def _send_to_entity(self, entity_name: str, message: str) -> str:
        """Send a message to an entity and return its response."""
        if not message:
            return f"Send what to {entity_name}?"

        try:
            response = await self.process_manager.send_to_entity(entity_name, message)
            # Log the exchange
            await self.process_manager.router.route("user", entity_name, message)
            await self.process_manager.router.route(entity_name, "user", response)
            return response or "(no response)"
        except KeyError:
            return f"Entity {entity_name!r} not found. Use /maestros to see available entities."
        except Exception as e:
            logger.exception("Error sending to %s", entity_name)
            return f"Error: {e}"

    def _format_status(self) -> str:
        """Format entity status for display."""
        statuses = self.process_manager.get_status()
        if not statuses:
            return "No entities running."

        lines = []
        for s in statuses:
            uptime = f", uptime={int(s['uptime'])}s" if s["uptime"] else ""
            pid = f", pid={s['pid']}" if s["pid"] else ""
            lines.append(
                f"- {s['name']} [{s['role']}] {s['state']} (model={s['model']}{pid}{uptime})"
            )
        return "Entities:\n" + "\n".join(lines)


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of max_len characters."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
