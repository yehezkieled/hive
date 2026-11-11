"""The standalone Telegram bot: a second way in when Tailscale is down.

``python -m hive.telegram`` (run by ``scripts/hive-telegram.sh`` under
``hive-telegram.service``). It polls Telegram outbound only and opens no port. It needs
no database and no Hive runtime: only the gateway's settings (``HIVE_GATEWAY_*``), so it
stays up when everything else on the PC is down.

Rules:

- Only private chats with an id on ``TELEGRAM_ALLOWED_USER_IDS`` get a reply. Anyone
  else gets nothing; the bot logs their user id, never their text.
- Only the fixed commands in ``COMMANDS`` exist. No command takes free text: the one
  argument any command reads is ``/watchdog on|off|status``. Plain text, files and
  unknown commands get the command list back.
- The bot token never reaches a log line or a reply (``RedactingFormatter``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Message, Update
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from hive.gateway.settings import DEFAULT_PORT, GatewaySettings
from hive.gateway.wake import WakeService, ago, last_activity
from hive.gateway.watchdog import WatchdogStore
from hive.telegram.recovery import RecoveryControls

logger = logging.getLogger("hive.telegram")

EX_CONFIG = 78  # sysexits: the config is wrong; the unit does not restart on it
TOKEN_RE = re.compile(r"^[0-9]{3,}:[A-Za-z0-9_-]{30,}$")
USER_ID_RE = re.compile(r"^[0-9]{1,20}$")
COMMAND_RE = re.compile(r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(\S.*))?$", re.DOTALL)
DEFAULT_STATE_DIR = "~/.local/state/hive-telegram"
REJECTED_MARK = "rejected-token.sha256"  # the wrapper skips a token Telegram refused
WINDOWS_TAILSCALE = Path("/mnt/c/Program Files/Tailscale/tailscale.exe")
PROBE_TIMEOUT_S = 3.0
TAILSCALE_TIMEOUT_S = 8.0
UNLISTED_LOG_EVERY_S = 3600.0  # one log line per unlisted sender per hour
RETRY_FIRST_S = 5.0
RETRY_MAX_S = 300.0

COMMANDS: dict[str, str] = {
    "status": "firstmate's state in one line",
    "health": "the whole chain: desk, firstmate, last activity, automatic recovery, Tailscale",
    "wake": "firstmate's state, with a Wake or Restart button when one applies",
    "restart": "restart a stuck firstmate session (asks you to confirm)",
    "watchdog": "automatic recovery: /watchdog on, /watchdog off or /watchdog status",
    "help": "this list",
}
HELP_TEXT = "Hive remote. These are the only commands:\n" + "\n".join(
    f"/{name} - {what}" for name, what in COMMANDS.items()
)


class BotConfigError(ValueError):
    """The bot's config is missing or malformed. The message never holds a value."""


@dataclass(frozen=True)
class RemoteConfig:
    token: str
    allowed_user_ids: tuple[int, ...]

    def __repr__(self) -> str:  # never print the token, even by accident
        return f"RemoteConfig(token=<hidden>, allowed_user_ids={self.allowed_user_ids})"


def load_config(env: Mapping[str, str]) -> RemoteConfig:
    """Read and check ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_ALLOWED_USER_IDS``."""
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    ids = env.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not token or not ids:
        raise BotConfigError("TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS must both be set")
    if not TOKEN_RE.match(token):
        raise BotConfigError("TELEGRAM_BOT_TOKEN does not look like a BotFather token")
    parts = [p.strip() for p in ids.split(",")]
    if not all(USER_ID_RE.match(p) for p in parts):
        raise BotConfigError("TELEGRAM_ALLOWED_USER_IDS must be numeric user ids, comma separated")
    allowed = tuple(dict.fromkeys(int(p) for p in parts))
    if not all(allowed):
        raise BotConfigError("TELEGRAM_ALLOWED_USER_IDS must not contain 0")
    return RemoteConfig(token, allowed)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RedactingFormatter(logging.Formatter):
    """Replaces each secret in a formatted record, tracebacks included."""

    def __init__(self, fmt: str, secrets: tuple[str, ...]) -> None:
        super().__init__(fmt, datefmt="%H:%M:%S")
        self._secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "[bot token]")
        return text


def setup_logging(token: str = "") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", (token,))
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # httpx logs every request URL at INFO, and Telegram's URLs carry the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---- the chain /health reports ---------------------------------------------------------

Probe = Callable[[], Awaitable[str]]


