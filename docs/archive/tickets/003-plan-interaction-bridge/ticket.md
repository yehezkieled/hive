# 003 — Interactive-gate bridge for the PTY harness

> **Discovered during:** Ticket 001 deploy testing (Sprint
> `2026-Q2-S1`). The PTY smoke test (`/m:dev`) surfaced this hang.
> Lineage only — 003 is **new scope**, committed to Sprint
> `2026-Q2-S2`, not part of S1's commitment.

## What

When an Entity running on the PTY (Claude Code) Harness hits an
**interactive gate** — plan-mode approval (`ExitPlanMode`), an
`AskUserQuestion` call, or a permission prompt — the harness has no
handler for it. The Turn blocks on the TUI menu until
`TranscriptReader.await_next_assistant_turn` times out at 180s, then
returns an error / empty reply.

Build a bridge that **detects the gate from the transcript** (not
screen-scraping), **surfaces it to the user** (Telegram + web), and
**routes the user's decision back** into the PTY by injecting the
keypress into the live Turn (hold-and-inject — see
[ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md)).

## Why

The PTY harness is the plan-billed runtime Hive depends on after the
2026-06-15 headless-billing cutoff (Phase 1). Under headless
`claude -p`, plan mode printed the plan and exited — the Turn
completed. The interactive PTY instead sits on the approval menu
forever. Result: any Entity in `plan` mode, or any Entity that calls
`AskUserQuestion` mid-Turn, silently hangs for 180s and fails.

The user wants maestros to **keep the ability to plan** and to have
**visibility + interaction** when they do — not a silent hang, and not
plan mode disabled outright. A maestro that just planned holds rich
live context (why this plan, the first step, which Workers to spawn);
the fix must let it **continue that same Turn** on approval rather than
discard and re-plan.

## Scope

- Detect interactive gates from the session `.jsonl` transcript.
- Surface the plan / question / permission request to the user
  (Telegram + web), reusing the existing pending-approval row pattern.
- Route the user's approve / deny / answer back into the *live* Turn
  by injecting the keypress (hold-and-inject), waking the blocked Turn
  via an in-memory doorbell (`asyncio.Event`).
- Gates in scope, in shipping order:
  1. **plan-mode approval** (`ExitPlanMode`) — transcript shape verified.
  2. **`AskUserQuestion`** — transcript shape verified.
  3. **permission prompt** — transcript shape **unverified**; must be
     captured and confirmed structurally detectable before its
     detector ships.

## Immediate stopgap (already applied — not this ticket's deliverable)

`dev` maestro was stuck in `plan` mode and hung every Telegram turn.
Flipped to `yolo` live via `/mode yolo dev` (in-memory + persisted to
postgres) on 2026-05-29. This only removes the plan-mode **spawn
flag** — a `yolo` maestro that calls `AskUserQuestion` will still
hang. The bridge is the real fix.

## Non-goals

- No auto-decide — Hive never auto-approves or auto-denies on a timer.
  A gated Entity parks indefinitely and is only re-pinged.
- Disabling plan mode for Entities (the user explicitly wants
  planning kept).
- Screen-scraping the TUI (transcript is the source of truth — see
  ADR 0001 / commit `88110b5`).
- A general human-in-the-loop framework beyond these three named gates.

## Acceptance

- A maestro in `plan` mode completes a Telegram turn without hanging:
  the plan reaches the user, and on approval the **same Turn**
  continues to execution; with no answer it parks cleanly (no 180s
  failure, exempt from idle-kill).
- An `AskUserQuestion` mid-Turn no longer hangs to the 180s timeout;
  the chosen option is injected and the Turn continues.
- The permission-prompt gate is detected from the transcript once its
  shape is captured and verified (or explicitly deferred with the
  shape documented).
- No screen-scraping introduced; detection reads the transcript.
- Tests cover gate detection and the approve/answer round-trip.

## Status

Design **complete** — see
[`design.md`](design.md) and
[ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md) for the
chosen approach (hold-and-inject, park-forever, in-memory doorbell,
all three gates). `outline.md` and `plan.md` carry the implementation
structure and the issue ledger. Ready to slice into issues.
