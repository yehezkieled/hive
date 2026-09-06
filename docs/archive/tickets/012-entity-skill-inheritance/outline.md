# Outline — Ticket 012: Curate & expose CC skills to Entities

Implementation structure for the [design](design.md). Direct lane — one
PR. Module sketch + assembly order.

## New module: `src/hive/process/skill_curation.py`

```python
"""Per-role Claude Code skill denylist (Ticket 012, ADR 0008)."""

# Skills that pause for a human ("thinking" skills) — safe only for the
# Maestro, whose gates bridge to Telegram. Exact Skill() deny tokens.
_THINKING_DENY: list[str] = [
    "Skill(grill-me)", "Skill(brainstorming)", "Skill(grill-with-docs)",
    "Skill(improve-codebase-architecture)", "Skill(capture)", "Skill(curate)",
    "Skill(cc-freeze)", "Skill(triage)", "Skill(plan-next-sprint)",
    "Skill(run-ticket)", "Skill(initiate-project)",
]

# Needs hands-on driving of a built artifact — un-bridgeable for any role.
_ALL_ROLES_DENY: list[str] = ["Skill(prototype)"]

def skill_denylist_for(role: str) -> list[str]:
    deny = list(_ALL_ROLES_DENY)
    if role in ("lead", "worker"):
        deny += _THINKING_DENY
    return deny
```

## Wiring

`_adapter_config_from_entity()` (`lifecycle_manager.py:109`) merges the
denylist into `disallowed_tools`, deduped, preserving existing tokens:

```python
disallowed = list(entity.disallowed_tools) + skill_denylist_for(entity.role)
# pass deduped `disallowed` to ClaudeAdapterConfig(disallowed_tools=...)
```

Flows unchanged from there: `ClaudeAdapterConfig.disallowed_tools` →
`_build_pty_extra_args` → `--disallowedTools` (`claude_adapter.py:87-88`).

## JD prose

Add a `## Skills — when to use` section to each
`personalities/role-{maestro,lead,worker}.md`, after the messaging/org
sections and before `## Honesty`:

- **worker / lead** → the autonomous executor set (`tdd`, `diagnose`,
  `systematic-debugging`, `research-codebase`, `requesting-code-review`,
  `using-git-worktrees`, `verification-before-completion`).
- **maestro** → adds the thinking set for clarifying goals with the user
  (`grill-me`, `brainstorming`) before spawning a Team.

## Tests (`tests/process/test_skill_curation.py` + adapter assertion)

1. `skill_denylist_for("worker")` and `("lead")` contain all
   `_THINKING_DENY` + `_ALL_ROLES_DENY` tokens.
2. `skill_denylist_for("maestro")` contains `_ALL_ROLES_DENY` and **none**
   of `_THINKING_DENY`.
3. On the mocked-PTY / Fake-adapter seam: a spawned Worker's CLI args carry
   `--disallowedTools … Skill(grill-me) …`; a Maestro's do not.
4. Pre-existing `entity.disallowed_tools` (Agent/Task for maestro/lead) are
   preserved alongside the skill tokens.

## Assembly order

1. `skill_curation.py` + its unit test (red→green).
2. Wire into `_adapter_config_from_entity`; adapter-seam assertion.
3. JD `## Skills` sections.
4. Build-time check of the `brainstorming` token form on the pinned binary.
5. Host smoke: blocked skill absent, allowed skill invokes.
