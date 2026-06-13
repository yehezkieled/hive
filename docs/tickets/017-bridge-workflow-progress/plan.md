# Plan — Ticket 017: bridge Workflow progress (and absorb 027)

Read-only bridge that surfaces a Lead's **Workflow run** (count / phase /
completion / failure) to the dashboard + Telegram, and fixes the reader's
false-timeout at its root. Structure from [`outline.md`](outline.md); decisions
from [`design.md`](design.md) + [ADR 0014](../../adr/0014-workflow-progress-from-on-disk-run-record.md).
GitHub is the work queue; **this ledger is the source of truth that travels with
the repo.** Each slice's full spec lives in its issue.

> **Supersedes Ticket 027** — the false-timeout fix and friendly-message cleanup
> are folded into slice #118. 027 is closed/superseded, not worked separately.

## Slices

| Summary (what the slice delivers) | Issue | Blocked by |
|-----------------------------------|-------|------------|
| Adapter run-record surface — `WorkflowProgress` + `poll_workflow_progress()` + `workflow_active()` (fail-soft, fixtures) | [#116](https://github.com/yehezkieled/hive/issues/116) | — |
| Progress sweeper + `ProgressStore` + discrete notifications + orphan rule | [#117](https://github.com/yehezkieled/hive/issues/117) | #116 |
| Reader liveness-reset + friendly timeout message (**absorbs 027**) | [#118](https://github.com/yehezkieled/hive/issues/118) | #116 |
| Dashboard aggregate run-card under the Lead (replace always-zero worker-count with an active-run indicator) | [#119](https://github.com/yehezkieled/hive/issues/119) | #117 |

All labelled `ready-for-agent` (#118 also `bug`). No parent/PRD issue — this
ledger plus `design.md` are the spec (run-ticket default).

## Execution waves

```
  Wave 1:  #116  (foundation — the adapter surface everything reads)
  Wave 2:  #117, #118   (parallel — both only need #116)
  Wave 3:  #119  (needs #117's ProgressStore)
```

Blockers are **logical only**: #117/#118 both consume #116's `WorkflowProgress`
/ `workflow_active`; #119 reads the store #117 builds. File overlap is the
fleet's problem (it merges one PR at a time; the later agent rebases) — no extra
edges.

## Conventions

- Branch `ticket-017/issue-<n>-<slug>`, target `main`, squash-merge.
- Validation gate (every PR): `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`.
- Autonomy: every slice is AFK — auto-merge on green CI + passing review agent.
- **#119 additionally needs a real browser smoke** (JS-rendered card — a 200 from
  curl is not sufficient, per CLAUDE.md).
- **Build-binding contracts** (design.md → *Robustness contracts* + *No-hang
  guarantee*) are not optional polish — the fleet must honour them:
  - `workflow_active(window: float) -> bool` takes the reader's `timeout` and is
    True only when a run's journal/state mtime **advanced within `window`** (not
    on mere existence) — this is what stops the reader hanging on a stale orphan
    (#116 defines it; #118 wires + tests it, incl. the no-hang case).
  - Fail-soft is **per-file** (one corrupt `wf_*.json` skips that file, not all);
    never raises into the Lead path (#116).
  - `partials` capped to last 3 × ~280 chars, **dashboard-only** — never carried
    in a Telegram/email notification (#116 caps, #117 enforces).
  - #118 friendly timeout message carries **no path** — e.g. *"Turn didn't
    complete within {timeout}s; if a Workflow is running it may still be working —
    check the dashboard."*
- **Layout smoke gate** (slice #116, run on deploy + on every ticket-009 binary
  pin): spawn a test Lead, trigger a real Workflow run, and assert the on-disk
  layout still matches research.md §1 (`wf_<id>.json` keys + `journal.jsonl`
  `started`/`result`). A mismatch fails loudly, naming the pinned CC version —
  this is the guard for the ADR 0014 private-layout coupling.

## Reference-doc impact (cross-cutting ✱)

- `CONTEXT.md` — **done** (added **Workflow run**).
- `docs/adr/0014-…` — **done**.
- `docs/tickets/INDEX.md` — 017 → in progress, issues #116–#119; **027 →
  superseded by 017**.
- `README.md` / `docs/ARCHITECTURE.md` — conditional: if #119 materially changes
  the documented dashboard surface, add a short note in that slice's PR (then it
  is a declared cross-cutting edit).

## Definition of done

Mirrors the Sprint `2026-Q2-S5` DoD lines for 017 (+ the absorbed 027):

- A running Workflow launched by a Lead surfaces progress (count / phase /
  completion) on the dashboard and pings Telegram on start + completion (#116,
  #117, #119).
- Failure / cancellation / orphan surfaces honestly, never a silent hang (#117).
- The reader no longer false-times-out a healthy Lead mid-run (no duplicate
  team), and the timeout message leaks no internal path (#118 — absorbs 027).
- `ruff` + `pytest -m "not integration"` green; a maestro→lead→Workflow turn is
  visible end-to-end on deployed code.
- INDEX rows: 017 → in progress (#116–#119); 027 → superseded.

## To build

Run the fleet Workflow against this `plan.md`.
