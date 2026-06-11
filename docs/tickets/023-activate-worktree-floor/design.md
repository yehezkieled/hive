# 023 — Design (chosen approach)

Seeded by [`ticket.md`](ticket.md); grounded in [`research.md`](research.md);
grilled 2026-06-11 (six forks). Session-pinning decision recorded in
[ADR 0011](../../adr/0011-session-pinning-over-directory-heuristics.md).

## Decision in one line

Activate the worktree floor with a ~3-line DI fix made testable by a
composition-root factory, **and** stop guessing which transcript belongs
to which Entity (session pinning) — then make peer messaging
self-healing: a boss-alias so senders never type names, rejection
feedback so failed actions report back, spawn-failure notes to the
maestro, and a turn-end inbox check so busy entities never strand mail.

## The six decisions

### D1 — Floor + session pinning (not floor alone)

The floor (wire `WorktreeManager(PROJECT_ROOT, WORKTREES_DIR)` into
`ProcessManager` at the composition root) fixes lead/worker transcript
geography *and* file isolation. But binding stays a guess for any
entity sharing a project dir — and at least one always will: the PA
Maestro sits in the live checkout today by accident, and a future
`hive_dev` project Maestro will share its project dir with the owner's
own `claude` sessions *by design* (per the per-project Maestro model,
CONTEXT.md Relationships). F3 proved the failure is silent.

**Session pinning**: the adapter reads `~/.claude/sessions/<pid>.json`
(pid of the harness process it spawned) → `sessionId` → transcript is
`<project_dir>/<sessionId>.jsonl`, exactly. The new-or-growing
heuristic (`transcript_reader.py:60-96`) survives only as a fallback
when the pid-state file is missing/late. Trade-off (undocumented CC
interface) recorded in ADR 0011; Ticket 020 already plans to trust the
same file for `waitingFor`.

Rejected: maestro worktrees (a Maestro's home is its *project*, not a
Hive worktree — ownership model is Ticket 024); fuzzy dir heuristics
hardening (still a guess, just a fancier one).

### D2 — Addressing: parent alias + rejection feedback (both)

The kickoff context named the parent explicitly and the lead still
invented `maestro.strutils` — JD-only is disproven. Two mechanisms in
`_handle_actions`:

- **Alias**: `to:"maestro"` → org root (first dotted segment);
  `to:"parent"` → immediate parent. Resolved by the dispatcher; no
  name to mistype. No fuzzy matching — silent misdelivery is worse
  than a drop. Alias available to all roles; misuse (a maestro's
  `to:"maestro"` resolving to itself) is rejected by the existing
  self-message ban and explained by feedback.
- **Feedback**: any rejected action (unknown recipient, permission
  denied) → audit entry (`action_rejected`) + a system message queued
  to the **sender** stating what failed and the correct form →
  wake-on-inbound wakes it → it self-corrects next turn. Runaway
  correction loops are capped by the existing wake budget (6/60s).

### D3 — Spawn failure notifies the maestro; healing stays 020

A stillborn lead (F1: PTY died 3×, kickoff failed) currently leaves a
corpse on the org chart and a warning in a log nobody reads. Reuse
D2's feedback channel: spawn/kickoff failure → system note to the
owning maestro's inbox ("your lead `strutils` failed to start") →
wake. Auto-bounce/retry of jammed or dead sessions remains Ticket
020's whole job. 023 *notifies*; 020 *heals*.

### D4 — Turn-end inbox check

Wake-on-inbound is single-shot: a wake landing while the recipient is
busy is swallowed (`wake_scheduler.py:156-158`) and nothing retries —
mail parks for up to 120 minutes. After 016, busy-when-mail-arrives is
the *normal* case (30-minute Workflow turns). New invariant: **when a
turn completes, if the entity's queue is non-empty, schedule a wake**
(budget-respecting, same machinery). Didn't fire in the incident;
fixed here because 016 turns it from latent to load-bearing.

### D5 — Floor mechanics

- **5a**: use the existing `config.WORKTREES_DIR` constant (defined,
  mkdir'd, never imported). No env knob until a need exists.
- **5b**: no orphan sweep in 023 — crash-recovery/re-adoption is
  **Ticket 025** (opened from this grill).
- **5c**: extract `build_process_manager()` from `__main__.py` and
  unit-test that production composition passes a real
  `WorktreeManager`. This closes the exact gap class that shipped the
  bug: hermetic tests green while the composition root was broken.

### D6 — Defaults: document-only

Post-#90 the knob audit is clean (research Q9). One intentional shadow
gets a comment (maestro `permission_mode="yolo"` at
`lifecycle_manager.py:211` — first-spawn safety, deliberate);
`max_workers` stays a class constant until someone needs to tune it.
No code changes.

## Glossary impact

- **Session pinning** added to `CONTEXT.md` (Execution).
- Maestro relationships sharpened (PA Maestro, 1-project-1-maestro,
  resource-only maestro↔maestro) — committed during the grill.

## Out of scope (restated)

Worker→Workflow migration and changed-worktree cleanup (016); progress
bridge (017); auto-bounce/healing (020); the `user` router queue
(021); project ownership & PA write-policy (024); worktree
crash-recovery & orphan policy (025).
