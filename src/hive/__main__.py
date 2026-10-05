"""Hive entry point — python -m hive."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime

from hive.bootstrap import build_process_manager
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
    DAILY_SUMMARY_ENABLED,
    DAILY_SUMMARY_HOUR,
    DEFAULT_MODEL,
    EMAIL_DIGEST_BUFFER_SIZE,
    EMAIL_DIGEST_INTERVAL_MINUTES,
    EMAIL_ENABLED,
    EMAIL_TO,
    HEARTBEAT_ENABLED,
    HEARTBEAT_INTERVAL_MINUTES,
    HIVE_CLAUDE_CREDENTIALS_PATH,
    HIVE_QUOTA_POLL_SECONDS,
    IDLE_TIMEOUT_MINUTES,
    MAX_CONCURRENT_SESSIONS,
    PERSONALITIES_DIR,
    POSTGRES_DSN,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SUMMARY_CHAT_ID,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
    VAPID_PRIVATE_KEY,
    VAPID_PUBLIC_KEY,
    VAPID_SUBJECT,
    WEB_HOST,
    WEB_PORT,
)
from hive.knowledge.blueprints import BlueprintStore
from hive.models.vault import Vault
from hive.notifications import EmailDigest, NotificationDispatcher
from hive.observability.health_monitor import HealthMonitor
from hive.process.manager import ProcessManager
from hive.runtime import QuotaMonitor
from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.registry import availability_report
from hive.vault.config import VaultConfig
from hive.vault.provider import build_provider

logger = logging.getLogger("hive")


async def idle_checker(
    process_manager: ProcessManager,
    stop_event: asyncio.Event,
) -> None:
    """Background task: kill idle entities."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=300)
            break  # stop_event was set
        except TimeoutError:
            pass  # 5 minutes elapsed, do the check
        try:
            killed = await process_manager.kill_idle_entities(IDLE_TIMEOUT_MINUTES)
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
    """Start Hive: the Vault entity, the approval rail and the optional channels.

    Since the cut-over (ADR 0033) Hive runs no Maestro or Team Lead. The desk
    (``python -m hive.gateway``) is the primary surface; Telegram is an optional
    backup ping/approval channel that stays off unless ``TELEGRAM_BOT_TOKEN`` is
    set, and the legacy web app (``HIVE_WEB_PORT``) keeps the vault and
    mode-request approvals until the desk covers them.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Starting Hive...")

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

    vault_cfg = VaultConfig.from_env()
    payment_provider = build_provider(vault_cfg.provider) if vault_cfg.enabled else None
    process_manager = build_process_manager(
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
        vault_daily_cap_cents=vault_cfg.daily_cap_cents,
        vault_monthly_cap_cents=vault_cfg.monthly_cap_cents,
        vault_cap_currencies=vault_cfg.cap_currencies,
        notification_dispatcher=notification_dispatcher,
    )

    # Interactive-gate bridge (Ticket 003): construct the coordinator and wire
    # it into the manager so PtySession parks-and-injects on plan/ask gates and
    # re-pings unanswered ones.
    process_manager.gate_coordinator = GateCoordinator(
        mode_request_store,
        on_nudge=process_manager._gate_nudge,
    )

    # Wire wake-on-inbound so queued messages auto-spawn a session for the
    # recipient.
    process_manager.enable_wake_on_inbound()

    # QuotaMonitor — background poller of Anthropic plan-quota.
    # Alerts at 80/90/100 thresholds on both 5h and 7d windows; meta-alerts
    # if the endpoint goes blind. See
    # docs/adr/0002-quota-from-undocumented-oauth-endpoint.md.
    quota_monitor = QuotaMonitor(
        credentials_path=HIVE_CLAUDE_CREDENTIALS_PATH,
        notifications=notification_dispatcher,
        poll_seconds=HIVE_QUOTA_POLL_SECONDS,
    )
    process_manager.quota_monitor = quota_monitor
    await quota_monitor.start()
    logger.info(
        "QuotaMonitor started (poll every %.0fs, credentials %s)",
        HIVE_QUOTA_POLL_SECONDS,
        HIVE_CLAUDE_CREDENTIALS_PATH,
    )

    # Purge rows of retired roles before restore: a leftover row would
    # zombie-restore as a bare Entity. Idempotent.
    for retired in ("worker", "maestro", "lead"):
        purged = await entity_store.purge_role(retired)
        if purged:
            logger.info("Purged %d retired %s row(s) from the entity store", purged, retired)

    # Restore persisted entities (structure, not running procs)
    for persisted in await entity_store.all():
        process_manager.restore(persisted)
        logger.info("Restored persisted entity: %s", persisted.name)

    # Reconcile interactive-gate rows orphaned by a restart (Ticket 003 #27):
    # a pending gate whose parked Turn died is marked stale, not left dangling.
    await process_manager.reconcile_orphaned_gates()

    # Ensure default vault exists when the Vault subsystem is enabled.
    # Opt-in until a real provider ships; the role-vault personality
    # provides the locked-down JD. Off by default (HIVE_VAULT_ENABLED).
    if vault_cfg.enabled and "vault" not in process_manager.entities:
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
        logger.info("Registered default vault entity (provider=%s)", vault_cfg.provider)

    stop_event = asyncio.Event()

    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

    # Telegram is an optional backup channel (ADR 0033): off unless configured.
    bridge = None
    if TELEGRAM_BOT_TOKEN:
        from hive.telegram.bridge import TelegramBridge

        bridge = TelegramBridge(
            bot_token=TELEGRAM_BOT_TOKEN,
            allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
            process_manager=process_manager,
            token_store=token_store,
            task_store=task_store,
            audit_log=audit_log,
            vault_store=vault_store,
            mode_request_store=mode_request_store,
            attachment_store=attachment_store,
        )
        bridge.blueprint_store = blueprint_store
        await bridge.start()
        notification_dispatcher.register(bridge)
        logger.info(
            "Telegram bridge started as a backup channel (notification channels: %d)",
            notification_dispatcher.channel_count,
        )
        if DAILY_SUMMARY_ENABLED and SUMMARY_CHAT_ID:
            background_tasks.append(
                asyncio.create_task(daily_summary_scheduler(bridge, DAILY_SUMMARY_HOUR, stop_event))
            )
            logger.info("Daily summary scheduled at %02d:00 UTC", DAILY_SUMMARY_HOUR)
        if HEARTBEAT_ENABLED and SUMMARY_CHAT_ID:
            background_tasks.append(asyncio.create_task(heartbeat_scheduler(bridge, stop_event)))
            logger.info("Heartbeat scheduler started (interval=%dm)", HEARTBEAT_INTERVAL_MINUTES)
    else:
        logger.info("No TELEGRAM_BOT_TOKEN set: Telegram backup channel is off")

    health_monitor = None
    # Legacy web app (vault and mode-request approvals) — independent of Telegram.
    if WEB_PORT > 0:
        import uvicorn

        from hive.bus.push_subscription_store import PushSubscriptionStore
        from hive.commands.dispatch import CommandDispatcher
        from hive.notifications import WebPushChannel
        from hive.web.app import create_app
        from hive.web.sse import SSEBroker

        web_dispatcher = CommandDispatcher(
            process_manager=process_manager,
            token_store=token_store,
            task_store=task_store,
            audit_log=audit_log,
            vault_store=vault_store,
            mode_request_store=mode_request_store,
            blueprint_store=blueprint_store,
            attachment_store=attachment_store,
        )

        sse_broker = SSEBroker()
        notification_dispatcher.register(sse_broker)

        # Web Push channel (Ticket 041, ADR 0026). Registered unconditionally;
        # it no-ops until VAPID keys are set.
        push_subscription_store = PushSubscriptionStore(store.pool)
        web_push_channel = WebPushChannel(
            push_subscription_store,
            VAPID_PUBLIC_KEY,
            VAPID_PRIVATE_KEY,
            VAPID_SUBJECT,
        )
        notification_dispatcher.register(web_push_channel)
        logger.info(
            "Web Push channel registered (push %s)",
            "enabled" if VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY else "inert — no VAPID keys",
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
            command_dispatcher=web_dispatcher,
            message_store=store,
            sse_broker=sse_broker,
            attachment_store=attachment_store,
            health_monitor=health_monitor,
            push_subscription_store=push_subscription_store,
            vapid_public_key=VAPID_PUBLIC_KEY,
        )
        config = uvicorn.Config(web_app, host=WEB_HOST, port=WEB_PORT, log_level="info")
        server = uvicorn.Server(config)
        background_tasks.append(asyncio.create_task(server.serve()))
        logger.info("Web dashboard started on %s:%d", WEB_HOST, WEB_PORT)
        background_tasks.append(asyncio.create_task(health_monitor.run(stop_event)))
        logger.info("Health monitor started (tick=%ds)", health_monitor.tick_seconds)

    if AUTO_KILL_IDLE_ENABLED:
        background_tasks.append(asyncio.create_task(idle_checker(process_manager, stop_event)))
        logger.info("Idle checker started (timeout=%dm)", IDLE_TIMEOUT_MINUTES)

    # Which harnesses can run turns right now (ADR 0029). Logged; and when none
    # can (Claude Code logged out, Pi unconfigured) say so in the notification
    # channels at boot rather than waiting for the first failed turn.
    harness_lines, no_harness = await availability_report(process_manager.harness_detector)
    for line in harness_lines:
        logger.info("Harness: %s", line)
    if no_harness is not None:
        await process_manager._notify(str(no_harness), kind="harness_unavailable")

    await stop_event.wait()

    # Cleanup — graceful stop preserves DB rows so entities restore on next boot
    logger.info("Shutting down...")
    for task in background_tasks:
        task.cancel()
    if bridge is not None:
        await bridge.stop()
    await process_manager.stop_all()
    await store.close()
    logger.info("Hive stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
