"""Per-role Claude Code skill denylist (Ticket 012, ADR 0008).

Hive Entities inherit ``~/.claude/skills`` wholesale. Some skills *pause
mid-Turn for a human* ("thinking" skills), a deadlock for an Entity with no
human at its session. This module owns the denylist of ``Skill()`` tokens that
gets merged into ``disallowed_tools`` at spawn time
(``lifecycle_manager._adapter_config_from_entity``), reusing the existing
``--disallowedTools`` path.
"""

from __future__ import annotations

# Token form (Ticket 014): a skill installed under ``~/.claude/skills`` is
# denied by its bare name (``Skill(name)``); a skill from a PLUGIN needs the
# namespaced form (``Skill(plugin:name)``). The bare form is a silent no-op for
# plugin skills — verified on the pinned 2.1.177 binary: ``Skill(brainstorming)``
# RAN, ``Skill(superpowers:brainstorming)`` BLOCKED.

# Needs hands-on driving of a built artifact — un-bridgeable for any role.
# Dormant guard (Ticket 014): ``prototype`` was trimmed from ``~/.claude/skills``
# and is not installed; denying a missing skill is a harmless no-op, so the token
# is retained to auto-re-guard a bare-user-skill reinstall for every role.
_ALL_ROLES_DENY: list[str] = ["Skill(prototype)"]

# Skills that pause for a human ("thinking" skills). No Entity has a human at
# its own session, so the Turn would deadlock.
# Exact ``Skill()`` deny tokens, one per skill.
_THINKING_DENY: list[str] = [
    # Live thinking skills under ~/.claude/skills — bare user-skill name.
    "Skill(grill-with-docs)",
    "Skill(improve-codebase-architecture)",
    "Skill(capture)",
    "Skill(curate)",
    "Skill(cc-freeze)",
    "Skill(plan-next-sprint)",
    "Skill(run-ticket)",
    "Skill(initiate-project)",
    # Live thinking skill from the superpowers PLUGIN — needs the namespaced
    # token; the bare ``Skill(brainstorming)`` does not deny it.
    "Skill(superpowers:brainstorming)",
    # Dormant guards (Ticket 014): trimmed from ~/.claude/skills, not installed.
    # No-op denials kept so a bare-user-skill reinstall is auto-re-guarded for
    # non-maestro roles. (If one returns from a plugin, switch to Skill(plugin:name).)
    "Skill(grill-me)",
    "Skill(triage)",
]


def skill_denylist_for(role: str) -> list[str]:
    """Return the ``Skill()`` deny tokens for a role.

    The only Entity left after the cut-over (ADR 0033) is the Vault, which has
    no human at its own session, so every role gets the human-pausing skills
    denied as well as ``_ALL_ROLES_DENY``.
    """
    return [*_ALL_ROLES_DENY, *_THINKING_DENY]
