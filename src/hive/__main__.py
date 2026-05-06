"""Hive entry point — python -m hive."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime

from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.router import MessageRouter
from hive.bus.store import MessageStore
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import (
    AUTO_KILL_IDLE_ENABLED,
    AUTONOMOUS_SPAWN_LIMIT,
    DAILY_SUMMARY_ENABLED,
    DAILY_SUMMARY_HOUR,
    DEFAULT_MAESTRO,
    DEFAULT_MODEL,
    EMAIL_DIGEST_BUFFER_SIZE,
    EMAIL_DIGEST_INTERVAL_MINUTES,
    EMAIL_ENABLED,
    EMAIL_TO,
    HEARTBEAT_ENABLED,
    HEARTBEAT_INTERVAL_MINUTES,
    IDLE_TIMEOUT_MINUTES,
    MAX_CONCURRENT_SESSIONS,
    PERSONALITIES_DIR,
    POSTGRES_DSN,
    PRIORITY_EVAL_INTERVAL_MINUTES,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SUMMARY_CHAT_ID,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
    VAULT_CAP_CURRENCIES,
    VAULT_DAILY_CAP_CENTS,
    VAULT_ENABLED,
    VAULT_MONTHLY_CAP_CENTS,
    VAULT_PROVIDER,
    WEB_HOST,
    WEB_PORT,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.models.maestro import Maestro
from hive.models.vault import Vault
from hive.notifications import EmailDigest, NotificationDispatcher
from hive.observability.health_monitor import HealthMonitor
from hive.process.manager import ProcessManager
from hive.process.scheduler import PriorityScheduler
from hive.vault.provider import build_provider

logger = logging.getLogger("hive")


async def idle_checker(
    process_manager: ProcessManager,
    default_maestro: str,
    stop_event: asyncio.Event,
) -> None:
    """Background task: kill idle workers/teams. Maestros are never auto-killed."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=300)
            break  # stop_event was set
        except TimeoutError:
            pass  # 5 minutes elapsed, do the check
        try:
            maestro_names = {
                name
                for name, entity in process_manager.entities.items()
                if isinstance(entity, Maestro)
            }
            killed = await process_manager.kill_idle_entities(
                IDLE_TIMEOUT_MINUTES,
                exempt_names=maestro_names,
            )
            if killed:
                logger.info("Auto-killed idle entities: %s", killed)
        except Exception:
            logger.exception("Error in idle checker")


async def daily_summary_scheduler(
    bridge: object,  # TelegramBridge, but avoid circular import at module level
    summary_hour: int,
    stop_event: asyncio.Event,
) -> None:
    """Background task: send daily summary at the configured UTC hour."""
    last_sent_date = None
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
            break  # stop_event was set
        except TimeoutError:
            pass  # 1 hour elapsed, check if it's summary time
        now = datetime.now(UTC)
        if now.hour == summary_hour and now.date() != last_sent_date:
            try:
                summary = await bridge.format_daily_summary()  # type: ignore[attr-defined]
                await bridge._send_notification(summary)  # type: ignore[attr-defined]
                last_sent_date = now.date()
                logger.info("Daily summary sent")
            except Exception:
                logger.exception("Error sending daily summary")


