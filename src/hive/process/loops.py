"""Loop prompt fragments injected into entity system prompts via --append-system-prompt."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_VALID_ROLES = ("maestro", "lead", "vault")

# Default location: personalities/ at the repo root, two levels up from this file
# (src/hive/process/loops.py → src/hive → src → repo).
_DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "personalities"


def load_role_jd(role: str, base_dir: Path | None = None) -> str:
    """Read ``base_dir/role-<role>.md`` and return its contents.

    Reads are cached per ``(role, base_dir)`` so spawn-time injection is
    cheap. Cache invalidation is intentionally not supported — role JDs
    are static within a process lifetime.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"Unknown role {role!r}. Valid: {', '.join(_VALID_ROLES)}")
    target = base_dir if base_dir is not None else _DEFAULT_BASE_DIR
    return _read_role_file(role, target)


@lru_cache(maxsize=32)
def _read_role_file(role: str, base_dir: Path) -> str:
    return (base_dir / f"role-{role}.md").read_text()


LOOP_PROMPTS: dict[str, str] = {
    "ralph": (
        "Follow the RALPH loop: Read requirements, Ask clarifying questions, "
        "List approach options, Plan steps, Halt for review before executing."
    ),
    "ship-it": "Execute immediately without stopping for confirmation. Ship it.",
    "plan-act-observe": (
        "Follow the Plan-Act-Observe cycle: Plan your next step, "
        "Act on it, Observe the result, repeat."
    ),
    "build-test-refine": (
        "Follow Build-Test-Refine: Build the feature, Test it, Refine based on results."
    ),
}


# Maestro structural-identity blocks (Ticket 033), appended after the shared
# maestro role JD. The role JD is ownership-neutral; this block tells a maestro
# *which* kind it is, keyed on ``is_pa``. Kept here beside LOOP_PROMPTS — short
# prompt text as Python constants is already the idiom — so any harness adapter
# can append it without re-deriving the PA/project distinction.
MAESTRO_IDENTITY: dict[str, str] = {
    "pa": (
        "You are the PA Maestro — Hive's default route: every user message that "
        "names no maestro reaches you. You own no project of your own. You may "
        "read any project but write only to ownerless ones — never a project "
        "another maestro owns. You are not bound to a single codebase; you "
        "triage, plan, and delegate across whatever the user brings you. When "
        "work belongs to a project another maestro owns, coordinate through that "
        "maestro or the user — do not edit it yourself."
    ),
    "project": (
        "You are a project maestro — you own exactly one project and write only "
        "within its root. Your teams, plans, and reports serve that project. You "
        "are not the default route, and you do not act on other maestros' "
        "projects."
    ),
}
