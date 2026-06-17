# 035 — Interaction pattern: `blackboard`

> The second pattern on the Track 2 delivery mechanism shipped in **034**.
> **Blocked by 034** — the mechanism + `debate` must land first; this is
> replication on a proven shape, not greenfield.

## What

Define and ship the `blackboard` interaction pattern on 034's delivery
mechanism: leaf agents read from and write to a **shared, evolving artifact**
(the "blackboard") so each builds on the others' partial results, instead of
working blind in parallel. Invokable by a Lead the same way as `debate`.

## Why

- 034 proves the mechanism on one pattern; `blackboard` is the second of the
  three named patterns in Track 2's library (debate / blackboard / tournament).
- It fits work where leaf agents must **share intermediate state** — incremental
  refinement, a shared scratchpad, progressive enrichment — which is distinct
  from `debate` (independent positions, then adjudication).

## Acceptance

- `blackboard` is defined (semantics: when to use it, how leaf agents share and
  converge on the shared artifact, the result shape) and shipped on 034's
  mechanism, invokable by a Lead.
- Documented in `CONTEXT.md` (glossary) and surfaced in `role-lead.md` alongside
  `debate`.
- Tests cover the pattern.

## Non-goals

- The delivery mechanism itself — owned by **034**.
- `tournament` (036) and `research-consolidate` (later).

## Notes

Blocked by 034; once the mechanism is fixed this is replication, not greenfield.
Likely medium, direct-lane. With 036, the natural **spill → S8** if 034 runs
long.