async def heartbeat_scheduler(
    bridge: object,  # TelegramBridge, but avoid circular import at module level
    stop_event: asyncio.Event,
) -> None:
    """Background task: send periodic heartbeat notifications."""
    while not stop_event.is_set():
        interval = getattr(bridge, "heartbeat_interval_minutes", 30)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=min(interval * 60, 3600))
            break  # stop_event was set
        except TimeoutError:
            pass  # interval elapsed
        if not getattr(bridge, "heartbeat_enabled", False):
            continue
        try:
            message = bridge.format_heartbeat()  # type: ignore[attr-defined]
            await bridge._send_notification(message)  # type: ignore[attr-defined]
            bridge._last_heartbeat_at = datetime.now(UTC)  # type: ignore[attr-defined]
            logger.info("Heartbeat sent")
        except Exception:
            logger.exception("Error sending heartbeat")


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
    blueprint_store = BlueprintStore(store.pool)
    mode_request_store = ModeRequestStore(store.pool)
    attachment_store = AttachmentStore(store.pool)

    notification_dispatcher = NotificationDispatcher()

    if EMAIL_ENABLED:
        if not EMAIL_TO:
            logger.warning(
                "HIVE_EMAIL_ENABLED set but HIVE_EMAIL_TO is empty; skipping email digest"
            )
        else:
            digest = EmailDigest(
                recipient=EMAIL_TO,
                smtp_host=SMTP_HOST,
                smtp_port=SMTP_PORT,
                smtp_user=SMTP_USER,
                smtp_password=SMTP_PASSWORD,
                buffer_size=EMAIL_DIGEST_BUFFER_SIZE,
                interval_minutes=EMAIL_DIGEST_INTERVAL_MINUTES,
            )
            notification_dispatcher.register(digest)
            mode = "console" if digest.console_mode else "smtp"
            logger.info("Email digest channel registered (mode=%s, to=%s)", mode, EMAIL_TO)

    payment_provider = build_provider(VAULT_PROVIDER) if VAULT_ENABLED else None
    process_manager = ProcessManager(
        router=router,
        max_sessions=MAX_CONCURRENT_SESSIONS,
        entity_store=entity_store,
        token_store=token_store,
        audit_log=audit_log,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
        mode_request_store=mode_request_store,
        task_store=task_store,
        vault_store=vault_store,
        payment_provider=payment_provider,
        vault_daily_cap_cents=VAULT_DAILY_CAP_CENTS,
        vault_monthly_cap_cents=VAULT_MONTHLY_CAP_CENTS,
        vault_cap_currencies=VAULT_CAP_CURRENCIES,
        notification_dispatcher=notification_dispatcher,
    )

    # Priority scheduler (Sprint 19). The scheduler pokes each alive
    # maestro every PRIORITY_EVAL_INTERVAL_MINUTES with a facts prompt;
    # the maestro decides allocation via spawn/kill actions. ProcessManager
    # consults the scheduler's per-maestro rate limit when dispatching
    # autonomous spawn actions.
    scheduler = PriorityScheduler(
        process_manager=process_manager,
        task_store=task_store,
        token_store=token_store,
        eval_interval_minutes=PRIORITY_EVAL_INTERVAL_MINUTES,
        spawn_limit=AUTONOMOUS_SPAWN_LIMIT,
    )
    process_manager.scheduler = scheduler

    # Restore persisted entities (organizational structure, not running procs)
    for persisted in await entity_store.all():
        process_manager.restore(persisted)
        logger.info("Restored persisted entity: %s", persisted.name)

    # Rebuild team hierarchy from restored entities
    process_manager.rebuild_hierarchy()

    # Ensure default maestro exists — register fresh on first run, skip if
    # already restored from a previous session.
    if DEFAULT_MAESTRO not in process_manager.entities:
        personality_path = PERSONALITIES_DIR / f"{DEFAULT_MAESTRO}.md"
        await process_manager.register_maestro(
            DEFAULT_MAESTRO,
            model=DEFAULT_MODEL,
            personality_path=personality_path if personality_path.exists() else None,
        )
        logger.info("Registered default maestro: %s", DEFAULT_MAESTRO)

    # Ensure default vault exists when the Vault subsystem is enabled.
    # Opt-in until a real provider ships; the role-vault personality
    # provides the locked-down JD.
    if VAULT_ENABLED and "vault" not in process_manager.entities:
        vault_personality = PERSONALITIES_DIR / "role-vault.md"
        vault = Vault(
            name="vault",
            model=DEFAULT_MODEL,
            personality_path=vault_personality if vault_personality.exists() else None,
        )
        if vault.personality_path and vault.personality_path.exists():
            vault.load_personality()
        await process_manager.register_entity(vault)
        await process_manager._persist(vault)
        logger.info("Registered default vault entity (provider=%s)", VAULT_PROVIDER)

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
            mode_request_store=mode_request_store,
            attachment_store=attachment_store,
            scheduler=scheduler,
        )
        bridge.blueprint_store = blueprint_store
        await bridge.start()
        notification_dispatcher.register(bridge)
        logger.info(
            "Running with Telegram bridge (notification channels: %d)",
            notification_dispatcher.channel_count,
        )

        # Start web dashboard if configured
        if WEB_PORT > 0:
            import uvicorn

            from hive.commands.dispatch import CommandDispatcher
            from hive.web.app import create_app
            from hive.web.sse import SSEBroker

            web_dispatcher = CommandDispatcher(
                process_manager=process_manager,
                default_maestro=DEFAULT_MAESTRO,
                token_store=token_store,
                task_store=task_store,
                audit_log=audit_log,
                vault_store=vault_store,
                mode_request_store=mode_request_store,
                blueprint_store=blueprint_store,
                attachment_store=attachment_store,
                scheduler=scheduler,
            )

            sse_broker = SSEBroker()
            notification_dispatcher.register(sse_broker)
            logger.info(
                "SSE broker registered (notification channels: %d)",
                notification_dispatcher.channel_count,
            )

            health_monitor = HealthMonitor(
                pool=store.pool,
                bridge=bridge,
                process_manager=process_manager,
            )

            web_app = create_app(
                process_manager=process_manager,
                token_store=token_store,
                task_store=task_store,
                audit_log=audit_log,
                vault_store=vault_store,
                mode_request_store=mode_request_store,
                default_maestro=DEFAULT_MAESTRO,
                personalities_dir=PERSONALITIES_DIR,
                command_dispatcher=web_dispatcher,
                message_store=store,
                sse_broker=sse_broker,
                attachment_store=attachment_store,
                health_monitor=health_monitor,
            )
            config = uvicorn.Config(web_app, host=WEB_HOST, port=WEB_PORT, log_level="info")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            logger.info("Web dashboard started on %s:%d", WEB_HOST, WEB_PORT)

        # Keep running until interrupted
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Start background auto-management tasks
        background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
        if AUTO_KILL_IDLE_ENABLED:
            background_tasks.append(
                asyncio.create_task(idle_checker(process_manager, DEFAULT_MAESTRO, stop_event))
            )
            logger.info("Idle checker started (timeout=%dm)", IDLE_TIMEOUT_MINUTES)
        if DAILY_SUMMARY_ENABLED and SUMMARY_CHAT_ID:
            background_tasks.append(
                asyncio.create_task(daily_summary_scheduler(bridge, DAILY_SUMMARY_HOUR, stop_event))
            )
            logger.info("Daily summary scheduled at %02d:00 UTC", DAILY_SUMMARY_HOUR)
        if HEARTBEAT_ENABLED and SUMMARY_CHAT_ID:
            background_tasks.append(asyncio.create_task(heartbeat_scheduler(bridge, stop_event)))
            logger.info("Heartbeat scheduler started (interval=%dm)", HEARTBEAT_INTERVAL_MINUTES)
        background_tasks.append(asyncio.create_task(scheduler.run(stop_event)))
        logger.info(
            "Priority scheduler started (interval=%dm, spawn_limit=%d)",
            PRIORITY_EVAL_INTERVAL_MINUTES,
            AUTONOMOUS_SPAWN_LIMIT,
        )
        if WEB_PORT > 0:
            background_tasks.append(asyncio.create_task(health_monitor.run(stop_event)))
            logger.info("Health monitor started (tick=%ds)", health_monitor.tick_seconds)

        await stop_event.wait()

        # Cleanup
        logger.info("Shutting down...")
        for task in background_tasks:
            task.cancel()
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

    # Cleanup — graceful stop preserves DB rows so entities restore on next boot
    await process_manager.stop_all()
    await store.close()
    logger.info("Hive stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
