"""Hive entry point — python -m hive."""

from __future__ import annotations

import asyncio
import logging
import signal

from hive.bus.router import MessageRouter
from hive.bus.store import MessageStore
from hive.config import (
    DB_PATH,
    DEFAULT_MAESTRO,
    DEFAULT_MODEL,
    MAX_CONCURRENT_SESSIONS,
    PERSONALITIES_DIR,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
)
from hive.models.maestro import Maestro
from hive.process.manager import ProcessManager

logger = logging.getLogger("hive")


async def main() -> None:
    """Start the Hive orchestrator."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Starting Hive orchestrator...")

    # Initialize components
    store = MessageStore(DB_PATH)
    await store.connect()

    router = MessageRouter(store)

    process_manager = ProcessManager(
        router=router,
        max_sessions=MAX_CONCURRENT_SESSIONS,
    )

    # Register default maestro
    personality_path = PERSONALITIES_DIR / f"maestro-{DEFAULT_MAESTRO}.md"
    maestro = Maestro(
        name=DEFAULT_MAESTRO,
        model=DEFAULT_MODEL,
        personality_path=personality_path if personality_path.exists() else None,
    )
    # Don't spawn yet — spawn on first message (lazy)
    process_manager._entities[DEFAULT_MAESTRO] = maestro
    router.register(DEFAULT_MAESTRO)
    logger.info("Registered default maestro: %s", DEFAULT_MAESTRO)

    # Determine mode: Telegram or local CLI
    use_telegram = bool(TELEGRAM_BOT_TOKEN)

    if use_telegram:
        from hive.telegram.bridge import TelegramBridge

        bridge = TelegramBridge(
            bot_token=TELEGRAM_BOT_TOKEN,
            allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
            process_manager=process_manager,
            default_maestro=DEFAULT_MAESTRO,
        )
        await bridge.start()
        logger.info("Running with Telegram bridge")

        # Keep running until interrupted
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        # Cleanup
        logger.info("Shutting down...")
        await bridge.stop()
    else:
        from hive.cli.local import LocalCLI

        logger.info("No TELEGRAM_BOT_TOKEN set, running local CLI")
        cli = LocalCLI(
            process_manager=process_manager,
            router=router,
            default_maestro=DEFAULT_MAESTRO,
        )
        await cli.run()

    # Cleanup
    await process_manager.kill_all()
    await store.close()
    logger.info("Hive stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
