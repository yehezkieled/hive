# ADR 0021 — Further interaction patterns ship as user-authored global skills, not Hive-native recipes

- **Status:** Accepted — amends [ADR 0020](0020-interaction-patterns-as-jd-recipes.md)
- **Date:** 2026-06-18
- **Tickets:** supersedes [035](../tickets/035-interaction-pattern-blackboard/)
  (`blackboard`) and [036](../tickets/036-interaction-pattern-tournament/)
  (`tournament`); narrows the *forward* mechanism of ADR 0020 (Ticket
  [034](../tickets/034-interaction-pattern-library/))

## Context

ADR 0020 fixed the **delivery mechanism** for interaction patterns as
Hive-native JD recipes — prose + a Workflow-script skeleton written into
`personalities/role-*.md`, pushed into every Entity's prompt at spawn — and
**rejected skills**. Ticket 034 shipped `debate` on that mechanism; 035
(`blackboard`) and 036 (`tournament`) were to replicate it, completing a
three-pattern named library.

Two facts reframe the *remaining* patterns:

1. **ADR 0020's skill objection was about _project-level_ skills, not global
   ones.** Its complaint — "a Lead's cwd is a per-project worktree, so
   `.claude/skills` discovery is unreliable" — applies to skills sitting in the
   worktree. **Global** skills in `~/.claude/skills` are discovered regardless
   of cwd, and Hive Entities already inherit the global library **wholesale**
   (Ticket 012, [ADR 0008](0008-per-role-skill-curation-denylist.md)), denylist-gated.
   So a globally-authored skill reaches every Lead **today, with zero Hive
   engine work** — the provisioning gap 0020 cited does not exist for global
   skills.

2. **The developer is authoring task-capabilities as global skills built on the
   Workflow tool directly.** A Lead invokes such a skill to *do a task*; the
   coordination shape (debate/blackboard/tournament-like) becomes an
   implementation detail *inside* the skill, not a first-class Hive-delivered
   recipe. This subsumes the need for a Hive-owned named-pattern **library**.

## Decision

- **Further interaction patterns / task-capabilities are delivered as
  user-authored global skills** (`~/.claude/skills`), inherited by Leads via the
  existing skill-curation path (012 / 0008). No Hive engine work, no JD recipe,
  no provisioning pipeline.
- **Supersede Tickets 035 (`blackboard`) and 036 (`tournament`).** The
  named-library track is retired; the three-pattern library goal is dropped.
- **`debate` (034) stays as shipped** — a Hive-native JD recipe. ADR 0020 is
  **not retracted** for what it delivered; this ADR narrows the forward
  mechanism only.

## Consequences

- **Positive:** zero Hive engine work for new coordination shapes; the developer
  owns and iterates them directly in the global library; the cwd fragility is
  sidestepped entirely; the Workflow engine and tool policy stay untouched.
- **Negative / accepted:** one shipped Hive-native pattern (`debate`) now
  coexists with a global-skill direction — a minor inconsistency. Global skills
  are **not in the Hive repo**, so they are not diffable, PR-reviewable, or
  unit-testable via Hive's prompt-assembly tests, and they couple Lead behavior
  to the developer's personal `~/.claude` library. Delivery is "available, the
  Lead chooses to invoke" rather than guaranteed-in-prompt — but JD adherence
  was already probabilistic (ADR 0020 §Consequences), so the floor is similar.
- **Phase 3:** the orchestration engine (015/016/017/018) and the
  pattern-mechanism proof (`debate`, 034) shipped; the named-library sub-goal is
  replaced by global skills. Whether Phase 3 is therefore "done" is a roadmap
  call — recorded in [`../roadmap.md`](../roadmap.md).
- **Reversibility:** cheap. `debate` already documents the JD-recipe path, and
  ADR 0020 still stands as the *how*; re-adopting Hive-native patterns later is
  "add a recipe + a glossary entry."

## Alternatives rejected

- **(b) Hive-provisioned skills** — build a pipeline that pushes Hive-owned skill
  files into each Entity at spawn. Real net-new engine work for no benefit over
  global skills here: the developer is the sole skill author and global
  inheritance already works. Rejected as over-engineering.
- **Keep 035/036 as JD recipes (the 0020 plan)** — works, but duplicates effort
  the developer now prefers to spend on global skills, and would ship two more
  Hive-native patterns the developer intends to express as skills anyway.

> **Append-only note.** ADR 0020 is left unedited (repo rule: never edit an
> existing ADR). This file records the amendment; `INDEX.md` and `roadmap.md`
> carry the cross-reference.
