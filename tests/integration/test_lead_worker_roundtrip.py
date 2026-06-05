"""Lead-worker round-trip: a worker emits a hive_actions reply to its lead.

Hermetic — no real ``claude``. The worker's turn is served by a
``FakeAdapter`` (``tests.fakes``) injected via ``using_adapter``, so the
test drives the full dispatch path deterministically:

    1. Build the hierarchy through the facade: a maestro, a team (its
       lead), and one worker nested under the lead per the dotted naming
       convention (so ``can_message`` allows worker -> lead).
    2. ``send_to_entity`` runs one turn; the fake returns a response
       carrying a ``<hive_actions>`` block addressed to the lead.
    3. The dispatcher parses the block and routes the message to the
       lead's queue, recording a ``peer_message_sent`` audit entry.
    4. We assert the action routed, the lead's queue received it, and
       the audit log captured the worker -> lead send.

Distinct from the ``test_process_manager`` action-routing unit tests in
two ways: it exercises a worker messaging *its own lead* via the dotted
naming convention, and it asserts the ``peer_message_sent`` audit entry.

Not marked ``@pytest.mark.integration`` — post-Ticket-007 there is no
real ``claude -p`` subprocess to gate on, so CI runs it like any other
test (``-m "not integration"`` no longer excludes it).
"""

from __future__ import annotations

from hive.bus.audit_log import AuditLog
from hive.bus.router import MessageRouter
from hive.process.manager import ProcessManager
from tests.fakes import FakeAdapter, using_adapter


async def test_worker_replies_to_lead_via_hive_actions(
    router: MessageRouter,
    audit_log: AuditLog,
) -> None:
    """Worker emits a hive_actions message; lead's queue receives it."""
    mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=4)
    try:
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")  # lead: dev.backend
        await mgr.spawn_worker("dev.backend", worker_name="w1")  # dev.backend.w1

        # FakeAdapter serves this canned turn — no model "decides" to
        # message the lead, so the round-trip is deterministic.
        reply = (
            "Acknowledged.\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "task complete"}]\n'
            "</hive_actions>"
        )
        with using_adapter(mgr, FakeAdapter(reply)):
            await mgr.send_to_entity(
                "dev.backend.w1",
                "Ping your lead via hive_actions with the text 'task complete'.",
            )

        assert "dev.backend" in mgr._last_routed_actions, (
            f"Worker did not emit a hive_actions message addressed to its lead. "
            f"Routed actions: {mgr._last_routed_actions}"
        )

        delivered = await router.get_next("dev.backend", timeout=1.0)
        assert delivered is not None, "Lead's queue did not receive the worker's message."
        assert delivered.sender == "dev.backend.w1"
        assert "complete" in delivered.content.lower()

        events = await audit_log.recent(action_prefix="peer_message_")
        assert any(
            e["action"] == "peer_message_sent"
            and e["actor"] == "dev.backend.w1"
            and e["target"] == "dev.backend"
            for e in events
        ), "Expected peer_message_sent audit entry for worker -> lead."
    finally:
        await mgr.kill_all()
