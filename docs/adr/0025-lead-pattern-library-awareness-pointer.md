# ADR 0025 — Lead pattern-library awareness via a JD pointer; the Lead self-selects

- **Status:** Accepted — amends [ADR 0021](0021-further-patterns-as-global-skills.md);
  narrows [ADR 0020](0020-interaction-patterns-as-jd-recipes.md)'s
  maestro-names-the-pattern path
- **Date:** 2026-06-28
- **Tickets:** [044](../tickets/044-pattern-library-awareness/)

## Context

[ADR 0021](0021-further-patterns-as-global-skills.md) settled that further
interaction patterns ship as **user-authored global skills** (`~/.claude/skills`),
inherited by Leads via Ticket 012 / [ADR 0008](0008-per-role-skill-curation-denylist.md) —
zero Hive engine work. The plumbing already works: global skills are inherited
wholesale and interaction-pattern skills auto-allow for a Lead (they are
autonomous executor skills, not on the curation denylist).

That left one gap: **a Lead won't invoke a skill it doesn't know exists.** The
Lead role JD (`personalities/role-lead.md`) teaches only the inline `debate`
recipe (Ticket 034), and its `## Interaction patterns` intro still says "Today
one pattern is defined; more arrive on the same mechanism" — stale under 0021,
since further patterns arrive as *skills*, not JD recipes.

Separately, [ADR 0020](0020-interaction-patterns-as-jd-recipes.md) set an
asymmetric-depth model: the maestro gets a pattern **menu** and may name a
pattern in the contract; the Lead **runs** it. For the further (global-skill)
patterns the developer does **not** want to dictate selection — the Lead should
self-select. This serves the emerging loop-engineering aim: the loop
self-organizes its fan-outs without the human naming the shape.

## Decision

- **Add a thin (~5-line) awareness pointer** to `personalities/role-lead.md`'s
  `## Interaction patterns` intro: beyond the inline `debate` recipe, further
  coordination shapes live in the Lead's inherited **global-skill** library; the
  Lead **scans its skills and self-selects** when a fan-out matches a known
  shape. The pointer **replaces** the stale "more arrive on the same mechanism"
  sentence; the `### debate` recipe is untouched.
- **The Lead self-selects these further patterns; the maestro does not name
  them.** This narrows ADR 0020's "maestro names the pattern" path: it stays for
  the inline `debate` recipe, but the global-skill patterns are Lead-chosen, not
  maestro-dictated.
- **No other change.** The maestro role file is unchanged; the skill-curation
  denylist is unchanged (interaction-pattern skills already auto-allow for
  Leads); no engine work, no provisioning pipeline, no `CONTEXT.md` glossary
  change (the "Interaction pattern" entry already reflects 0021).

## Consequences

- **Positive:** closes the awareness gap with a doc-only edit; the loop can
  self-organize fan-outs without human dictation; the maestro JD, tool policy,
  and Workflow engine stay untouched. Reuses the inheritance path that already
  works (012 / 0008).
- **Negative / accepted:** delivery stays "available, the Lead chooses to
  invoke" — the same probabilistic floor ADR 0021 accepted. Awareness raises the
  odds a Lead reaches for a skill but does not guarantee it. The pointer also
  names patterns (`blackboard`, `tournament`) whose **skill files may not exist
  yet** — a dangling menu until the developer authors them; acceptable, since the
  Lead degrades gracefully to "no matching skill → author free-form" under the
  existing Authoring rules. The pointer couples Lead behaviour to the developer's
  personal `~/.claude` library (already true post-0021).
- The inline `debate` recipe (034) and the maestro menu (0020) stay as shipped.
- **Reversibility:** trivial — delete the pointer lines; ADRs 0020/0021 still
  stand as the underlying mechanism.

## Alternatives rejected

- **Add a matching maestro pointer** so the maestro names further patterns too —
  rejected: the developer wants the Lead to self-select these; a maestro pointer
  re-introduces the dictation 0021's spirit moves away from and complicates the
  asymmetric-depth model for no gain.
- **Source the skill files in-repo + install-at-deploy** so the menu is never
  dangling — rejected for this personal-project scope; ADR 0021 already leans
  against it and the developer owns the global library directly.
- **Append the pointer, keep the stale sentence** — rejected: the old sentence
  is false under 0021 and would contradict the pointer.

> **Append-only note.** ADRs 0020 and 0021 are left unedited (repo rule: never
> edit an existing ADR). This file records the amendment; `INDEX.md` carries the
> Ticket-044 cross-reference.
