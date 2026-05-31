# Questions — design forks to resolve

> **RESOLVED 2026-05-30.** Every fork below is answered. The chosen
> approach is in [`design.md`](design.md); the core interaction
> decision is recorded in
> [ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md).
> Kept below for the reasoning trail. (Grilled via free-text + flow
> diagrams, one fork at a time — as the user prefers for conceptual work.)
>
> - **Q1** interaction model → **B1 hold-and-inject** (not notify-only, not escape-and-re-prompt)
> - **Q2** no-answer policy → **park forever, no auto-decide**; new `GATED` state
> - **Q5 / round-trip** → **reuse the approval row + new in-memory doorbell** (W1)
> - **Q3** gates → **all three**: plan → `AskUserQuestion` → permission prompt
> - **Q4** surfaces → **Telegram + web** (free via the reused row)
> - **Q6** detection → **inside `TranscriptReader`**, handling in `PtySession`

## Q1 — Core interaction model (PAUSED HERE, unanswered)

When a maestro hits a gate, what does Hive do?

- **(A) Notify-only / passive.** Read the plan from the transcript,
  push it to the user, then auto-proceed (inject "1"). User just
  watches; turn never actually waits.
- **(B) Approve round-trip / active.** Push the plan, **block the
  turn**, wait for the user's approve/deny, then inject "1"/"2".
  Mirrors Hive's existing `/approve` flow. Needs a timeout policy
  (auto-approve / auto-deny / park).
- **(Hybrid)** e.g. notify-only for `AskUserQuestion`, full
  round-trip for plan approval.

User's stated intent: *"I want to interact or even know that it is
planning"* — leans (B) but accepts (A). **Get a firm answer before
designing.**

## Q2 — Timeout behaviour (only if B/hybrid)

A blocked turn can't wait forever (the 180s reader timeout, plan-quota
windows, idle-kill). On no user response within N minutes: auto-approve,
auto-deny, or park the Entity in a "waiting" state and notify? What is N?

## Q3 — Which gates, and in what order?

Plan-mode approval and `AskUserQuestion` are confirmed. Are permission
prompts in scope, or fully handled by `yolo`/`bypassPermissions`
already? Recommend shipping plan-approval first, `AskUserQuestion`
second.

## Q4 — Surface(s)

Telegram only, or web dashboard too (reusing the mode-request approval
UI)? The existing approval pattern already spans both surfaces.

## Q5 — Reuse vs. new mechanism

Confirm the plan-approval should be modelled as a pending-approval row
reusing `request_mode_change` / vault-action machinery (see
`research.md` → "Existing pattern to reuse"), vs. a bespoke mechanism
inside the PTY adapter.

## Q6 — Detection placement

Where does gate-detection live? Options: inside
`TranscriptReader.await_next_assistant_turn` (it already tails the
file), or a separate watcher the adapter consults. Affects how the
blocking/un-blocking is wired.

## Open external input

User referenced **cortexOS** as having solved a similar problem twice
but hasn't shared it yet. Ask for the repo/path or a paste; fold its
approach into the design before finalizing.
