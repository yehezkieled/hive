"""Unit tests for the role tool-policy module (Ticket 015, ADR 0010).

``role_tool_denylist`` is the authoritative, code-level source of the
per-role tool guard that previously lived in auto-generated personality
markdown. The lead set deliberately omits ``TaskOutput``/``TaskStop``
(the Workflow sync-wait + cancel verbs); the maestro set adds them back
plus ``Workflow`` so the fan-out chain stays Maestro → Lead → Workflow.
"""

from __future__ import annotations

from hive.process.tool_policy import role_tool_denylist

LEAD_DENY = [
    "Agent",
    "Task",
    "ExitPlanMode",
    "AskUserQuestion",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
]


def test_role_tool_denylist_exact_sets_per_role() -> None:
    """Lead prunes TaskOutput/TaskStop; maestro adds them back + Workflow.

    Vault and unknown roles get no role-level denials. Each call returns
    a fresh list so callers can't corrupt the policy by mutation.
    """
    lead = role_tool_denylist("lead")
    assert lead == LEAD_DENY
    # The Workflow sync-wait verbs are deliberately absent for leads.
    assert "TaskOutput" not in lead
    assert "TaskStop" not in lead

    maestro = role_tool_denylist("maestro")
    assert maestro == [*LEAD_DENY, "TaskOutput", "TaskStop", "Workflow"]

    assert role_tool_denylist("vault") == []
    assert role_tool_denylist("definitely-not-a-role") == []


def test_native_gates_denied_to_both_coordinator_roles() -> None:
    """Ticket 029: native interactive gates are retired for coordinators.

    Neither a lead nor a maestro has a human at its own PTY to answer a TUI
    gate, so both ``ExitPlanMode`` and ``AskUserQuestion`` are denied. Maestros
    ask the user via the conversational decision channel (request_decision)
    instead (ADR 0018).
    """
    for role in ("lead", "maestro"):
        deny = role_tool_denylist(role)
        assert "ExitPlanMode" in deny
        assert "AskUserQuestion" in deny

    # Fresh list each call — mutating one result never leaks into the next.
    first = role_tool_denylist("lead")
    first.append("Edit")
    assert role_tool_denylist("lead") == LEAD_DENY
