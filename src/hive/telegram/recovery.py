"""Recovery controls shared by both Telegram bots: Wake, Restart and the watchdog switch.

Only ids on the allowlist get a reply or an effect; an empty allowlist serves nobody.
Restart and ``/watchdog on`` never act on the first tap: they post a Confirm / Cancel
bound to that chat and message, valid once for ``RESTART_CONFIRM_TTL_S``. The watchdog
relay sends each new watchdog event (written by the gateway) to the chat.

Used by :class:`hive.telegram.remote.RemoteBot` (the standalone fixed-command bot) and by
:class:`hive.telegram.bridge.TelegramBridge` (the Vault runtime's backup channel). Imports
nothing from Hive core, so the standalone bot runs without a database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from hive.gateway.wake import WakeService
from hive.gateway.watchdog import ON_DETAIL, ConfigError, WatchdogStore

logger = logging.getLogger(__name__)


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


class RecoveryControls:
    """Wake / Restart / watchdog handling over an allowlist. Subclasses set the attributes."""

    allowed_user_ids: list[int]
    wake: WakeService
    watchdog_store: WatchdogStore
    _app: Application | None
    _restart_confirms: dict[str, tuple[int, int, float]]
    _watchdog_confirms: dict[str, tuple[int, int, float]]

    def _relay_targets(self) -> list[int]:
        return list(self.allowed_user_ids)

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
