"""Per-role Claude Code skill denylist (Ticket 012, ADR 0008).

Hive Entities inherit ``~/.claude/skills`` wholesale. Some skills *pause
mid-Turn for a human* ("thinking" skills) — fine for the Maestro, whose
interactive gates bridge to Telegram (Ticket 003), but a deadlock for a
Lead/Worker, which has no human to answer. This module owns the role-keyed
denylist of ``Skill()`` tokens that gets merged into ``disallowed_tools`` at
spawn time (``lifecycle_manager._adapter_config_from_entity``), reusing the
existing ``--disallowedTools`` path.
"""

from __future__ import annotations

# Needs hands-on driving of a built artifact — un-bridgeable for any role,
# including the Maestro (there is no Telegram bridge for "drive this app").
_ALL_ROLES_DENY: list[str] = ["Skill(prototype)"]

# Skills that pause for a human ("thinking" skills). Safe only for the
# Maestro, whose gates reach the user on Telegram; for any other role the
# wait escalates to a parent Entity that cannot answer, so the Turn deadlocks.
# Exact ``Skill()`` deny tokens, one per skill.
_THINKING_DENY: list[str] = [
    "Skill(grill-me)",
    "Skill(brainstorming)",
    "Skill(grill-with-docs)",
    "Skill(improve-codebase-architecture)",
    "Skill(capture)",
    "Skill(curate)",
    "Skill(cc-freeze)",
    "Skill(triage)",
    "Skill(plan-next-sprint)",
    "Skill(run-ticket)",
    "Skill(initiate-project)",
]


def skill_denylist_for(role: str) -> list[str]:
    """Return the ``Skill()`` deny tokens for a role.

    Every role blocks ``_ALL_ROLES_DENY``. Every role *except* the Maestro
    also blocks ``_THINKING_DENY`` — so lead, worker, vault, and any future
    non-maestro role lose the human-pausing skills, while the Maestro keeps
    them (its gates bridge to Telegram).
    """
    deny = list(_ALL_ROLES_DENY)
    if role != "maestro":
        deny.extend(_THINKING_DENY)
    return deny
