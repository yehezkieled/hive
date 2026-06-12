# Plan — Ticket 016: Migrate leaf dispatch: `spawn_worker` → Workflow

Ban Worker creation on every path (lead, maestro, user) and remove every
prompt that teaches the verb, per [design.md](design.md) and
[ADR 0013](../../adr/0013-retire-worker-creation-all-paths.md); 018 then
deletes the dead machinery. Full slice specs live in the GitHub issues —
this file is the orchestration layer only.

## Slices

| Summary (what the slice delivers) | Issue | Blocked by |
|-----------------------------------|-------|------------|
| Worker creation denied for all actors, with rejection feedback | [#109](https://github.com/yehezkieled/hive/issues/109) | — |
| All `spawn_worker` prompt surfaces trimmed + Workflow-authoring rules in the lead JD | [#110](https://github.com/yehezkieled/hive/issues/110) | — |
| `/worker spawn` arm removed (kill survives until 018) | [#111](https://github.com/yehezkieled/hive/issues/111) | — |

## Execution waves

- Wave 1: #109, #110, #111 (all parallel — no logical blockers; the
  system is coherent at every intermediate state, see
  [outline.md](outline.md) § Slice independence)

## Conventions

- Branch `ticket-016/issue-<n>-<slug>`, target `main`, squash-merge
- Validation gate (every PR): `ruff check src/ tests/ && ruff format
  --check src/ tests/ && pytest -m "not integration"`
- Autonomy: every slice is AFK — auto-merge on green CI + passing
  review agent
- Note for the fleet: #110 and #109/#111 touch `docs/DEPLOYMENT.md` on
  different lines — later merger rebases; no serialize needed

## Ticket-level verification (after all slices merge)

- `grep -rn "spawn_worker" personalities/ src/hive/process/scheduler.py
  src/hive/process/wake_scheduler.py` → zero hits
- Full suite green on `main`; deploy per `docs/DEPLOYMENT.md`
  (push → restart `hive.service` → journal check)
- Live (sprint DoD): deployed maestro→lead turn with a Workflow
  fan-out completing end-to-end; main checkout stays clean
- Kill any pre-existing Worker stragglers at deploy (`/worker kill`)

## Cross-cutting impact (✱)

- `docs/DEPLOYMENT.md:552,557` (rides #110), `:766` (rides #111)
- `README.md` — zero `spawn_worker` mentions (verified 2026-06-12);
  re-grep at build time
- Already landed with design: ADR 0013, `CONTEXT.md` (Worker
  retirement note + **Leaf agent** term), 025 ticket inherits
  orphan-sweep candidate scope

## Out of scope

- Deleting the Worker class/lifecycle/verb/`can_spawn_worker`/the
  `/worker` command — Ticket 018 (precondition now provable:
  mechanically zero ways to create a Worker)
- 017's progress bridge; mid-run steering (S6)
- Mechanical fan-out caps; Hive-side worktree GC (025 candidate)

To build: run the fleet Workflow against this `plan.md`.
