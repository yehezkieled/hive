"""Per-role tool denylist (Ticket 015, ADR 0010).

The role tool guard used to be written into auto-generated personality
markdown — conditional (only when a lead was spawned with
``display_name`` + ``personality``) and lost on service restart. This
module is the authoritative, code-level replacement: a pure function
mapping a role to its deny-token list, merged into ``disallowed_tools``
on **every** spawn (``lifecycle_manager._adapter_config_from_entity``),
restart included.

Policy (ADR 0010):

- **lead** keeps the anti-subagent discipline (``Agent``/``Task`` and
  the TodoWrite family stay denied) but regains ``TaskOutput`` and
  ``TaskStop`` — the sync-wait + cancel verbs the Workflow leaf engine
  needs.
- **maestro** gets the full lead set plus ``TaskOutput``/``TaskStop``
  and ``Workflow``, so the fan-out chain stays Maestro → Lead →
  Workflow — a Maestro fanning out leaf work itself re-creates "the
  org never grows" one level up.
- Every other role (worker, vault, unknown) has no role-level denials.
"""

from __future__ import annotations

# Anti-subagent + planning verbs denied to both coordinator roles.
# TaskOutput/TaskStop are deliberately absent — leads need them to
# sync-wait on (and cancel) a running Workflow.
_LEAD_DENY: list[str] = [
    "Agent",
    "Task",
    "ExitPlanMode",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
]

# Maestros never drive a Workflow run themselves — that is the Lead's
# job — so they also lose the sync-wait verbs and Workflow itself.
_MAESTRO_DENY: list[str] = [*_LEAD_DENY, "TaskOutput", "TaskStop", "Workflow"]


def role_tool_denylist(role: str) -> list[str]:
    """Return the role-level tool deny tokens for ``role``.

    Returns a fresh list on every call — callers may mutate the result
    without corrupting the policy. Roles without a coordinator guard
    (worker, vault, anything unknown) get an empty list.
    """
    if role == "lead":
        return list(_LEAD_DENY)
    if role == "maestro":
        return list(_MAESTRO_DENY)
    return []
