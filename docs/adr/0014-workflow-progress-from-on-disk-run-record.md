# ADR 0014 — Surface Workflow progress (and fix the false-timeout) by reading Claude Code's on-disk run record

- **Status:** Accepted
- **Date:** 2026-06-13
- **Ticket:** [017](../tickets/017-bridge-workflow-progress/) — **supersedes** [027](../tickets/027-workflow-run-false-timeout/)

## Context

Ticket 016 drained `spawn_worker` from the leaf path, so a Team Lead now does
all leaf work by driving the Claude Code **Workflow** tool inside one sync-wait
Turn (ADR 0010). That ADR deferred *visibility* to Ticket 017, naming the
exact regression: "leaf visibility regresses to *Lead busy → Lead done* until
Ticket 017's read-only watcher lands." Ephemeral **Leaf agents** are not
Entities — they never appear in the org tree — so during a run the dashboard
shows a Lead with nothing under it.

Two things were unknown until this ticket's research settled them, by
**inspecting a real Workflow run on disk**:

1. **Progress is tappable.** A run writes, under the Lead's own pinned session
   directory, a rich state snapshot `workflows/wf_<id>.json` (carrying
   `agentCount`, `phases`, `status`, `result[]`, `totalTokens`) **and** an
   append-only `subagents/workflows/wf_<id>/journal.jsonl` (`started` / `result`
   events per Leaf agent). The earlier assumption that progress was "internal to
   Claude Code, untappable" was wrong.

2. **The reader false-times-out healthy Leads.** The 2026-06-13 live smoke of
   016 (filed as Ticket 027) showed the transcript reader's 180s no-progress
   deadline killing a *healthy* Lead mid-run, after which the Maestro spawned a
   **duplicate team**. Root cause: the deadline keys off the **Lead's own
   transcript** (mtime + a pending-tool proxy), but a healthy run's activity
   lives in **different files** (the journal / Leaf transcripts) the reader never
   reads. The pending-tool guard added by ADR 0010 is a timing-fragile proxy,
   not a fix.

## Decision

Ship one ticket that both **surfaces** a run and **stops mistaking a live run
for a dead Lead**, by reading the same on-disk record.

- **Read-only progress bridge.** A single global **sweeper** (one tracked task,
  ~2s tick) iterates the registered adapters and calls a new
  `poll_workflow_progress()` that returns a uniform `WorkflowProgress`. It keeps
  an in-memory store, emits **discrete** notifications (`workflow_started` /
  `workflow_completed` / `workflow_failed`) through the existing
  `NotificationDispatcher` (Telegram + SSE toast + email), and the dashboard
  renders **one aggregate run-card under the Lead** via the existing 5s htmx
  re-render. Per-tick count/phase updates never enter the notification pipe —
  they live in the in-memory store the htmx poll reads — so a chatty run never
  spams Telegram.

- **Coupling quarantined in the Adapter (ADR 0001).** Only
  `ClaudeAdapter.poll_workflow_progress()` knows the `wf_<id>.json` /
  `journal.jsonl` layout. The sweeper, dispatcher, and dashboard stay
  harness-agnostic. The method is **ClaudeAdapter-only** for now (not on the
  `Runtime` ABC); a future Codex/OpenCode adapter opts in when it has an
  equivalent surface. The sweeper duck-types it.

- **Absorb 027: fix the false-timeout at its root.** Before the reader raises
  `TimeoutError`, it asks the adapter layer *"is a Workflow run alive for this
  session?"* (the run's journal/state mtime advanced within the window). If yes,
  it resets the no-progress deadline. This keys off **where the work actually
  is**, replacing the fragile Lead-transcript proxy. The user-facing timeout
  message is also made friendly (no leaked `/home/.../<uuid>.jsonl` path).

- **Honest failure / orphans.** Terminal `status` surfaces as a failed card +
  ping. A run still `status:"running"` whose owning adapter is no longer
  `is_busy()` is **orphaned** (the Lead's Turn ended without the run finishing —
  e.g. a real PTY crash) and is surfaced as "interrupted," never left as a
  silent spinner.

- **One ticket, not two (E2 over E1).** 017 absorbs 027 whole — the false-timeout
  fix *and* the friendly-message cleanup — rather than leaving 027 as a separate
  ticket. The progress bridge already computes the run-liveness signal the reader
  fix needs; splitting them would build that signal twice and leave the
  duplicate-team bug live and unscheduled. 027 is marked superseded.

## Consequences

- **Positive:** leaf visibility is restored without any persistent-Worker
  machinery; the duplicate-team bug is fixed at root, not patched; the
  notification noise problem is dodged structurally (ticks never become
  notifications); `WorkflowProgress` is one uniform shape for dashboard +
  Telegram.
- **Negative / accepted:** Hive now depends on Claude Code's **private,
  undocumented** on-disk workflow layout — a future binary bump could change it.
  Mitigation: the dependency is quarantined in one adapter method and **fails
  soft** (missing/changed files → no progress card, never crashes the Lead path),
  and the layout is a documented assumption to re-verify on each pinned-version
  bump (cf. the transcript-format risk in ADR 0012). A Phase-5 Codex/OpenCode
  adapter has no such files and must implement its own progress surface (or
  none). 017's blast radius now includes the turn-acceptance path
  (`await_next_assistant_turn`), the carefully-built ADR 0010/0012/026 lineage —
  hence this ADR.
- **Reversibility:** the sweeper, dashboard card, and notifications are cheap to
  revert. The reader liveness-reset is the load-bearing, harder-to-reverse
  piece (it changes when a Turn is declared dead) — the reason 027 is absorbed
  here under a written decision rather than done silently.

## Alternatives rejected

- **E1 — keep 017 pure visibility, leave 027 separate.** Cleaner ticket
  boundaries, but builds the run-liveness signal twice and leaves a live
  duplicate-team bug unscheduled. Rejected for coherence.
- **Per-agent org-tree rows** (mirroring the old Worker look). Contradicts the
  domain model (a Leaf agent has no org-tree presence, CONTEXT.md) and re-creates
  the per-worker visual 016 just deleted. Rejected; one aggregate card instead.
- **Pending-tool-guard-only** (the status quo from ADR 0010). A timing-fragile
  proxy on the Lead's transcript; the live smoke proved it starves. Rejected as a
  band-aid.
