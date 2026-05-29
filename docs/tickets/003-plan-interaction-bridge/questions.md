# Questions — design forks to resolve (brainstorm in progress)

The brainstorm started but paused at Q1. Resume here. The user
prefers **free-text questions + flow diagrams**, one at a time — not
rapid AskUserQuestion option-picking — for conceptual design work.

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
