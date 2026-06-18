# ADR 0020 — Interaction patterns ship as role-file (JD) recipes, lead-executes / maestro-names

- **Status:** Accepted
- **Date:** 2026-06-18
- **Ticket:** [034](../tickets/034-interaction-pattern-library/) — opens Phase 3 **Track 2**

## Context

Phase 3 Track 2 gives Team Leads a **named library of interaction patterns**
(`debate` / `blackboard` / `tournament`) — reusable shapes for structuring a
Workflow fan-out, so a Lead stops authoring the coordination shape free-form
every time (`role-lead.md` teaches free-form authoring today; no named patterns
exist). Ticket 034 must decide *how a pattern reaches a Lead* — a hard-to-reverse
choice, since it sets the shape every later pattern inherits — and prove it by
shipping one pattern, `debate`, end-to-end. This ADR fixes the **mechanism
only**; `debate`'s semantics are a definition in `CONTEXT.md` + `role-lead.md`,
not part of this decision (cf. ADR 0010, which decided *Workflow adoption*, not
the use-case shapes).

Two facts from the code, verified, constrain the choice:

1. **JD delivery has a spawn-time guarantee.** Each Entity's prompt is built by
   appending its role file (`personalities/role-{role}.md`) via `load_role_jd()`
   in `_adapter_config_from_entity`, on **every** spawn including restart. The
   content is in-repo, version-controlled, and present regardless of the Lead's
   working directory.

2. **Skills/scripts have none.** Hive ships **zero** custom skills — Entities
   inherit the global `~/.claude/skills` library wholesale; Hive's only skill
   lever is the *deny*list (`skill_curation.py`). A Lead's cwd is a per-project
   worktree (ADR 0010 worktree floor), so project-level `.claude/skills` /
   `.claude/workflows` discovery is unreliable, and a denylist-only policy lets
   new skills rot in (ADR 0008). Provisioning skills to Entities is net-new
   engine work that Ticket 034 lists as a non-goal.

A separate question rode along: are patterns **Lead-only or also
Maestro-usable**? The maestro already has `Workflow` in `_MAESTRO_DENY`
(`tool_policy.py:47`) — it cannot drive a run at all — so any Workflow-shaped
pattern is Lead-scoped by construction.

## Decision

**Deliver interaction patterns as role-file (JD) recipes — push, not pull —
with a shared vocabulary at asymmetric depth, and execution that stays
Lead-only.**

- **Mechanism (push).** A pattern is a named, canonical coordination recipe
  written into the role files that are already injected into every Entity's
  system prompt at spawn. No new tool, no skill file, no saved script, no change
  to the Workflow engine. The reusable "template" is a Workflow-script *skeleton*
  embedded in the recipe (a code block the Lead copy-adapts), so Option D's value
  is captured without a separate, unprovisioned artifact.

- **Asymmetric depth.** `role-lead.md` carries the **full executable recipe**
  (when-to-use, agent roles, the Workflow skeleton, the result shape).
  `role-maestro.md` carries a **menu** — pattern names + a one-line "when to use"
  — so a maestro can *name* a pattern in the contract it hands a lead. A named
  library is only "shared" if both sides know the names.

- **Lead-executes / maestro-names.** The maestro names a pattern; only the lead
  runs it. `Workflow` stays in `_MAESTRO_DENY` — no denylist change — so the
  Maestro→Lead→Workflow chain (ADR 0010) is preserved and Lead-only execution is
  free.

- **No new capability.** A Lead can already author parallel-agents-plus-judge
  with the Workflow tool. This ADR buys **consistency** (every Lead runs a
  pattern the same known-good way), **shared vocabulary** (the maestro can ask by
  name), and a **foundation** to replicate the remaining patterns cheaply in S8 —
  not automation.

## Consequences

- **Positive:** delivery is guaranteed on every spawn (incl. restart) with zero
  new plumbing; the library is in-repo, reviewable, diffable, and unit-testable
  via the prompt-assembly path; Lead-only execution needs no policy change; S8
  patterns are "add a recipe + a glossary entry"; the Workflow engine is
  untouched (a Track-2 non-goal honored).
- **Negative / accepted:** JD push guarantees the recipe is *delivered*, not that
  the Lead *follows* it — adherence is probabilistic (it is model guidance). A
  skill would be no better (the Lead must still choose to invoke it); only the
  authored Workflow script is deterministic, and the Lead authors that either
  way. So this is the maximum consistency available without engine work.
- **Negative / accepted:** patterns live as prose in two role files, so the
  vocabulary can drift between `role-lead.md` and `role-maestro.md` if edited
  carelessly; the glossary in `CONTEXT.md` is the single source of truth for the
  names.
- **Reversibility:** cheap to revert (delete the role-file sections + glossary
  entries). The load-bearing, harder-to-reverse part is the *commitment to JD as
  the pattern surface* — which is why it is recorded here rather than chosen
  silently.

## Alternatives rejected

- **(B) Per-pattern skill / (C) one routing skill.** Same content as JD but no
  spawn-time delivery guarantee: unprovisioned (Hive ships no skills), cwd-fragile
  from a Lead's per-project worktree, exposed to denylist rot, and a Lead-only
  skill would need to *invert* the denylist policy. Building a skill-provisioning
  pipeline is the engine work 034 excludes.
- **(D) Saved Workflow script (`.claude/workflows/*.js`).** Same provisioning /
  cwd problems, and Hive has no "load saved pattern and run" hook — Leads author
  scripts inline. Its reuse value is folded into the JD recipe's embedded
  skeleton.
- **Maestro-executable patterns.** Would require removing `Workflow` from
  `_MAESTRO_DENY`, collapsing the Maestro→Lead→Workflow chain (ADR 0010).
  Rejected — maestro names, lead runs.
