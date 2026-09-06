# Plan — Ticket 018: Retire the persistent Worker entity

Delete the dead `Worker` entity type that 016 drained, per
[design.md](design.md) and [outline.md](outline.md). The retirement *decision*
is [ADR 0013](../../adr/0013-retire-worker-creation-all-paths.md) — 018
executes it, so **no new ADR**. Full slice specs live in the GitHub issues;
this file is the orchestration layer only.

## Slices

| Summary (what the slice delivers) | Issue | Blocked by |
|-----------------------------------|-------|------------|
| Spawn-action-chain peel — delete the dead `spawn_worker` path | [#132](https://github.com/yehezkieled/hive/issues/132) | — |
| Remove the `/worker` command surface (keep `/kill`, `/swarm`) | [#133](https://github.com/yehezkieled/hive/issues/133) | — |
| Drop Worker branches from the permission matrix | [#134](https://github.com/yehezkieled/hive/issues/134) | — |
| Remove Worker serialization from the dashboard | [#135](https://github.com/yehezkieled/hive/issues/135) | — |
| Glossary + docs — tombstone the Worker term | [#136](https://github.com/yehezkieled/hive/issues/136) | — |
| Delete the Worker entity type (atomic core) + DB cleanup guard | [#137](https://github.com/yehezkieled/hive/issues/137) | #132 |

## Execution waves

- **Wave 1:** #132, #133, #134, #135, #136 (all parallel — independent dead-
  behaviour peels; each leaves CI green, see [outline.md](outline.md) § Slice
  independence)
- **Wave 2:** #137 (the atomic type deletion — after #132 removes the
  constructor; ideally after all of Wave 1 to keep its diff small)

## Conventions

- Branch `ticket-018/issue-<n>-<slug>`, target `main`, squash-merge
- Validation gate (every PR): `ruff check src/ tests/ && ruff format --check
  src/ tests/ && pytest -m "not integration"`
- Autonomy: every slice is AFK — auto-merge on green CI + passing review agent
- **Atomicity note:** #137 must land every remaining `Worker` reference in one
  commit (Python import) — do NOT split it. The Wave-1 peels exist precisely to
  shrink it.
- **No serialize edges needed:** peels touch mostly disjoint files; where #137
  and a peel share a file (`dispatch.py`, `message_dispatcher.py`,
  `permissions.py`) they touch different lines — the later merger rebases.

## Ticket-level verification (after all slices merge)

- `grep -rn "import.*Worker\|isinstance(.*Worker)\|spawn_worker" src/ tests/`
  → zero hits; `grep -rn "\bWorker\b" src/` → zero class references
- Full suite green on `main`; `ruff` clean
- Deploy per `docs/DEPLOYMENT.md` (push → restart `hive.service` → journal
  check); `/worker kill` any straggler **before** #133 removes the command,
  then confirm the #137 DELETE guard leaves no `role='worker'` rows
- Live (shared 016/018 DoD): a deployed maestro→lead→leaf Workflow turn
  completes end-to-end; main checkout stays clean
- Org model is now: persistent = Maestro / Lead, ephemeral = Leaf agents

## Cross-cutting impact (✱)

- `CONTEXT.md` glossary tombstone (#136) + `README.md` + `docs/DEPLOYMENT.md`
  reference edits (#136)
- ADRs **not** edited (append-only); no new ADR
- `docs/tickets/INDEX.md` — 018 → in progress, issues #132–#137

## Out of scope

- The Workflow engine / leaf migration (015 / 016 — prerequisites)
- The interaction-pattern library (Track 2)
- Dropping the shared `entities` columns (TeamLead needs them)

To build: run the fleet Workflow against this `plan.md`.
