# 036 — Interaction pattern: `tournament`

> The third pattern on the Track 2 delivery mechanism shipped in **034**.
> **Blocked by 034.** With 035, completes the named interaction-pattern library
> → **Phase 3 done**.

## What

Define and ship the `tournament` interaction pattern on 034's delivery
mechanism: multiple leaf agents produce **competing candidates**, which are then
pruned/ranked in rounds (bracket-style) until a winner remains — for
"generate N attempts, pick the best" work. Invokable by a Lead.

## Why

- The third of the three named patterns in Track 2's library; completing the
  debate / blackboard / tournament set **completes Phase 3**.
- It fits **wide solution spaces** where you want diverse attempts then
  selection — distinct from `debate` (adjudicated positions) and `blackboard`
  (shared incremental refinement).

## Acceptance

- `tournament` is defined (semantics: candidate generation, round structure /
  scoring, winner selection, result shape) and shipped on 034's mechanism,
  invokable by a Lead.
- Documented in `CONTEXT.md` (glossary) and surfaced in `role-lead.md`.
- Tests cover the pattern.

## Non-goals

- The delivery mechanism itself — owned by **034**.
- `blackboard` (035) and `research-consolidate` (later).

## Notes

Blocked by 034; replication on the proven mechanism. Likely medium, direct-lane.
With 035, the natural **spill → S8** if 034's design runs long.
