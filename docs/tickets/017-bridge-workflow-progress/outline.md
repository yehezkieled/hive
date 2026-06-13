# Outline — Workflow progress bridge

Module + interface sketch for the approach in [`design.md`](design.md)
(decisions) and [ADR 0014](../../adr/0014-workflow-progress-from-on-disk-run-record.md).
Actionable slices live in [`plan.md`](plan.md).

The shape mirrors 003: **deep modules** (pure-ish, unit-testable without a live
PTY) behind thin **glue** that wires them into `ClaudeAdapter`,
`TranscriptReader`, `ProcessManager`, and the web view. Deep modules hold the
logic; glue holds the I/O.

## Deep modules

### ① `WorkflowProgress` — the uniform DTO

```python
@dataclass(frozen=True)
class WorkflowProgress:
    run_id: str            # "wf_3911b298-ffa"
    name: str              # workflowName
    phase: str | None      # current phase title, from phases/workflowProgress
    agent_count: int       # agentCount (M)
    done_count: int        # count(result) in journal (N)
    status: str            # "running" | "completed" | "failed" | ...
    partials: list | None  # last 3 results, ~280 chars each; dashboard-only (never in notifications)
```

Harness-neutral. Dashboard + Telegram both consume only this — no caller touches
CC's file shapes.

### ② CC run-record parser — `parse_run_dir(session_dir) -> list[WorkflowProgress]`

Pure function over a session working dir. Globs `workflows/wf_*.json`, reads each
snapshot, counts journal `result` events for `done_count`, caps `partials` (last
3, ~280 chars). **Fail-soft is per-file:** an `OSError`/`JSONDecodeError` on one
`wf_*.json` skips *that* file and continues with the rest; a missing dir or no
matches → `[]`; a bad journal line is tolerated. Never raises into the Lead path.

- **Why deep:** the entire "what runs exist and what state" question sits behind
  one signature over a directory. Unit-testable with **fixture run dirs**
  (committed sample `wf_*.json` + `journal.jsonl`), no PTY, no async. This is the
  one module that encodes CC's layout (ADR 0001 quarantine).

### ③ `ProgressStore` — in-memory state + change detection

`upsert(entity, run) -> list[Transition]`. Holds the last-seen `WorkflowProgress`
per `(entity, run_id)`; returns the discrete transitions a tick produced
(`started`, `completed`, `failed`, `interrupted`) and nothing for a plain
count/phase tick. Drops a run on terminal status.

- **Why deep:** the "did anything notification-worthy happen" logic is pure
  data-in/transitions-out. Testable: feed a sequence of snapshots, assert exactly
  one `started` then one `completed`; assert ticks emit nothing; assert
  `running`+`not_busy` ⇒ `interrupted`.

## Glue (thin wiring into existing code)

| Seam | Change |
|------|--------|
| `runtime/claude_adapter.py` | `poll_workflow_progress()` = `parse_run_dir(self._pty.session_dir)`; `workflow_active(window: float) -> bool` = "any run journal/state mtime **advanced within `window` s**" (existence is not enough — the no-hang guarantee). `is_busy()` already exists. |
| `runtime/pty_session.py` | Add public `session_dir` (`_session_path.with_suffix("")` or `None`); pass the adapter's `workflow_active` into the `TranscriptReader` it builds. |
| new `process/workflow_watcher.py` | The sweeper: every ~2s, `for name, adapter in manager._adapters` → duck-type `poll_workflow_progress` → `store.upsert` → for each transition, build a `Notification` and `await manager._notify(...)`. Holds the `ProgressStore`. |
| `__main__.py` / `process/manager.py` | Construct the watcher; start it as a **tracked** task (ticket 008); set `manager.progress_store` (post-construction, like `quota_monitor`/`scheduler`) for `view_model`. |
| `runtime/transcript_reader.py` | Accept an optional `workflow_active: Callable[[float], bool]`. At the deadline (before raising), if `workflow_active(timeout)` → `deadline = now + timeout; continue`. Replace the raw `TimeoutError` text with a friendly message (no path). |
| `web/view_model.py` | Pull active runs from `process_manager.progress_store` keyed by Lead; replace always-zero `workers` with an active-run indicator. |
| `web/templates/_partials/active.html` (+ `_macros.html`) | Render the aggregate run-card (name · phase · ▓ N/M · status). |

## Data flow

```
 <session>/workflows/wf_*.json + journal.jsonl
        │  parse_run_dir
        ▼
 [WorkflowProgress]  ──adapter.poll_workflow_progress()──▶  Sweeper (~2s)
        │                                                      │
        │                                          store.upsert(entity, run)
        │                                                      │
        │                              ┌── transitions ────────┴──── ProgressStore (in-mem) ──┐
        │                              ▼                                                        ▼
        │                   _notify(workflow_started/completed/failed)              web view_model reads store
        │                              │                                                        │
        │                 Telegram + SSE toast + email                          active.html aggregate card (htmx 5s)
        ▼
 adapter.workflow_active() ──▶ TranscriptReader: at deadline, reset if a run is alive (E2 / 027)
```

## Test seams

- **parse_run_dir** — fixture run dirs: a running snapshot (N<M), a completed
  one, a failed one, a half-written JSON, a **corrupt journal line**, a **mixed
  valid+invalid glob** (assert the valid one survives), a missing dir → assert
  the `WorkflowProgress` list (or `[]`) and that nothing raises. Plus a
  **truncation** case: a huge result payload → `partials` capped to 3 × ~280 chars.
- **ProgressStore** — snapshot sequences → assert the exact transition stream;
  `running`+`not_busy` → `interrupted`; ticks → no transition.
- **reader liveness-reset** — (i) quiet transcript while `workflow_active(t)`=True
  → **no** `TimeoutError`; (ii) `workflow_active(t)`=False past the window →
  **friendly** error (no path); (iii) **no-hang**: a stale orphan (file frozen at
  `status:"running"`, old mtime) → `workflow_active` returns False → reader times
  out instead of looping forever.
- **sweeper** — fake adapters returning scripted progress + a fake dispatcher →
  assert `_notify` called once per discrete transition, never per tick.
- **view_model / template** — store with an active run → assert the Lead card
  renders the aggregate run-card and the count line is not "0W".

## Implementation order (→ slices in plan.md)

1. **Foundation:** `WorkflowProgress` + `parse_run_dir` +
   `poll_workflow_progress()` + `workflow_active()` (fail-soft, fixtures). Blocks
   the rest.
2. **Sweeper + ProgressStore + notifications + orphan rule** (needs 1).
3. **Reader liveness-reset + friendly message** — absorbs 027 (needs 1's
   `workflow_active`).
4. **Dashboard aggregate card** (needs 2's store).