async def desk_probe(port: int, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """True when the desk answers HTTP on loopback (any status: it is up)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout_s
        )
    except (OSError, TimeoutError):
        return False
    try:
        writer.write(b"HEAD / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout_s)
        return line.startswith(b"HTTP/")
    except (OSError, TimeoutError):
        return False
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


def tailscale_binary(env: Mapping[str, str]) -> Path | None:
    """``HIVE_TAILSCALE_BIN``, else ``tailscale`` on PATH, else the Windows one under WSL."""
    value = env.get("HIVE_TAILSCALE_BIN", "").strip()
    if value:
        return Path(value).expanduser()
    found = shutil.which("tailscale")
    if found:
        return Path(found)
    return WINDOWS_TAILSCALE if WINDOWS_TAILSCALE.is_file() else None


async def tailscale_state(binary: Path | None, timeout_s: float = TAILSCALE_TIMEOUT_S) -> str:
    """One plain phrase for the PC's Tailscale: read only, never changes anything."""
    if binary is None:
        return "unknown (Tailscale's command was not found)"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "status",
            "--json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return "unknown (could not run Tailscale)"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return "unknown (Tailscale did not answer)"
    try:
        data = json.loads(out)
        backend = str(data.get("BackendState", ""))
        online = bool((data.get("Self") or {}).get("Online"))
    except (ValueError, AttributeError, TypeError):
        return "unknown (Tailscale's answer was unreadable)"
    if backend == "Running" and online:
        return "connected"
    if backend == "Running":
        return "running but offline (no route to Tailscale's servers)"
    if backend == "NeedsLogin":
        return "not connected: it needs a sign-in"
    return f"not connected (state: {backend.lower() or 'unknown'})"


def _clock(ts: float, tz: str) -> str:
    try:
        zone = ZoneInfo(tz)
    except Exception:  # an unknown zone name falls back to the host's
        zone = None
    return datetime.fromtimestamp(ts, zone).strftime("%H:%M")


# ---- the bot ---------------------------------------------------------------------------


class RemoteBot(RecoveryControls):
    """The fixed-command bot. One instance per process."""

    def __init__(
        self,
        config: RemoteConfig,
        wake: WakeService | None = None,
        watchdog_store: WatchdogStore | None = None,
        desk_port: int = DEFAULT_PORT,
        tailscale: Path | None = None,
        state_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.allowed_user_ids = list(config.allowed_user_ids)
        self.wake = wake or WakeService(GatewaySettings.from_env())
        self.watchdog_store = watchdog_store or WatchdogStore(self.wake.settings.data_dir)
        self.desk_port = desk_port
        self.tailscale = tailscale
        self.state_dir = state_dir or Path(DEFAULT_STATE_DIR).expanduser()
        self._clock = clock
        self._app: Application | None = None
        self._relay_task: asyncio.Task[None] | None = None
        self._restart_confirms: dict[str, tuple[int, int, float]] = {}
        self._watchdog_confirms: dict[str, tuple[int, int, float]] = {}
        self._unlisted_logged: dict[int, float] = {}

    # -- lifecycle --

    async def start(self) -> None:
        """Connect and start polling. Raises InvalidToken or NetworkError for the caller."""
        app = Application.builder().token(self.config.token).build()
        app.add_handler(MessageHandler(filters.UpdateType.MESSAGE, self._handle_update))
        app.add_handler(CallbackQueryHandler(self._handle_callback, pattern="^wake:"))
        app.add_error_handler(self._on_error)
        try:
            await app.initialize()  # getMe: proves the token and the network
            await app.start()
            await app.updater.start_polling(  # type: ignore[union-attr]
                drop_pending_updates=True, allowed_updates=["message", "callback_query"]
            )
        except BaseException:
            with contextlib.suppress(Exception):  # leave nothing half-started for the retry
                if app.running:
                    await app.stop()
                await app.shutdown()
            raise
        self._app = app
        self.clear_rejected()
        self._relay_task = asyncio.get_running_loop().create_task(self._relay_watchdog())
        logger.info(
            "Telegram bot polling (outbound only); %d allowed id(s)", len(self.allowed_user_ids)
        )

    async def stop(self) -> None:
        if self._relay_task is not None:
            self._relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._relay_task
            self._relay_task = None
        if self._app is not None:
            await self._app.updater.stop()  # type: ignore[union-attr]
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    def mark_rejected(self) -> None:
        """Remember (as a digest) that Telegram refused this token, so the wrapper skips it."""
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / REJECTED_MARK).write_text(token_digest(self.config.token) + "\n")

    def clear_rejected(self) -> None:
        with contextlib.suppress(OSError):
            (self.state_dir / REJECTED_MARK).unlink()

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        logger.warning("Telegram error: %s: %s", type(err).__name__, err)

    # -- messages --

    def _allowed(self, user_id: int, chat_type: str | None) -> bool:
        return user_id in self.allowed_user_ids and chat_type == "private"

    def _log_unlisted(self, user_id: int) -> None:
        now = self._clock()
        if now - self._unlisted_logged.get(user_id, -UNLISTED_LOG_EVERY_S) < UNLISTED_LOG_EVERY_S:
            return
        self._unlisted_logged[user_id] = now
        logger.warning("ignored a message from user id %d: not on the allowlist", user_id)

    async def _handle_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        chat = update.effective_chat
        if not self._allowed(user_id, chat.type if chat else None):
            self._log_unlisted(user_id)
            return
        await self.handle_text(message, message.text or "", user_id, update)

    async def handle_text(self, message: Message, text: str, user_id: int, update: Update) -> None:
        """Answer one message from an allowed sender. Text is matched, never run."""
        match = COMMAND_RE.match(text.strip())
        name = match.group(1).lower() if match else ""
        arg = (match.group(2) or "").strip() if match else ""
        if name not in COMMANDS:
            logger.info("user %d sent something that is not a command", user_id)
            await message.reply_text(HELP_TEXT)
            return
        if name != "watchdog" and arg:
            await message.reply_text("That command takes no extra text")
            return
        logger.info("user %d ran /%s", user_id, name)
        if name == "help":
            await message.reply_text(HELP_TEXT)
        elif name == "status":
            status = await self.wake.status.fresh()
            await message.reply_text(f"Firstmate {status.describe()}")
        elif name == "health":
            await message.reply_text(await self.health_text())
        elif name == "wake":
            await self._send_wake_status(update)
        elif name == "restart":
            await self._ask_restart_confirm(message)
        else:
            await self._watchdog_command(message, arg, user_id)

    # -- /health --

    async def health_text(self) -> str:
        status, desk, tailscale = await asyncio.gather(
            self.wake.status.fresh(),
            desk_probe(self.desk_port),
            tailscale_state(self.tailscale),
        )
        if status.state == "unknown":
            session = "unknown (could not read it)"
        elif status.session:
            beat = (
                "no watcher beat yet"
                if status.beat_age_s is None
                else f"watcher beat {ago(status.beat_age_s)} ago"
            )
            session = f"up ({beat})" if status.state == "alive" else f"up, watcher silent ({beat})"
        else:
            session = "down"
        pid = status.pids[0] if status.pids else 0
        settings = self.wake.settings
        age = await asyncio.to_thread(last_activity, settings.fm_home, pid)
        if age is None:
            activity = "no record"
        else:
            activity = f"{ago(age)} ago (at {_clock(self._clock() - age, settings.default_tz)})"
        view = await asyncio.to_thread(self.watchdog_store.view)
        extra = view.config_error or view.halted_reason
        recovery = view.label + (f" ({extra})" if extra else "")
        return "\n".join(
            (
                "Hive health",
                f"Desk: {'up' if desk else 'down'}",
                f"Firstmate session: {session}",
                f"Last firstmate activity: {activity}",
                f"Automatic recovery: {recovery}",
                f"Tailscale on the PC: {tailscale}",
            )
        )


# ---- entry point -----------------------------------------------------------------------


async def run(bot: RemoteBot, stop: asyncio.Event) -> int:
    """Start (retrying while Telegram is unreachable), poll until ``stop``, then stop."""
    delay = RETRY_FIRST_S
    while not stop.is_set():
        try:
            await bot.start()
            break
        except InvalidToken:
            logger.error(
                "Telegram rejected the bot token. Set a new one with scripts/telegram-setup.sh set"
            )
            try:
                bot.mark_rejected()
            except OSError:
                logger.error("Could not persist the rejected-token marker; not retrying this token")
                # launchd restarts every unsuccessful exit; systemd is configured
                # to suppress restart on EX_CONFIG.
                if sys.platform == "darwin":
                    return 0
            return EX_CONFIG
        except (NetworkError, TelegramError, OSError) as exc:
            logger.warning(
                "cannot reach Telegram yet (%s); retrying in %ds", type(exc).__name__, delay
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), delay)
        delay = min(delay * 2, RETRY_MAX_S)
    else:
        return 0
    await stop.wait()
    await bot.stop()
    logger.info("Telegram bot stopped")
    return 0


def main() -> int:
    setup_logging()
    try:
        config = load_config(os.environ)
    except BotConfigError as exc:
        logger.error("not starting: %s (see docs/telegram.md)", exc)
        return EX_CONFIG
    setup_logging(config.token)
    port = int(os.environ.get("HIVE_GATEWAY_PORT", DEFAULT_PORT))
    state_dir = Path(os.environ.get("HIVE_TELEGRAM_STATE_DIR", DEFAULT_STATE_DIR)).expanduser()
    bot = RemoteBot(
        config, desk_port=port, tailscale=tailscale_binary(os.environ), state_dir=state_dir
    )

    async def _main() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        return await run(bot, stop)

    return asyncio.run(_main())
