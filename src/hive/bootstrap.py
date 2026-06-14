"""Production composition root for Hive (Ticket 023, issue #92).

`build_process_manager` is the single place where the live service's
`ProcessManager` is assembled. It exists as a separate, explicitly-
parameterised factory (rather than inline wiring in ``__main__``) so the
*production composition itself* is unit-testable: Ticket 015 shipped the
worktree floor green behind injected fakes while ``__main__`` never
constructed a `WorktreeManager`, leaving the floor dead code in
production. Tests may pass fakes for the stores (no Postgres needed),
but the factory always builds the real `WorktreeManager` pointed at the
config paths — that wiring is the thing under test.
"""

from __future__ import annotations

from collections.abc import Iterable

from hive.bus.attachment_store import AttachmentStore
from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.project_store import ProjectStore
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.bus.token_store import TokenStore
from hive.bus.vault_store import VaultStore
from hive.config import PROJECT_ROOT, WORKTREES_DIR
from hive.knowledge.blueprints import BlueprintStore
from hive.notifications import NotificationDispatcher
from hive.process.manager import ProcessManager
from hive.process.worktree import WorktreeManager
from hive.vault.provider import PaymentProvider


def build_process_manager(
    *,
    router: MessageRouter,
    max_sessions: int = 3,
    entity_store: EntityStore | None = None,
    project_store: ProjectStore | None = None,
    token_store: TokenStore | None = None,
    audit_log: AuditLog | None = None,
    blueprint_store: BlueprintStore | None = None,
    attachment_store: AttachmentStore | None = None,
    mode_request_store: ModeRequestStore | None = None,
    task_store: TaskStore | None = None,
    vault_store: VaultStore | None = None,
    payment_provider: PaymentProvider | None = None,
    vault_daily_cap_cents: int = 0,
    vault_monthly_cap_cents: int = 0,
    vault_cap_currencies: Iterable[str] = ("AUD", "USD"),
    notification_dispatcher: NotificationDispatcher | None = None,
) -> ProcessManager:
    """Build the production `ProcessManager`, worktree floor included.

    The `WorktreeManager` is constructed here — not taken as a parameter —
    because passing it in is exactly how the floor went dead in 015 (every
    caller but production injected one). Spawned Team Leads get a dedicated
    worktree under ``WORKTREES_DIR`` instead of running in the live
    checkout (ADR 0010, "Worktree floor").
    """
    worktree_mgr = WorktreeManager(PROJECT_ROOT, WORKTREES_DIR)
    return ProcessManager(
        router=router,
        worktree_mgr=worktree_mgr,
        max_sessions=max_sessions,
        entity_store=entity_store,
        project_store=project_store,
        token_store=token_store,
        audit_log=audit_log,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
        mode_request_store=mode_request_store,
        task_store=task_store,
        vault_store=vault_store,
        payment_provider=payment_provider,
        vault_daily_cap_cents=vault_daily_cap_cents,
        vault_monthly_cap_cents=vault_monthly_cap_cents,
        vault_cap_currencies=vault_cap_currencies,
        notification_dispatcher=notification_dispatcher,
    )
