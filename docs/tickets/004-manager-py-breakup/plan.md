# Plan — Ticket 004: break up `process/manager.py`

Split the 2,469-LOC `ProcessManager` god object into a thin facade +
four collaborator modules using composition (ADR 0006), as five serial
PRs with the test suite green between each. Zero behaviour change.

The full spec of each slice lives in its **GitHub issue** — this
ledger carries only the one-line summary + the orchestration. Design
rationale: `design.md`; module skeletons: `outline.md`.

## Slices

| Summary (what the slice delivers) | Issue | Type | Blocked by |
|-----------------------------------|-------|------|------------|
| Extract `approval_handler.py` — establishes the composition pattern | [#41](https://github.com/yehezkieled/hive/issues/41) | AFK | — |
| Extract `wake_scheduler.py` (owns `_wake_tasks` / `_wake_budget`) | [#42](https://github.com/yehezkieled/hive/issues/42) | AFK | #41 |
| Extract `message_dispatcher.py` (the `_last_*` rebind seam) | [#43](https://github.com/yehezkieled/hive/issues/43) | AFK | #42 |
| Extract `lifecycle_manager.py` (lock-heavy) | [#44](https://github.com/yehezkieled/hive/issues/44) | AFK | #43 |
| Thin core to ≈400 LOC + end-to-end maestro smoke | [#45](https://github.com/yehezkieled/hive/issues/45) | AFK | #44 |

## Execution waves

Single-issue waves — this ticket is **serial, not parallel**:

- Wave 1: #41
- Wave 2: #42
- Wave 3: #43
- Wave 4: #44
- Wave 5: #45

**Why serial (the rare serialize exception):** all five slices rewrite
the *same* file (`manager.py`) — each cuts a chunk out and re-thins the
core — so concurrent agents would conflict nonstop. On top of that,
#41 establishes the composition contract (`__init__(self, mgr)`
back-ref, facade delegation incl. private methods) that #42–#45 copy:
a real logical dependency, not just file overlap. Parallelism is
intentionally forgone here; the blocked-by chain is the whole point.

## Conventions

- Branch `ticket-004/issue-<n>-<slug>`, target `main`, squash-merge.
- Validation gate (every PR):
  `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`
- Autonomy: **all five AFK** — auto-merge on green CI + passing review
  agent. The fragile `_last_*` rebind (#43) is caught by existing test
  assertions, so a wrong implementation fails CI and won't merge.
- **Post-merge human check (not CI-able):** #45's maestro-turn smoke
  must be run by a human against the Tailscale IP — Telegram path *and*
  web path with an actual browser, not `curl`. Under All-AFK this
  happens *after* #45 merges; it is a gate on closing the ticket, not
  on merging the PR. Related: issue #40 (combined 004+007 smoke).

## Invariants every slice must preserve

(Verified by the boundary trace; see `design.md` §Hazard preservation.)

- Single `_state_lock` instance on the facade; reach via
  `self._mgr._state_lock`. No `await` inside a lock block.
- `_last_*` lists are **rebound** (`self._mgr._last_x = []`), not
  cleared locally — #43's headline risk.
- `_kickoff_tasks` / `_wake_tasks` GC-tracking sets stay facade-owned.
- The facade re-binds every method external code/tests reference —
  **public and private** (`_handle_actions`, `_get_or_create_adapter`,
  `_on_gate_state`, `_gate_nudge`, `_auto_kickoff`).
- Re-export `_render_auto_personality`, `_WAKE_ON_INBOUND_TEXT` (and
  `_adapter_config_from_entity`) from `manager.py` — tests import them.
- `from hive.process.manager import ProcessManager` never breaks.

## Cross-cutting impact

- **ADR 0006** (`docs/adr/0006-god-object-breakup-composition.md`) —
  already committed with `design.md`. Records composition-over-mixins
  as the Phase 2 house pattern.
- **CONTEXT.md** — no change (pure code-structure refactor, no new
  domain terms).
- **README / ARCHITECTURE** — no change (public surface preserved).
- **INDEX.md** — row 004 → `in progress`, Issues `#41–#45`.

## Out of scope

- Any behaviour change. Every slice is a verbatim move + `self.` →
  `self._mgr.` rewrites; existing tests pass unmodified.
- Fixing the pre-existing untracked `asyncio.create_task` in
  `_on_gate_state` (latent GC risk; noted in `design.md`, not a
  regression from this refactor).
- The Vault consolidation (Ticket 005), which will cite ADR 0006.

---

> **To build:** run the fleet Workflow against this `plan.md`. It reads
> the waves, spawns one worktree-isolated agent per slice, and runs
> branch → commit → push → PR → merge per the Conventions — one wave at
> a time, since the chain is serial. `run-ticket` plans only; it does
> not build.
