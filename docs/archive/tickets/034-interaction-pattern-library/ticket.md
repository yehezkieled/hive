# 034 — Interaction-pattern library: delivery mechanism + `debate` reference pattern

> Opens Phase 3 **Track 2**, the remaining Phase 3 theme. The named patterns
> (debate / blackboard / tournament, plus a stray "research-consolidate") appear
> across the roadmap and sprint docs but are **defined nowhere** — no code, no
> semantics, no ADR.

## What

Establish **how** a reusable interaction pattern is delivered to a Team Lead,
then ship **one** pattern — `debate` — end-to-end on that mechanism, so a Lead
can invoke a named pattern to structure a Workflow fan-out instead of authoring
the coordination shape from scratch. Mechanism + one real consumer in a single
ticket: a delivery mechanism with no pattern flowing through it cannot be
validated.

## Why

- The foundation is in place: Leads orchestrate via Workflow (ADR 0010),
  authoring is currently *free-form* (`role-lead.md`, no canonical template), and
  runs are observed read-only (ADR 0014).
- Track 2's job is to turn that free-form authoring into a shared, *named*
  vocabulary Leads compose with — the remaining Phase 3 work (deadline
  2026-07-11).
- Proving the mechanism on one pattern de-risks replicating the rest
  (blackboard / tournament) cheaply in S8.

## Acceptance

- An **ADR fixes the delivery mechanism** — the design fork: reusable Workflow
  script template vs. a per-pattern skill vs. one routing skill vs. JD-only — and
  whether patterns are Lead-only or also Maestro-usable (skill curation is
  **denylist-only**, ADR 0008, so a Lead-only skill must invert the policy).
- The `debate` pattern is **defined** (semantics: when to use, agent roles,
  result shape) and **shipped** on the chosen mechanism, invokable by a Lead.
- `debate` is documented in `CONTEXT.md` (glossary) and surfaced in
  `role-lead.md`.
- Tests cover the mechanism + the `debate` pattern.
- blackboard / tournament / research-consolidate are explicitly listed as S8
  follow-ups.

## Non-goals

- **blackboard / tournament / research-consolidate** patterns — S8, once the
  mechanism is proven.
- Mid-run Workflow **steering** (still later scope; runs stay read-only per
  ADR 0014).
- Changes to the Workflow tool itself — Track 2 is Lead-facing (JD / skill /
  templates), not engine work.

## Notes

Design-heavy and **ADR-worthy** (the delivery-mechanism decision is hard to
reverse) → full design grill in Phase B; the natural **spill candidate** to S8
if S7 overruns. Lane: likely direct (one cohesive PR), but confirm at `plan.md`
once `design.md` reveals whether the mechanism slices.
