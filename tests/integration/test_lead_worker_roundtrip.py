"""Integration test: worker emits hive_actions reply to its lead.

Marked ``@pytest.mark.integration`` because it spawns a real ``claude -p``
subprocess. CI skips integration tests; run locally with::

    .venv/bin/python -m pytest -m integration tests/integration/

The test exercises the full Sprint 9 + Sprint 19 round-trip:
    1. Register a lead and a worker (worker name nests under lead per the
       dotted naming convention so ``can_message`` allows worker → lead).
    2. ``send_to_entity`` fires a real claude -p subprocess for the worker,
       with MESSAGING_PROMPT injected (Sprint 19 — workers now get the
       protocol so they can talk back).
    3. The worker's response is parsed for ``<hive_actions>`` blocks; the
       message routes to the lead's queue.
    4. We pull from the lead's queue and assert the worker's reply landed.
"""

from __future__ import annotations

import pytest

from hive.bus.audit_log import AuditLog
from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent
from hive.notifications import NotificationDispatcher
from hive.process.manager import ProcessManager


@pytest.mark.integration
async def test_worker_replies_to_lead_via_hive_actions(
    router: MessageRouter,
    entity_store: EntityStore,
    audit_log: AuditLog,
) -> None:
    """Worker emits a hive_actions message; lead's queue receives it."""
    mgr = ProcessManager(
        router=router,
        entity_store=entity_store,
        audit_log=audit_log,
        max_sessions=4,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        lead = TeamLead(
            name="dev.backend",
            team_name="backend",
            maestro_name="dev",
            model="haiku",
        )
        worker = WorkerAgent(
            name="dev.backend.w1",
            team_name="backend",
            lead_name="dev.backend",
            model="haiku",
            system_prompt=(
                "You are a worker reporting to your lead at dev.backend. "
                "When asked to ping your lead, reply with a brief acknowledgement "
                "and a <hive_actions> block sending a message to dev.backend."
            ),
        )
        mgr._entities[lead.name] = lead
        mgr._entities[worker.name] = worker
        router.register(lead.name)
        router.register(worker.name)

        await mgr.send_to_entity(
            worker.name,
            "Ping your lead via hive_actions with the text 'task complete'.",
        )

        assert lead.name in mgr._last_routed_actions, (
            f"Worker did not emit a hive_actions message addressed to its lead. "
            f"Routed actions: {mgr._last_routed_actions}"
        )

        delivered = await router.get_next(lead.name, timeout=1.0)
        assert delivered is not None, "Lead's queue did not receive the worker's message."
        assert delivered.sender == worker.name
        assert "complete" in delivered.content.lower()

        events = await audit_log.recent(action_prefix="peer_message_")
        assert any(
            e["action"] == "peer_message_sent"
            and e["actor"] == worker.name
            and e["target"] == lead.name
            for e in events
        ), "Expected peer_message_sent audit entry for worker → lead."
    finally:
        await mgr.kill_all()
