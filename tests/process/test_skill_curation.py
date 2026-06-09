"""Unit tests for the per-role skill denylist (Ticket 012, ADR 0008).

``skill_denylist_for`` returns the ``Skill()`` deny tokens for a role. Every
role blocks ``_ALL_ROLES_DENY``; every non-Maestro role additionally blocks
the human-pausing ``_THINKING_DENY`` set. The Maestro keeps the thinking
skills — its interactive gates bridge to Telegram.
"""

from __future__ import annotations

from hive.process.skill_curation import (
    _ALL_ROLES_DENY,
    _THINKING_DENY,
    skill_denylist_for,
)


def test_non_maestro_roles_block_all_and_thinking_skills() -> None:
    """Lead, Worker, Vault, and future non-Maestro roles block everything."""
    for role in ("worker", "lead", "vault"):
        deny = skill_denylist_for(role)
        for token in _ALL_ROLES_DENY:
            assert token in deny, f"{role} missing {token}"
        for token in _THINKING_DENY:
            assert token in deny, f"{role} missing {token}"


def test_maestro_blocks_all_roles_set_but_no_thinking_skills() -> None:
    """The Maestro keeps the thinking skills — only Skill(prototype) is denied."""
    deny = skill_denylist_for("maestro")
    assert "Skill(prototype)" in deny
    for token in _ALL_ROLES_DENY:
        assert token in deny
    assert "Skill(grill-me)" not in deny
    assert "Skill(brainstorming)" not in deny
    for token in _THINKING_DENY:
        assert token not in deny, f"maestro must not block thinking skill {token}"


def test_no_duplicate_tokens_in_any_returned_list() -> None:
    """Each role's denylist has no repeated tokens."""
    for role in ("maestro", "lead", "worker", "vault"):
        deny = skill_denylist_for(role)
        assert len(deny) == len(set(deny)), f"duplicate tokens for {role}: {deny}"
