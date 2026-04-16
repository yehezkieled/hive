"""Hive entry point — python -m hive."""

from __future__ import annotations

import asyncio
import logging
import signal

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.bus.store import MessageStore
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import (
    BLUEPRINTS_DIR,
    DEFAULT_MAESTRO,
    DEFAULT_MODEL,
    MAX_CONCURRENT_SESSIONS,
    PERSONALITIES_DIR,
    POSTGRES_DSN,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
    WEB_PORT,
)
from hive.knowledge.blueprints import BlueprintStore
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
    store = MessageStore(POSTGRES_DSN)
    await store.connect()

    router = MessageRouter(store)
    entity_store = EntityStore(store.pool)
    token_store = TokenStore(store.pool)
    task_store = TaskStore(store.pool)
    audit_log = AuditLog(store.pool)
    vault_store = VaultStore(store.pool)
    blueprint_store = BlueprintStore(BLUEPRINTS_DIR)

    process_manager = ProcessManager(
        router=router,
        max_sessions=MAX_CONCURRENT_SESSIONS,
        entity_store=entity_store,
        token_store=token_store,
        audit_log=audit_log,
    )

    # Restore persisted entities (organizational structure, not running procs)
    for persisted in await entity_store.all():
        process_manager.restore(persisted)
        logger.info("Restored persisted entity: %s", persisted.name)

    # Rebuild team hierarchy from restored entities
    process_manager.rebuild_hierarchy()

    # Ensure default maestro exists — register fresh on first run, skip if
    # already restored from a previous session.
    if DEFAULT_MAESTRO not in process_manager.entities:
        personality_path = PERSONALITIES_DIR / f"maestro-{DEFAULT_MAESTRO}.md"
        await process_manager.register_maestro(
            DEFAULT_MAESTRO,
            model=DEFAULT_MODEL,
            personality_path=personality_path if personality_path.exists() else None,
        )
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
            token_store=token_store,
            task_store=task_store,
            audit_log=audit_log,
            vault_store=vault_store,
        )
        bridge.blueprint_store = blueprint_store
        await bridge.start()
        logger.info("Running with Telegram bridge")

        # Start web dashboard if configured
        if WEB_PORT > 0:
            import uvicorn

            from hive.web.app import create_app

            web_app = create_app(
                process_manager=process_manager,
                token_store=token_store,
                task_store=task_store,
                audit_log=audit_log,
            )
            config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="info")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            logger.info("Web dashboard started on port %d", WEB_PORT)

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
