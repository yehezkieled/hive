# Design — bridge Workflow progress (and absorb 027)

Chosen approach. Resolves the forks in [`questions.md`](questions.md); grounded
in [`research.md`](research.md); the load-bearing decision is recorded in
[ADR 0014](../../adr/0014-workflow-progress-from-on-disk-run-record.md). This
ticket is **cross-cutting ✱** and **supersedes Ticket 027**.

## Resolved forks

| Fork | Decision |
|------|----------|
| **A** progress tap | Read the run's **on-disk record** under the Lead's pinned session dir: poll `workflows/wf_<id>.json` (rich state) + tail `subagents/workflows/wf_<id>/journal.jsonl` (per-agent deltas). Settled empirically (research.md §1). |
| **B** watcher shape | **Global sweeper** — one tracked task (~2s), iterates `manager._adapters`. CC-file knowledge is **quarantined** in `ClaudeAdapter.poll_workflow_progress()` (ADR 0001); ClaudeAdapter-only for now, sweeper duck-types it. |
| **C** noise | Phone buzzes on **discrete** events only — start, completion, failure. Per-tick count/phase updates are **dashboard-only** (in-memory store → htmx), never enter the notification pipe. |
| **D** rendering | **One aggregate run-card under the Lead** (name / phase / N-of-M / status). Not per-agent rows (Leaf agents have no org-tree presence). Fix the now-stale `…W` worker count too. |
| **E** failure / 027 | **E2** — 017 absorbs 027 whole: reader resets its no-progress deadline on **journal liveness** (root-cause fix), friendly timeout message, and an **orphan rule** (`is_busy` × `status`). |
| **F** schema | Uniform `WorkflowProgress(run_id, name, phase, agent_count, done_count, status, partials)`; kinds `workflow_started` / `workflow_completed` / `workflow_failed`. |
| **G** ADR | **Yes — ADR 0014** (couples to CC's private file layout + edits turn-acceptance; a real trade-off). |

## The on-disk surface (recap)

A Lead's `PtySession` already pins `_session_path` (the `<uuid>.jsonl`, ADR 0011).
Strip `.jsonl` → the session working dir, which holds:

```
<session>/workflows/wf_<id>.json          ← state snapshot: agentCount, phases,
                                              status, result[], totalTokens (rewritten through the run)
<session>/subagents/workflows/wf_<id>/
        journal.jsonl                      ← append-only: {started|result, agentId, result?}
        agent-<id>.jsonl                   ← each Leaf agent's transcript (not read by 017)
```

`status:"completed"` = success; any other terminal value = failure/cancel. The
journal gives `count(started)` / `count(result)` = live N-of-M; `wf_<id>.json`
gives phase + status.

## Architecture

```
 ┌─ Sweeper (1 tracked task, ~2s) ──────────────────────────────────────────────┐
 │   for name, adapter in manager._adapters:                                      │
 │       for run in adapter.poll_workflow_progress():   # [WorkflowProgress]      │
 │           store.upsert(name, run)                    # in-memory ProgressStore │
 │           if discrete_transition(run):               # started/done/failed     │
 │               await manager._notify(text, kind, data)                          │
 └───────────────┬───────────────────────────────────────────────┬───────────────┘
                 │ discrete events only                           │ ProgressStore
                 ▼                                                 ▼
        NotificationDispatcher                          view_model (per-Lead runs)
        ├─ TelegramBridge  → phone ping (start/done/fail)         │
        ├─ SSEBroker       → dashboard toast                      ▼
        └─ EmailDigest     → digest                    active.html: aggregate run-card
                                                       (htmx re-render every 5s)

 ┌─ ClaudeAdapter (the ONLY CC-coupled code) ───────────────────────────────────┐
 │   poll_workflow_progress() -> list[WorkflowProgress]:                          │
 │       sdir = self._pty._session_path.with_suffix("")   (or [] if unresolved)   │
 │       parse (sdir/"workflows").glob("wf_*.json")  [+ journal for done-count]    │
 │   workflow_active() -> bool:   # liveness for the reader (E2)                   │
 │       any wf journal/state mtime advanced within the no-progress window         │
 └───────────────────────────────────────────────────────────────────────────────┘
```

## Components to change

| Component | Change |
|-----------|--------|
| `runtime/claude_adapter.py` | New `poll_workflow_progress() -> list[WorkflowProgress]` (parses `wf_<id>.json` + journal; fails soft) and `workflow_active(window: float) -> bool` (True iff a run's journal/state mtime **advanced within `window` seconds** — existence alone is not enough; see Robustness). The only code that knows the CC file layout. |
| `runtime/pty_session.py` | Add a public `session_dir` accessor (`_session_path.with_suffix("")` or `None`) so the adapter doesn't reach a private field; wire the adapter's `workflow_active` into the `TranscriptReader` it constructs. |
| `runtime/base.py` | **No change** — keep `poll_workflow_progress` off the `Runtime` ABC (YAGNI until Phase 5); sweeper duck-types via `getattr`/`hasattr`. |
| new `process/workflow_watcher.py` | The sweeper + in-memory `ProgressStore` (a ProcessManager collaborator, composition per ADR 0006). Change-detection + discrete-event emission via `_notify`. |
| `__main__.py` / `process/manager.py` | Construct + start the sweeper as a **tracked** task (ticket 008); set the store as `manager.progress_store` (post-construction, matching the `quota_monitor`/`scheduler` pattern) so `view_model` reads it via `process_manager.progress_store`. |
| `notifications/dispatcher.py` | Add `workflow_started/completed/failed` kinds (no routing map needed — only discrete events are dispatched; ticks never reach here). |
| `runtime/transcript_reader.py` | **E2:** before raising `TimeoutError`, consult an injected `workflow_active(timeout)` predicate; if a run is alive, reset the deadline. Replace the raw error string with a friendly message. Keep the existing sentinel/pending-tool ladder. |
| `web/view_model.py` | Read `process_manager.progress_store`; attach active runs per Lead; replace the always-zero `workers` count with an active-run indicator. |
| `web/templates/_partials/active.html` (+ `_macros.html`) | Render the aggregate run-card under the Lead. |

## E2 — the reader fix (root cause, not band-aid)

The 180s deadline resets on the **Lead's own transcript** (mtime, or a pending
tool_use proxy — `transcript_reader.py:206-209, 248-249`). A healthy run's
activity is in **other files**, so the proxy starves and a healthy Lead is
declared dead → the Maestro spawns a duplicate team (027, live smoke).

```
 Lead transcript:  [TaskOutput pending] ──── frozen ~6 min ────▶    (A)/(B) can't reset
 journal.jsonl:    ░▓▓░▓░▓▓░▓░▓▓   ← ALIVE, but reader never looks here

 Fix: reader, at the deadline, asks adapter.workflow_active(timeout)? → True → reset.
      Key off where the work IS (the journal), not the proxy (the Lead transcript).
```

`workflow_active(window)` lives in the adapter (file-knowledge quarantine, fork
B), so the reader gains a predicate, not file-layout awareness. It is passed the
reader's own `timeout` as the window so the two never diverge.

**No-hang guarantee (load-bearing).** `workflow_active(window)` returns True only
when a run's journal/state mtime **advanced within `window` seconds** — *not* when
a `wf_*.json` merely exists. So an **orphaned** run (the Lead's Turn died, files
frozen at `status:"running"`) has a stale mtime → `workflow_active` returns False
→ the reader stops resetting and times out (with the friendly message) instead of
looping forever. The sweeper's orphan rule (below) independently closes the card
within ~2s. The two mechanisms agree: a run is "alive" only while its files keep
moving.

## Orphan rule (honest failure, acceptance #3)

```
                    is_busy()=True              is_busy()=False
 status running     ● live card                ⚠ ORPHAN → "interrupted" card + ping
 status completed   (→ terminal next tick)      ✓ done card + ping
 status failed/…    ⚠ failed card + ping        ⚠ failed card + ping
```

`ClaudeAdapter.is_busy()` already exists (`claude_adapter.py:162`, used by
idle-kill). With E2 in place, orphans become rare (real PTY crashes only) — the
rule is the safety net, not the common path.

## Robustness contracts (build-binding)

These are load-bearing because the parse runs in-band with a live Lead and the
file layout is a CC-private dependency (ADR 0014).

- **Fail-soft is per-file, not per-poll.** `parse_run_dir` globs `wf_*.json` and
  parses each independently: an `OSError`/`JSONDecodeError` on one file logs at
  debug and **skips that file**, never blanks the others. A missing session dir
  or no `wf_*.json` → `[]`. Journal `done_count` parsing tolerates a bad line
  (same per-line pattern as `transcript_reader`). Nothing here raises into the
  Lead path.
- **Torn reads self-heal.** `wf_<id>.json` is rewritten as state changes; a poll
  may catch it mid-write. Read the whole file then parse; on failure, skip this
  tick — the 2s sweeper and the reader's own re-poll recover on the next tick.
  (Atomicity of CC's write is **verify-on-deployed-binary**, not assumed.)
- **Partials are capped and dashboard-only.** `WorkflowProgress.partials` holds
  at most the **last 3** agent results, each truncated to ~280 chars. They are
  rendered only on the dashboard card; **Telegram/email notifications never carry
  partials** (only the start/done/fail summary line), so a large result payload
  can never choke the SSE queue or exceed Telegram's message limit.

## Slices (→ [`plan.md`](plan.md), fan-out)

1. **Adapter progress surface (foundation)** — `WorkflowProgress`,
   `poll_workflow_progress()`, `workflow_active()`; fail-soft + unit tests over
   fixture run dirs. Blocks the rest.
2. **Sweeper + ProgressStore + discrete notifications + orphan rule** — the
   watcher task, change-detection, `_notify` on start/done/fail/interrupted.
3. **Reader liveness-reset + friendly timeout message (absorbs 027)** — the E2
   turn-acceptance fix; tests for "run alive → no timeout" and the message.
4. **Dashboard aggregate run-card** — view model + partial; stale-count fix.

Logical blockers only: **1 → {2, 3}**, **2 → 4**.

## Reference-doc impact (cross-cutting ✱)

- `CONTEXT.md` — **done** (added **Workflow run**).
- `docs/adr/0014-…` — **done**.
- `docs/tickets/INDEX.md` — flip 017 → in progress with issue range; mark **027
  superseded by 017**.
- `README.md` / `docs/ARCHITECTURE.md` — if the run-card materially changes the
  documented dashboard surface, add a short note in the implementing slice
  (conditional).

## Non-goals (reaffirmed)

- **Steering** a run from the dashboard/phone (write-back) — S6+.
- The interaction-pattern library (Track 2).
- Per-agent org-tree rows (fork D, rejected — possible future).
- Putting `poll_workflow_progress` on the `Runtime` ABC (defer to Phase 5).
