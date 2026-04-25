"""Telegram bridge — connects Telegram Bot API to the Hive orchestrator.

Since Sprint 15 the bridge is a thin transport adapter: incoming Telegram
text is parsed and handed to a :class:`CommandDispatcher` (shared with
the web write surface), and only Telegram-specific state — the heartbeat
toggle/interval, daily-summary formatting, the `_send_notification`
sink — lives here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from hive.commands.dispatch import KNOWN_COMMANDS, CommandDispatcher
from hive.models.task import TaskStatus
from hive.telegram.commands import parse_command

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.knowledge.blueprints import BlueprintStore
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


# Bridge surface: dispatcher commands plus Telegram-only commands
# (currently just /heartbeat). Source of truth for the /help drift test
# in tests/test_help.py — keep in sync if a new transport-only command
# is ever added here.
BRIDGE_COMMANDS: frozenset[str] = KNOWN_COMMANDS | frozenset({"heartbeat"})


class TelegramBridge:
    """Bridges Telegram messages to the Hive orchestrator.

    Uses python-telegram-bot for async Bot API polling. Only accepts
    messages from allowed user IDs. Command execution is delegated to
    a :class:`CommandDispatcher`; this class owns transport concerns.
    """

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: list[int],
        process_manager: ProcessManager,
        default_maestro: str = "dev",
        token_store: TokenStore | None = None,
        task_store: TaskStore | None = None,
        audit_log: AuditLog | None = None,
        vault_store: VaultStore | None = None,
        mode_request_store: ModeRequestStore | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids
        self.process_manager = process_manager
        self.default_maestro = default_maestro
        self.token_store = token_store
        self.task_store = task_store
        self.audit_log = audit_log
        self.vault_store = vault_store
        self.mode_request_store = mode_request_store
        self._app: Application | None = None

        self.dispatcher = CommandDispatcher(
            process_manager=process_manager,
            default_maestro=default_maestro,
            token_store=token_store,
            task_store=task_store,
            audit_log=audit_log,
            vault_store=vault_store,
            mode_request_store=mode_request_store,
            blueprint_store=None,
        )

        # Heartbeat (Sprint 13) — in-memory state, resets on restart
        from hive.config import HEARTBEAT_ENABLED, HEARTBEAT_INTERVAL_MINUTES

        self.heartbeat_enabled: bool = HEARTBEAT_ENABLED
        self.heartbeat_interval_minutes: int = HEARTBEAT_INTERVAL_MINUTES

    @property
    def blueprint_store(self) -> BlueprintStore | None:
        """Blueprint store proxy — actual storage lives on the dispatcher.

        Kept as a property so legacy callers (`__main__.py` post-injects
        via ``bridge.blueprint_store = bp``) continue to work.
        """
        return self.dispatcher.blueprint_store

    @blueprint_store.setter
    def blueprint_store(self, value: BlueprintStore | None) -> None:
        self.dispatcher.blueprint_store = value

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

        # Register notification callback so ProcessManager can send proactive alerts
        self.process_manager.set_notification_callback(self._send_notification)
        logger.info("Telegram bridge started, polling for updates")

    async def _send_notification(self, message: str) -> None:
        """Send a proactive notification to the configured chat."""
        from hive.config import SUMMARY_CHAT_ID

        if not SUMMARY_CHAT_ID or self._app is None:
            logger.warning("Cannot send notification: no SUMMARY_CHAT_ID or app not started")
            return
        try:
            await self._app.bot.send_message(chat_id=int(SUMMARY_CHAT_ID), text=message)
        except Exception:
            logger.exception("Failed to send notification")

    async def format_daily_summary(self) -> str:
        """Build a Markdown daily summary covering the last 24 hours."""
        now = datetime.now(UTC)
        since = now - timedelta(hours=24)
        lines = ["Daily Hive Summary", ""]

        # Active entities
        statuses = self.process_manager.get_status()
        lines.append(f"Entities: {len(statuses)} registered")
        for s in statuses:
            lines.append(f"  {s['name']} [{s['role']}] {s['state']}")

        # Tasks completed in last 24h
        if self.task_store:
            completed = await self.task_store.list(status=TaskStatus.COMPLETED)
            recent_completed = [t for t in completed if t.completed_at and t.completed_at >= since]
            lines.append(f"\nTasks completed (24h): {len(recent_completed)}")
            for t in recent_completed[:10]:
                lines.append(f"  #{t.id} {t.title}")

        # Token usage / cost
        if self.token_store:
            totals = await self.token_store.totals(since=since)
            calls = int(totals.get("call_count", 0))
            cost = float(totals.get("cost_usd", 0) or 0)
            in_tok = int(totals.get("input_tokens", 0))
            out_tok = int(totals.get("output_tokens", 0))
            lines.append(f"\nToken usage (24h): {calls} calls")
            lines.append(f"  input: {in_tok:,}  output: {out_tok:,}")
            lines.append(f"  cost: ${cost:.4f} (API equivalent)")

        # Errors in last 24h
        if self.audit_log:
            errors = await self.audit_log.recent(limit=50, action_prefix="entity.error")
            recent_errors = [e for e in errors if e["timestamp"] >= since]
            if recent_errors:
                lines.append(f"\nErrors (24h): {len(recent_errors)}")
                for err in recent_errors[:5]:
                    lines.append(f"  {err['target']} at {err['timestamp']:%H:%M}")

        return "\n".join(lines)

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
        actor = f"user:{user_id}"
        response = await self._execute_command(cmd, actor=actor)

        # Audit every command — over-logging is cheaper to filter later.
        if self.audit_log is not None and cmd.name != "empty":
            await self.audit_log.record(
                actor=actor,
                action=f"command.{cmd.name}",
                target=cmd.target,
                details={"args": (cmd.args or "")[:200]},
            )

        # Send response back
        if response:
            # Telegram messages have a 4096 char limit
            for chunk in _chunk_text(response, 4096):
                await update.message.reply_text(chunk)

    async def _execute_command(self, cmd, actor: str = "system") -> str:
        """Execute a parsed command and return the response text.

        Heartbeat is handled here because it mutates bridge-local state
        (toggle + interval); every other command delegates to the
        :class:`CommandDispatcher`.
        """
        if cmd.name == "heartbeat":
            return await self._execute_heartbeat(cmd.target, cmd.args)

        result = await self.dispatcher.dispatch_command(cmd, actor=actor)
        return result.text

    def format_heartbeat(self) -> str:
        """Format a compact heartbeat status message."""
        statuses = self.process_manager.get_status()
        running = [s for s in statuses if s["alive"]]
        errors = [s for s in statuses if s["state"] == "error"]

        header = (
            f"Heartbeat — {self.heartbeat_interval_minutes}m interval\n"
            f"{len(running)} agent(s) running" + (f", {len(errors)} error(s)." if errors else ".")
        )

        if not statuses:
            return f"Heartbeat — {self.heartbeat_interval_minutes}m interval\nNo agents registered."

        lines = [header]
        for s in statuses:
            uptime = s.get("uptime")
            if uptime:
                hours, rem = divmod(int(uptime), 3600)
                mins = rem // 60
                uptime_str = f"{hours}h {mins}m" if hours else f"{mins}m"
                uptime_part = f" (uptime {uptime_str})"
            else:
                uptime_part = ""
            state = s["state"].upper() if isinstance(s["state"], str) else s["state"].value.upper()
            lines.append(f"- {s['name']} [{s['role']}] {state}{uptime_part}")
        return "\n".join(lines)

    async def _execute_heartbeat(self, target: str | None, args: str) -> str:
        """Handle /heartbeat on|off|status|<minutes> [minutes]."""
        sub = (target or "").strip().lower()

        if sub == "on":
            self.heartbeat_enabled = True
            if args.strip().isdigit():
                self.heartbeat_interval_minutes = int(args.strip())
            return (
                f"Heartbeat enabled (interval: {self.heartbeat_interval_minutes}m). "
                "Note: HIVE_HEARTBEAT_ENABLED=false in .env will override on restart."
            )

        if sub == "off":
            self.heartbeat_enabled = False
            return "Heartbeat disabled."

        if sub == "status":
            state = "enabled" if self.heartbeat_enabled else "disabled"
            return f"Heartbeat {state}, interval {self.heartbeat_interval_minutes}m."

        if sub.isdigit():
            self.heartbeat_interval_minutes = int(sub)
            return (
                f"Heartbeat interval set to {sub}m. Change takes effect on the next scheduled tick."
            )

        return "Usage: /heartbeat on|off|status|<minutes>"


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of max_len characters."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
