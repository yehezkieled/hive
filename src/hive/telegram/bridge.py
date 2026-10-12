"""Telegram bridge — connects Telegram Bot API to the Hive orchestrator.

Since Sprint 15 the bridge is a thin transport adapter: incoming Telegram
text is parsed and handed to a :class:`CommandDispatcher` (shared with
the web write surface), and only Telegram-specific state — the heartbeat
toggle/interval, daily-summary formatting, the `_send_notification`
sink — lives here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from hive.commands.dispatch import KNOWN_COMMANDS, CommandDispatcher
from hive.config import UPLOAD_MAX_BYTES, UPLOADS_DIR
from hive.gateway.settings import GatewaySettings
from hive.gateway.wake import WakeService
from hive.gateway.watchdog import ON_DETAIL, ConfigError, WatchdogStore
from hive.knowledge.attachment_embedder import embed_attachment
from hive.models.task import TaskStatus
from hive.notifications import ALERT_KINDS, QUOTA_KINDS, Notification
from hive.telegram.commands import Command, parse_command

if TYPE_CHECKING:
    from hive.bus.attachment_store import AttachmentStore
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
BRIDGE_COMMANDS: frozenset[str] = KNOWN_COMMANDS | frozenset({"heartbeat", "wake", "watchdog"})

WAKE_CALLBACK = "wake:firstmate"
RESTART_CALLBACK = "wake:restart"
RESTART_CONFIRM_PREFIX = "wake:restart:confirm:"
RESTART_CANCEL_PREFIX = "wake:restart:cancel:"
RESTART_CONFIRM_TTL_S = 120.0
WATCHDOG_CONFIRM_PREFIX = "wake:watchdog-on:confirm:"
WATCHDOG_CANCEL_PREFIX = "wake:watchdog-on:cancel:"
WATCHDOG_RELAY_S = 15.0
WATCHDOG_CURSOR = "watchdog-telegram-cursor"  # the last watchdog event this bot has sent
RESTART_CONFIRM_TEXT = "This stops the running firstmate session and starts a new one. Confirm?"
WATCHDOG_CONFIRM_TEXT = (
    "Turn on automatic recovery? This lets Hive stop and start the firstmate session by "
    f"itself. {ON_DETAIL} Confirm?"
)


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
        token_store: TokenStore | None = None,
        task_store: TaskStore | None = None,
        audit_log: AuditLog | None = None,
        vault_store: VaultStore | None = None,
        mode_request_store: ModeRequestStore | None = None,
        attachment_store: AttachmentStore | None = None,
        wake: WakeService | None = None,
        watchdog_store: WatchdogStore | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids
        self.process_manager = process_manager
        self.token_store = token_store
        self.task_store = task_store
        self.audit_log = audit_log
        self.vault_store = vault_store
        self.mode_request_store = mode_request_store
        self.attachment_store = attachment_store
        self._app: Application | None = None
        self.wake = wake or WakeService(GatewaySettings.from_env())
        self.watchdog_store = watchdog_store or WatchdogStore(self.wake.settings.data_dir)
        self._relay_task: asyncio.Task[None] | None = None
        # Pending confirms: token -> (chat id, confirm message id, monotonic expiry)
        self._restart_confirms: dict[str, tuple[int, int, float]] = {}
        self._watchdog_confirms: dict[str, tuple[int, int, float]] = {}

        self.dispatcher = CommandDispatcher(
            process_manager=process_manager,
            token_store=token_store,
            task_store=task_store,
            audit_log=audit_log,
            vault_store=vault_store,
            mode_request_store=mode_request_store,
            blueprint_store=None,
            attachment_store=attachment_store,
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
        # Sprint 17 — file transit. Photos arrive as PhotoSize lists (compressed JPEG);
        # documents (any file uploaded as "Send file") arrive via filters.Document.ALL.
        self._app.add_handler(CallbackQueryHandler(self._handle_callback, pattern="^wake:"))
        self._app.add_handler(MessageHandler(filters.PHOTO, self._handle_attachment))
        self._app.add_handler(MessageHandler(filters.Document.ALL, self._handle_attachment))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        logger.info("Telegram bridge started, polling for updates")
        self._relay_task = asyncio.get_running_loop().create_task(self._relay_watchdog())

    async def send(self, notification: Notification) -> None:
        """NotificationChannel implementation — delivers to the Telegram chat.

        When the alert role is turned down (``HIVE_TELEGRAM_ALERTS=false``,
        Ticket 041), the actionable alert-kinds are dropped here — they reach you
        via Web Push instead — while Telegram keeps relaying everything else as a
        debug/log surface.

        Plan-quota alerts are gated separately by ``HIVE_TELEGRAM_QUOTA_ALERTS``:
        they are ambient rather than actionable, so silencing the quota pings must
        not silence decisions/approvals (and vice versa).
        """
        from hive.config import TELEGRAM_ALERTS, TELEGRAM_QUOTA_ALERTS

        if not TELEGRAM_ALERTS and notification.kind in ALERT_KINDS:
            return
        if not TELEGRAM_QUOTA_ALERTS and notification.kind in QUOTA_KINDS:
            return
        await self._send_notification(notification.text)

    async def _send_notification(self, message: str) -> None:
        """Send a proactive notification to the configured chat."""
        from hive.config import SUMMARY_CHAT_ID

        if not SUMMARY_CHAT_ID or self._app is None:
            logger.warning("Cannot send notification: no SUMMARY_CHAT_ID or app not started")
            return
        try:
            await self._app.bot.send_message(chat_id=int(SUMMARY_CHAT_ID), text=message)
        except TelegramError:
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
        if self._relay_task is not None:
            self._relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._relay_task
            self._relay_task = None
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
        cmd = parse_command(text)
        if cmd.name == "wake":
            if self._may_wake(user_id):
                await self._send_wake_status(update)
            return

        if cmd.name == "watchdog":
            if self._may_wake(user_id):
                await self._watchdog_command(update.message, cmd.args, user_id)
            return

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

    async def _watchdog_command(self, message: Message, args: str | None, user_id: int) -> None:
        """``/watchdog on|off|status``: the allowlisted chat's switch for automatic recovery.

        Off is one step (the kill switch). On, like the desk, never happens on the command
        itself: it posts a confirm bound to its chat and message, and only that confirm, once
        and within RESTART_CONFIRM_TTL_S, turns it on.
        """
        word = (args or "status").strip().lower()
        if word not in ("on", "off", "status"):
            await message.reply_text("Usage: /watchdog on|off|status")
        elif word == "status":
            view = await asyncio.to_thread(self.watchdog_store.view)
            extra = view.config_error or view.halted_reason
            await message.reply_text(
                f"Automatic recovery: {view.label}" + (f" ({extra})" if extra else "")
            )
        elif word == "off":
            await message.reply_text(await self._switch_watchdog(False, f"telegram:{user_id}"))
        else:
            _cfg, err = await asyncio.to_thread(self.watchdog_store.load_config)
            if err:
                await message.reply_text(f"Not turned on: {err}. Fix the watchdog config first.")
                return
            await self._ask_confirm(
                message,
                WATCHDOG_CONFIRM_TEXT,
                WATCHDOG_CONFIRM_PREFIX,
                WATCHDOG_CANCEL_PREFIX,
                self._watchdog_confirms,
            )

    async def _switch_watchdog(self, on: bool, who: str) -> str:
        try:
            text = await asyncio.to_thread(self.watchdog_store.set_enabled, on, who, "telegram")
        except ConfigError as exc:
            return f"Not turned on: {exc}. Fix the watchdog config first."
        self.wake.audit("watchdog-switch", who, "telegram", "on" if on else "off", text)
        return text

    def _relay_targets(self) -> list[int]:
        from hive.config import SUMMARY_CHAT_ID

        return [int(SUMMARY_CHAT_ID)] if SUMMARY_CHAT_ID else list(self.allowed_user_ids)

    async def _relay_watchdog(self) -> None:
        """Send each new watchdog event to the chat. The gateway writes them, this bot (which
        owns the token) delivers them; the cursor file makes a restart neither skip nor repeat."""
        while True:
            try:
                await self._relay_watchdog_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("watchdog relay failed")
            await asyncio.sleep(WATCHDOG_RELAY_S)

    async def _relay_watchdog_once(self) -> None:
        cursor_path = self.wake.settings.data_dir / WATCHDOG_CURSOR
        store = self.watchdog_store
        try:
            cursor = int(cursor_path.read_text().strip())
        except (OSError, ValueError):
            # First run: start from now, so history is not replayed into the chat.
            cursor = await asyncio.to_thread(store.last_event_id)
            await asyncio.to_thread(self._save_cursor, cursor_path, cursor)
        for event in await asyncio.to_thread(store.events, cursor):
            text = f"Hive watchdog: {event.get('text', '')}"
            sent = False
            for chat_id in self._relay_targets():
                if self._app is None:
                    return
                try:
                    await self._app.bot.send_message(chat_id=chat_id, text=text)
                    sent = True
                except TelegramError:
                    logger.exception("could not send a watchdog event to chat %s", chat_id)
            if not sent:
                return  # try this event again on the next round
            cursor = int(event["id"])
            await asyncio.to_thread(self._save_cursor, cursor_path, cursor)

    @staticmethod
    def _save_cursor(path: Path, value: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(str(value))
        tmp.replace(path)

    def _may_wake(self, user_id: int) -> bool:
        """Wake is stricter than the other commands: an empty allowlist allows nobody."""
        return bool(self.allowed_user_ids) and user_id in self.allowed_user_ids

    async def _send_wake_status(self, update: Update) -> None:
        if update.message is None:
            return
        status = await self.wake.status.fresh()
        button = None
        if status.can_wake:
            button = InlineKeyboardButton("Wake firstmate", callback_data=WAKE_CALLBACK)
        elif status.can_restart:
            button = InlineKeyboardButton(
                "Restart session (stops the running one)", callback_data=RESTART_CALLBACK
            )
        markup = InlineKeyboardMarkup([[button]]) if button else None
        await update.message.reply_text(f"Firstmate {status.describe()}", reply_markup=markup)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """The Wake, Restart and recovery-switch buttons. Anyone off the allowlist gets no reply
        and no effect.

        Every tapped button is removed. Restart never runs on the first tap: it posts a confirm
        bound to its chat and message, and only that confirm, once and within
        RESTART_CONFIRM_TTL_S, restarts. ``/watchdog on`` confirms the same way.
        """
        query = update.callback_query
        if query is None:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        data = query.data or ""
        known = data in (WAKE_CALLBACK, RESTART_CALLBACK) or data.startswith(
            (
                RESTART_CONFIRM_PREFIX,
                RESTART_CANCEL_PREFIX,
                WATCHDOG_CONFIRM_PREFIX,
                WATCHDOG_CANCEL_PREFIX,
            )
        )
        if not self._may_wake(user_id) or not known:
            logger.warning("Unauthorized wake callback from user %d", user_id)
            return
        await query.answer("Waking firstmate…" if data == WAKE_CALLBACK else "")
        with contextlib.suppress(TelegramError):
            await query.edit_message_reply_markup(reply_markup=None)
        message = query.message
        if message is None:
            return
        who = f"telegram:{user_id}"
        if data == WAKE_CALLBACK:
            result = await self.wake.wake(who, "telegram")
            await message.reply_text(f"Wake firstmate: {result.outcome}. {result.message}")
        elif data == RESTART_CALLBACK:
            await self._ask_restart_confirm(message)
        elif data.startswith((WATCHDOG_CONFIRM_PREFIX, WATCHDOG_CANCEL_PREFIX)):
            if not self._take_confirm(self._watchdog_confirms, data.rsplit(":", 1)[1], message):
                await message.reply_text(
                    "This confirm has expired, was already used or belongs to another chat. "
                    "Automatic recovery was not changed. Send /watchdog on to start again."
                )
            elif data.startswith(WATCHDOG_CANCEL_PREFIX):
                await message.reply_text("Cancelled. Automatic recovery was not changed.")
            else:
                await message.reply_text(await self._switch_watchdog(True, who))
        elif not self._take_confirm(self._restart_confirms, data.rsplit(":", 1)[1], message):
            await message.reply_text(
                "This restart confirm has expired, was already used or belongs to another "
                "chat. Nothing was stopped. Send /wake to start again."
            )
        elif data.startswith(RESTART_CANCEL_PREFIX):
            await message.reply_text("Restart cancelled. Nothing was stopped.")
        else:
            result = await self.wake.restart(who, "telegram")
            await message.reply_text(f"Restart session: {result.outcome}. {result.message}")

    async def _ask_restart_confirm(self, message: Message) -> None:
        status = await self.wake.status.fresh()
        if not status.can_restart:
            await message.reply_text(
                f"Firstmate {status.describe()}. Restart is no longer offered; send /wake again."
            )
            return
        await self._ask_confirm(
            message,
            RESTART_CONFIRM_TEXT,
            RESTART_CONFIRM_PREFIX,
            RESTART_CANCEL_PREFIX,
            self._restart_confirms,
        )

    @staticmethod
    async def _ask_confirm(
        message: Message,
        text: str,
        confirm_prefix: str,
        cancel_prefix: str,
        confirms: dict[str, tuple[int, int, float]],
    ) -> None:
        """Post a Confirm / Cancel message and remember its token, bound to that message."""
        now = time.monotonic()
        for t in [t for t, entry in confirms.items() if entry[2] <= now]:
            del confirms[t]
        token = secrets.token_urlsafe(16)
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm", callback_data=confirm_prefix + token),
                    InlineKeyboardButton("Cancel", callback_data=cancel_prefix + token),
                ]
            ]
        )
        sent = await message.reply_text(text, reply_markup=markup)
        confirms[token] = (sent.chat_id, sent.message_id, now + RESTART_CONFIRM_TTL_S)

    @staticmethod
    def _take_confirm(
        confirms: dict[str, tuple[int, int, float]], token: str, message: Message
    ) -> bool:
        """Spend a live confirm token issued on this very message; anything else spends nothing."""
        entry = confirms.get(token)
        if entry is None or entry[:2] != (message.chat_id, message.message_id):
            return False
        del confirms[token]
        return time.monotonic() < entry[2]

    async def _handle_attachment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle PHOTO + DOCUMENT messages — Sprint 17 file transit.

        Downloads the file to ``UPLOADS_DIR``, persists metadata, and (if
        the caption parses to a routable command — /m: or /a:) forwards the prompt
        to the target entity with the file's absolute path injected as a context
        prefix. Captions that don't
        route to an entity get a hint reply instead.
        """
        if update.message is None:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            logger.warning("Unauthorized attachment from user %d", user_id)
            return

        if self.attachment_store is None:
            logger.warning("Attachment received but attachment_store not configured")
            await update.message.reply_text("Attachments not configured on this server.")
            return

        # Resolve the source object (PhotoSize or Document) and metadata.
        original_name: str | None
        mime_type: str | None
        if update.message.photo:
            tg_file = update.message.photo[-1]  # largest resolution
            original_name = None
            mime_type = "image/jpeg"
        elif update.message.document is not None:
            doc = update.message.document
            tg_file = doc
            original_name = doc.file_name
            mime_type = doc.mime_type
        else:
            return  # filter shouldn't dispatch here, but be safe

        size_bytes = tg_file.file_size
        if size_bytes is not None and size_bytes > UPLOAD_MAX_BYTES:
            await update.message.reply_text(
                f"File too large ({size_bytes} bytes). Max is {UPLOAD_MAX_BYTES} bytes "
                f"({UPLOAD_MAX_BYTES // (1024 * 1024)} MB)."
            )
            return

        # Pick an extension: filename takes precedence, else mime guess, else .bin
        ext = ""
        if original_name:
            ext = Path(original_name).suffix
        if not ext and mime_type:
            ext = mimetypes.guess_extension(mime_type) or ""
        if not ext:
            ext = ".bin"

        filename = f"{uuid.uuid4().hex}{ext}"
        target = UPLOADS_DIR / filename

        try:
            tg_obj = await context.bot.get_file(tg_file.file_id)
            await tg_obj.download_to_drive(target)
        except (TelegramError, OSError):
            logger.exception("Failed to download Telegram attachment")
            await update.message.reply_text("Failed to download the file. Try again.")
            return

        caption = update.message.caption or ""
        cmd = parse_command(caption)

        # Only route attachments through routable commands. Other commands
        # (/status, /task, etc.) ignore the file entirely so the file is at
        # least retrievable later via /files.
        routable = cmd.name == "message" and cmd.target

        forwarded_to = cmd.target if routable else None
        attachment_id = await self.attachment_store.save(
            file_path=str(target),
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            source="telegram",
            actor=f"user:{user_id}",
            forwarded_to=forwarded_to,
        )

        try:
            chunks = await embed_attachment(str(target), mime_type)
            if chunks:
                await self.attachment_store.save_chunks(attachment_id, chunks)
        except Exception:
            logger.exception("Failed to embed Telegram upload %s", target)

        if not routable:
            await update.message.reply_text(
                f"📎 File received and stored as attachment #{attachment_id}.\n"
                f"Add a caption like `/m:<entity> <text>` to forward "
                "the file to an agent."
            )
            return

        # Build the prompt prefix. The agent will see the absolute path and
        # can use Read/Bash to consume it (yolo permission mode bypasses prompts).
        meta_parts = [mime_type or "unknown"]
        if size_bytes is not None:
            meta_parts.append(f"{size_bytes} bytes")
        if original_name:
            meta_parts.append(f"original: {original_name}")
        prefix = f"[Attached file: {target} ({', '.join(meta_parts)})]\n\n"
        enriched_args = prefix + cmd.args

        actor = f"user:{user_id}"
        enriched_cmd = Command(name=cmd.name, target=cmd.target, args=enriched_args)
        response = await self._execute_command(enriched_cmd, actor=actor)

        if self.audit_log is not None:
            await self.audit_log.record(
                actor=actor,
                action=f"command.{cmd.name}",
                target=cmd.target,
                details={
                    "args": (cmd.args or "")[:200],
                    "attachment_id": attachment_id,
                },
            )

        if response:
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
