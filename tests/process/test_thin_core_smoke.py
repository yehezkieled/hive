"""Thin-core structural smoke tests (Ticket 004 slice 5).

After slices 1-4 lifted the four responsibility clusters out of
``ProcessManager``, ``manager.py`` is meant to be *only* a facade plus a
shared-state holder. These tests lock in that contract so a future edit
can't quietly re-fatten the core or break the public import surface:

- The four collaborators are wired in ``__init__`` and hold the back-ref.
- Every symbol external code / existing tests import from
  ``hive.process.manager`` still resolves there (the re-export contract
  that lets ``patch("hive.process.manager.X")`` reach the moved code).
- The retained core helpers are real bound methods on the facade, not
  delegations.
- The externally-referenced *private* methods (wired by ``__main__`` /
  the adapter, asserted by tests) are still bound attributes on the
  facade and are real bound methods, so a ``pm._foo = AsyncMock()``
  monkeypatch keeps working (the facade rule from design.md).

These are hermetic and DB-free: ``ProcessManager.__init__`` does no I/O,
so the manager is built over a ``MessageRouter`` around a dummy store.
The end-to-end *live* maestro-turn smoke (Telegram + web, from the
Tailscale IP, with a real browser check) is a post-merge human step --
it cannot run in CI. See the PR description and issue #45 / #40.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from hive.bus.router import MessageRouter
from hive.process.approval_handler import ApprovalHandler
from hive.process.lifecycle_manager import LifecycleManager
from hive.process.manager import ProcessManager
from hive.process.message_dispatcher import MessageDispatcher
from hive.process.wake_scheduler import WakeScheduler


@pytest.fixture
def manager() -> ProcessManager:
    """A hermetic ProcessManager — no DB, no subprocesses.

    ``__init__`` only stores its args and wires collaborators, so a
    ``MessageRouter`` over a dummy store is enough; nothing here touches
    Postgres or spawns anything.
    """
    return ProcessManager(router=MessageRouter(SimpleNamespace()))


# ---------------------------------------------------------------------------
# Collaborator wiring
# ---------------------------------------------------------------------------


def test_four_collaborators_wired(manager: ProcessManager) -> None:
    """All four collaborators are instantiated with the right types."""
    assert isinstance(manager.lifecycle, LifecycleManager)
    assert isinstance(manager.approvals, ApprovalHandler)
    assert isinstance(manager.dispatcher, MessageDispatcher)
    assert isinstance(manager.wake, WakeScheduler)


def test_collaborators_hold_back_ref(manager: ProcessManager) -> None:
    """Each collaborator's ``_mgr`` points back at the owning facade.

    This is the composition contract: collaborators reach all shared
    state through ``self._mgr``, so the back-ref must be the same object.
    """
    for collaborator in (
        manager.lifecycle,
        manager.approvals,
        manager.dispatcher,
        manager.wake,
    ):
        assert collaborator._mgr is manager


def test_collaborators_dont_import_manager_at_module_load() -> None:
    """Collaborator modules import nothing from manager.py at runtime.

    The dependency direction is one-way (manager imports collaborators).
    A runtime import of ``ProcessManager`` in a collaborator would create
    a cycle; the type hint must stay under ``TYPE_CHECKING``.
    """
    import hive.process.approval_handler as approval_handler
    import hive.process.lifecycle_manager as lifecycle_manager
    import hive.process.message_dispatcher as message_dispatcher
    import hive.process.wake_scheduler as wake_scheduler

    for module in (
        approval_handler,
        lifecycle_manager,
        message_dispatcher,
        wake_scheduler,
    ):
        # ProcessManager must not be bound at module scope — only the
        # TYPE_CHECKING hint references it, which never executes.
        assert not hasattr(module, "ProcessManager"), module.__name__


# ---------------------------------------------------------------------------
# Re-export / import-surface contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        # The public entrypoint (15 test files + source sites import it).
        "ProcessManager",
        # Collaborator classes re-exported from the facade module.
        "LifecycleManager",
        "MessageDispatcher",
        "WakeScheduler",
        "ApprovalHandler",
        # Module-level symbols tests import directly from the facade.
        "_render_auto_personality",
        "_WAKE_ON_INBOUND_TEXT",
        "_adapter_config_from_entity",
        "_PARSE_FAILURE_WINDOW_SECONDS",
        "_PARSE_FAILURE_MAX_PER_WINDOW",
        # Config flags / helpers tests patch as hive.process.manager.X so
        # the moved code (which resolves them through this module) sees the
        # patch. Removing any re-export silently breaks those patches.
        "ClaudeSession",
        "ADVISOR_ENABLED",
        "AUTO_COMPACT_ENABLED",
        "AUTO_COMPACT_THRESHOLD",
        "AUTO_RETRIEVE_ENABLED",
        "AUTO_RETRIEVE_FIRST_TURN_ONLY",
        "AUTO_RETRIEVE_INCLUDE_ATTACHMENTS",
        "AUTO_RETRIEVE_MAX_DISTANCE",
        "AUTO_RETRIEVE_TOP_K",
        "HIVE_USE_PTY",
        "generate_mcp_config",
        "can_message",
        "ClaudeAdapter",
    ],
)
def test_facade_module_reexports(symbol: str) -> None:
    """Every externally-referenced symbol still resolves on the module.

    ``from hive.process.manager import X`` and
    ``patch("hive.process.manager.X")`` must both keep working after the
    split. This guards the cleanup step from over-pruning re-exports.
    """
    import hive.process.manager as manager_module

    assert hasattr(manager_module, symbol), symbol


# ---------------------------------------------------------------------------
# Retained core helpers stay real implementations on the facade
# ---------------------------------------------------------------------------

# The methods design.md keeps in the core (state + cross-cutting helpers
# every collaborator calls). These must be defined on ProcessManager
# itself, not delegated out.
_RETAINED_CORE_METHODS = [
    "_persist",
    "_audit",
    "_notify",
    "_record_usage",
    "_peer_directory_for",
    "_parent_of",
    "get_status",
    "health_check",
    "restore",
    "rebuild_hierarchy",
]


@pytest.mark.parametrize("name", _RETAINED_CORE_METHODS)
def test_retained_core_methods_defined_on_facade(name: str) -> None:
    """Retained helpers live in manager.py, owned by ProcessManager.

    ``__qualname__`` starts with ``ProcessManager.`` because they are
    defined directly on the class, not inherited or re-bound from a
    collaborator.
    """
    method = getattr(ProcessManager, name)
    assert callable(method)
    assert method.__qualname__.startswith("ProcessManager."), method.__qualname__


def test_entities_and_active_count_are_properties() -> None:
    """The two read-only views stay properties on the facade."""
    assert isinstance(ProcessManager.entities, property)
    assert isinstance(ProcessManager.active_count, property)


# ---------------------------------------------------------------------------
# The facade rule — externally-referenced PRIVATE methods stay bound +
# monkeypatchable
# ---------------------------------------------------------------------------

# Private methods that MOVED to a collaborator but are bound on the
# manager instance by external wiring (__main__, the adapter) or asserted
# / patched by existing tests. design.md "facade rule": each needs a real
# thin delegation so a monkeypatch like ``pm._foo = AsyncMock()`` works.
_EXTERNALLY_REFERENCED_PRIVATE = [
    "_on_gate_state",  # __main__ passes into the adapter; tests patch it
    "_gate_nudge",  # __main__ on_nudge=process_manager._gate_nudge
    "_handle_actions",  # asserted by tests
    "_get_or_create_adapter",  # patched in test_advisor_mcp
    "_auto_kickoff",  # scheduled by the dispatcher as self._mgr._auto_kickoff
]


@pytest.mark.parametrize("name", _EXTERNALLY_REFERENCED_PRIVATE)
def test_externally_referenced_private_methods_are_bound(
    manager: ProcessManager, name: str
) -> None:
    """Each externally-referenced private method is a real bound method.

    A bound method (not a descriptor / property) is what makes
    ``pm._foo = AsyncMock()`` replace it cleanly, which both __main__
    wiring and the existing tests rely on.
    """
    bound = getattr(manager, name)
    assert inspect.ismethod(bound), name
    assert bound.__self__ is manager, name


def test_private_delegation_is_monkeypatchable(manager: ProcessManager) -> None:
    """A delegated private method can be replaced on the instance.

    Mirrors how __main__ / tests rebind these (e.g.
    ``pm._record_usage = AsyncMock()``). Replacing the attribute must
    take effect — i.e. it's a plain bound attribute, not a read-only
    descriptor.
    """
    sentinel = object()
    manager._on_gate_state = sentinel  # type: ignore[assignment, method-assign]
    assert manager._on_gate_state is sentinel
