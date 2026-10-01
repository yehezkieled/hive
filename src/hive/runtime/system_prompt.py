"""Harness-neutral system-prompt assembly shared by every Adapter.

The identity preamble, role JD and maestro identity block are plain prose, so
they live here rather than inside one Adapter (ADR 0001: no Claude-specific
code outside the Claude adapter).
"""

from __future__ import annotations


def build_system_prompts(*, name: str, role: str, system_prompt: str, is_pa: bool) -> list[str]:
    """The ordered prompt blocks an entity is spawned with."""
    prompts: list[str] = []
    if system_prompt:
        prompts.append(system_prompt)
    identity_lines = [
        f"You are {name}. Your role is {role}.",
        "If a hive_action is denied or fails, report the failure honestly. "
        "Do not narrate fictional success.",
    ]
    prompts.append("\n".join(identity_lines))
    from hive.process.loops import MAESTRO_IDENTITY, load_role_jd

    # T007: the loop framework is retired in favour of native /goal, seeded
    # on the first turn by message_dispatcher — no loop prompt appended here.
    if role in ("maestro", "lead"):
        prompts.append(load_role_jd(role))
    # State the maestro's structural role (PA vs. project) after the shared,
    # ownership-neutral role JD (Ticket 033). Maestro-only — leads never own
    # a project, so the distinction is meaningless for them.
    if role == "maestro":
        prompts.append(MAESTRO_IDENTITY["pa" if is_pa else "project"])
    return prompts
