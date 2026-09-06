# Design — Ticket 018: Retire the persistent Worker entity

Chosen approach for deleting the `Worker` entity type, grounded in
[`research.md`](research.md). The *decision* to retire Workers is already
recorded in [ADR 0013](../../adr/0013-retire-worker-creation-all-paths.md);
018 is its **execution**, so **no new ADR** — this file is the build contract.

## The governing constraint

`Worker` is an isolated `Entity` subclass selected by a load-bearing role
**string** (`role == "worker"`), never by `isinstance` in role-sensitive
paths. So deletion is subtraction, not surgery — Maestro / Lead / Vault are
untouched **if** the role-string branches go in lockstep.

The one hard rule is **Python import atomicity**: `Worker` is imported in 6
files and `isinstance`-checked in ~15. You cannot land "delete `worker.py`"
while anything still imports it. → **the type deletion is one atomic commit.**

## Approach: peel the dead behaviour, then cut the type

Five independent **peels** remove code that *uses* Worker (each leaves CI
green and shrinks the core), then one atomic **core** removes the *type*.

```
 Wave 1 — peels (parallel, independent):
   A spawn-action-chain   D dashboard
   B /worker command      E glossary + docs
   C permission matrix
 Wave 2 — atomic core:
   F TYPE DELETION  (worker.py + 6 imports + ~15 isinstance + role registries
                     + persistence reconstruction + the DELETE guard + own tests)

 Only logical blocker:  A ──▶ F   (A removes the constructor lifecycle.spawn_worker;
   F then deletes the class). B/C/D/E independent of F — Wave-1 only to keep F's diff small.
```

Each slice is **vertical**: it carries its own test changes. There is no
separate "tests" slice.

## Decisions

### D1 — Lane: FAN-OUT, 6 slices  ✓ confirmed
**Why not direct (one PR):** the core is atomic and large; a 25-file deletion
PR is unreviewable, and the peels (A–E) each tell a self-contained story that
merges and ships on its own. Fan-out gives reviewable units and lets the AFK
fleet parallelise Wave 1.
**Why not finer (per-file slices):** the core can't be subdivided (import
atomicity); over-slicing the peels just adds rebase churn for no review gain.

### D2 — Glossary: TOMBSTONE + REDIRECT  ✓ confirmed (was Q10)
Replace the `CONTEXT.md` "Worker" entry (`:23-30`) with a one-line tombstone
pointing at the existing **"Leaf agent"** term; fix the stale relationship
lines (`Team = Lead + Workers` → `Lead + Workflow runs`, lines `:20,:40-44,
:174`) and the illustrative mentions (`:60,:107,:184-185`).
**Why not full delete:** six **append-only ADRs** (0004/0008/0009/0010/0013/
0014) still say "Worker" and can't be edited — the tombstone is the glossary
anchor a future reader lands on. Cost: one line.
**Tombstone text (proposed):**
> **Worker** *(retired)*: the former persistent leaf Entity. Worker creation
> was banned in ADR 0013 (Ticket 016) and the type deleted in Ticket 018.
> Leaf work now runs as ephemeral **[[Leaf agent]]s** inside a Lead's
> **Workflow run**.

### D3 — Persistence: DEFENSIVE DELETE GUARD  ✓ confirmed (was Q5/Q6)
Keep the shared columns (`worktree_path`, `task_id`, `parent_name`,
`team_name` — TeamLead uses them; schema is append-only). Add a one-time,
idempotent startup guard `DELETE FROM entities WHERE role = 'worker'` (in the
restore path, `__main__.py:270-276` / `entity_store`), **plus** the deploy-time
`/worker kill` of stragglers that already lands in 016's deploy.
**Why not tolerant-fallback only:** `_row_to_entity`'s `:173` fallback
degrades a stray row to a base `Entity` with an invalid `role='worker'` — no
crash, but a **zombie** nothing reaps, cluttering `rebuild_hierarchy` and the
org tree. The guard is ~2 lines and idempotent.

### D4 — Denial stubs: DELETE BOTH, no guard  ✓ resolved in code (was Q4)
Delete the `spawn_worker` parse branch (`actions.py:280-298`) **and** the
dispatcher denial branch. Confirmed: `actions.py:362-363` already rejects an
unknown action type gracefully (`"Unknown action type … skipped."` + warning),
so a hallucinated `spawn_worker` post-deletion is handled — **no thin
retired-action guard required.**

## Slice → surface map (full refs in research.md)

| Slice | Removes | Key sites | Tests |
|-------|---------|-----------|-------|
| **A** spawn-chain | the dead creation path | `actions.py:84,280-298`; `permissions.py:137-143` + `manager.py:26`; `message_dispatcher.py:30,307,489-575`; `lifecycle_manager.py:352-440`; `manager.py:134,392-402,564`; `config.py:205` | DELETE `test_spawn_worker*`, `TestMaxWorkersEnforcement`; REDEFINE `*_denied`, `TestSpawnWorkerAction` |
| **B** /worker cmd | the command surface | `commands/dispatch.py:84,275-276,686-706`; `telegram/commands.py:76`; `telegram/help_text.py:113-118` | REDEFINE `test_commands.py` worker arms → kill-gone |
| **C** perm matrix | worker messaging branches | `permissions.py:53-54,65-67,98-103,118-120` | trim `test_permissions.py` / `test_peer_messaging.py` worker rules |
| **D** dashboard | worker serialization | `web/app.py:99-105`; `view_model.py:80-164` comments; `_macros.html:91-93`; `landing.html:738` | adjust `web` tests asserting org shape |
| **E** glossary+docs | doc references | `CONTEXT.md` (per D2); `README.md:5`; `DEPLOYMENT.md:36,555-557,567-570` | — |
| **F** core (atomic) | the type itself | `rm models/worker.py`; 6 imports; role registries `entity.py:198,201,308`, `loops.py:8`, `claude_adapter.py:64,109`; isinstance guards `entity_store.py:98-171`, `lifecycle_manager.py:266,481,491`, `manager.py:248,253,364,605`, `message_dispatcher.py:775`, `approval_handler.py:56-57,617`, `dispatch.py:24,1204-1210`; **+ D3 DELETE guard** | DELETE/repoint all remaining Worker-constructing tests (`test_team`, `test_entity_store.test_load_worker_*`, `test_role_jd`, `integration/test_lead_worker_roundtrip`) |

## Cross-cutting impact ✱

- **`CONTEXT.md`** — glossary edit (D2). Free to edit anytime; lands in slice E.
- **`README.md`, `docs/DEPLOYMENT.md`** — reference-doc edits in slice E
  (declared here per the cross-cutting rule).
- **ADRs** — **read-only**; none edited. No new ADR (ADR 0013 governs).
- **`docs/tickets/INDEX.md`** — flip 018 → in progress with the issue range.

## Verification (ticket-level, after all slices)

- `grep -rn "Worker\|spawn_worker" src/ tests/` → only intended residue
  (e.g. comments in append-only ADRs are out of `src/`); **zero** `Worker`
  class refs, **zero** `import.*worker`.
- `ruff check src/ tests/ && ruff format --check src/ tests/`.
- Full `pytest -m "not integration"` green.
- Deploy → `/worker kill` any straggler → restart → confirm DELETE guard
  leaves `entities` with no `role='worker'` rows → one maestro→lead→leaf
  Workflow smoke completes end-to-end, main checkout clean (shared with 016's
  live DoD).

## Out of scope

- The Workflow engine / leaf migration (015 / 016 — prerequisites).
- The interaction-pattern library (Track 2).
- Dropping the shared `entities` columns (TeamLead needs them).
