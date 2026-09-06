# Questions — Ticket 034: interaction-pattern delivery mechanism + `debate`

The unknowns going in. A code-research pass (see `research.md`) plus a design
grill resolve these; `design.md` records the answers. Ordered by dependency —
Q1 is the root fork; the rest hang off it.

## Q1 — The delivery mechanism (the ADR fork)

*How* does a reusable interaction pattern reach a Team Lead? The ticket names
four candidates:

- **(A) JD-embedded recipe** — a canonical `## Interaction patterns` section in
  `personalities/role-lead.md`: each pattern = name + when-to-use + agent roles
  + a Workflow-script *skeleton* (the template, as a code block) + result shape.
- **(B) Per-pattern skill** — one Claude Code skill per pattern
  (`debate`, `blackboard`, …) under `~/.claude/skills` or a plugin.
- **(C) One routing skill** — a single skill that dispatches to the named
  patterns.
- **(D) Reusable Workflow script template** — a saved script
  (`.claude/workflows/<pattern>.js`) the Lead invokes by name.

**Deciding constraint:** where does the pattern's source-of-truth live, and how
does it reach the Lead at spawn? (Skills/scripts are *not* provisioned by Hive —
Entities inherit global `~/.claude` wholesale; a Lead's cwd is a per-project
worktree, so project-level skill/script discovery is unreliable. JD content is
in-repo and injected at spawn regardless of cwd.) See `research.md` §1, §4, §5.

## Q2 — Lead-only or also Maestro-usable?

Depends on Q1. Note the existing policy: a maestro has the `Workflow` tool
**denied** (`_MAESTRO_DENY`, ADR 0010) — it cannot drive a Workflow run at all.
A pattern that *is* a Workflow fan-out is therefore inherently Lead-scoped. Does
that settle "Lead-only," or do we want a separate maestro-level pattern concept
(different primitive)? See `research.md` §2, §5.

## Q3 — `debate` semantics

What exactly is `debate`? Agent roles (N debaters + an adjudicator?), the
fan-out shape (parallel independent positions → judge → verdict), the result
shape, and when to use it vs `blackboard` (shared evolving artifact) and
`tournament` (candidates pruned in rounds). Acceptance requires this defined.

## Q4 — ADR scope

Does ADR 0020 fix the **delivery mechanism only** (precedent: ADR 0010 decided
*Workflow adoption*, not use-case shapes), or also the `debate` *shape*? The
shape could instead live in `CONTEXT.md` + `role-lead.md`.

## Q5 — Lane: direct or fan-out?

Settled at `plan.md` once `design.md` reveals whether the mechanism slices.
Provisional: **direct** — one cohesive PR (ADR + `role-lead.md` section +
`CONTEXT.md` entry + tests), not 2+ independently-shippable slices.
