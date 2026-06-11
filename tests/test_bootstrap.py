"""Tests for the production composition factory (Ticket 023, issue #92).

These tests exist to close a specific gap class: Ticket 015 shipped the
worktree floor fully tested *behind fakes*, while the composition root
(`__main__.py`) never constructed a WorktreeManager — so the floor was
dead code in production and no test could notice. The factory makes the
production wiring itself unit-testable: stores may be fakes (no Postgres
needed), but the WorktreeManager must be the real class pointed at the
real config paths.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import hive.__main__
from hive import config
from hive.bootstrap import build_process_manager
from hive.process.manager import ProcessManager
from hive.process.worktree import WorktreeManager


def test_factory_builds_real_worktree_manager_at_config_paths() -> None:
    """The factory wires a REAL WorktreeManager pointed at the config paths.

    No fakes for the worktree manager — this is the production wiring the
    015 tests never exercised (they injected `AsyncMock(spec=WorktreeManager)`).
    """
    manager = build_process_manager(
        router=MagicMock(),
        max_sessions=3,
    )

    assert isinstance(manager, ProcessManager)
    assert isinstance(manager.worktree_mgr, WorktreeManager)
    assert manager.worktree_mgr.repo_path == config.PROJECT_ROOT
    assert manager.worktree_mgr.worktree_dir == config.WORKTREES_DIR


def test_factory_passes_stores_and_caps_through_unchanged() -> None:
    """Every dependency `__main__` used to pass inline reaches ProcessManager.

    Characterizes the faithful extraction: same params, same values, only
    the worktree_mgr is new. Stores are fakes — the factory must not need
    Postgres to be testable.
    """
    router = MagicMock()
    entity_store = MagicMock()
    token_store = MagicMock()
    audit_log = MagicMock()
    blueprint_store = MagicMock()
    attachment_store = MagicMock()
    mode_request_store = MagicMock()
    task_store = MagicMock()
    vault_store = MagicMock()
    payment_provider = MagicMock()
    notification_dispatcher = MagicMock()

    manager = build_process_manager(
        router=router,
        max_sessions=5,
        entity_store=entity_store,
        token_store=token_store,
        audit_log=audit_log,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
        mode_request_store=mode_request_store,
        task_store=task_store,
        vault_store=vault_store,
        payment_provider=payment_provider,
        vault_daily_cap_cents=1200,
        vault_monthly_cap_cents=34000,
        vault_cap_currencies=("usd", "aud"),
        notification_dispatcher=notification_dispatcher,
    )

    assert manager.router is router
    assert manager.max_sessions == 5
    assert manager.entity_store is entity_store
    assert manager.token_store is token_store
    assert manager.audit_log is audit_log
    assert manager.blueprint_store is blueprint_store
    assert manager.attachment_store is attachment_store
    assert manager.mode_request_store is mode_request_store
    assert manager.task_store is task_store
    assert manager.vault_store is vault_store
    assert manager.payment_provider is payment_provider
    assert manager.vault_daily_cap_cents == 1200
    assert manager.vault_monthly_cap_cents == 34000
    # ProcessManager normalizes currencies (upper-case, sorted) — assert the
    # post-normalization value so this test pins behavior, not implementation.
    assert manager.vault_cap_currencies == ("AUD", "USD")
    assert manager.notification_dispatcher is notification_dispatcher


def test_main_composes_through_the_factory_only() -> None:
    """`__main__.main` goes through the factory — no inline construction.

    Source inspection is deliberate: the composition root has no other
    observable seam (running `main()` needs Postgres + Telegram). If inline
    `ProcessManager(...)` construction reappears in `main`, the factory —
    and the worktree floor it wires — can silently fall out of production
    again. That regression is exactly what this guards.
    """
    source = inspect.getsource(hive.__main__.main)

    assert "build_process_manager(" in source
    assert "ProcessManager(" not in source
