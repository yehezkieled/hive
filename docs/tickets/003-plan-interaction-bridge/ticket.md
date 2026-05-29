# 003 — Interactive-gate bridge for the PTY harness

## What

When an Entity running on the PTY (Claude Code) Harness hits an
**interactive gate** — plan-mode approval (`ExitPlanMode`), an
`AskUserQuestion` call, or a permission prompt — the harness has no
handler for it. The turn blocks on the TUI menu until
`TranscriptReader.await_next_assistant_turn` times out at 180s, then
returns an error / empty reply.

Build a bridge that **detects the gate from the transcript** (not
screen-scraping), **surfaces it to the user** (Telegram / web), and
optionally **routes the user's decision back** into the PTY by
injecting the keypress.

## Why

The PTY harness is the plan-billed runtime Hive depends on after the
2026-06-15 headless-billing cutoff (Phase 1). Under headless
`claude -p`, plan mode printed the plan and exited — the turn
completed. The interactive PTY instead sits on the approval menu
forever. Result: any Entity in `plan` mode, or any Entity that calls
`AskUserQuestion` mid-turn, silently hangs for 180s and fails.

The user wants maestros to **keep the ability to plan** and to have
**visibility + interaction** when they do — not a silent hang, and not
plan mode disabled outright.

## Scope

- Detect interactive gates from the session `.jsonl` transcript.
- Surface the plan / question to the user.
- (Design-dependent) route the user's approve/deny/answer back into
  the PTY.
- Gates in scope, in priority order: **plan-mode approval**,
  **AskUserQuestion**. (Permission prompts are mostly mooted by
  `yolo`/`bypassPermissions`; confirm during design.)

## Immediate stopgap (already applied — not this ticket's deliverable)

`dev` maestro was stuck in `plan` mode and hung every Telegram turn.
Flipped to `yolo` live via `/mode yolo dev` (in-memory + persisted to
postgres) on 2026-05-29. This only removes the plan-mode **spawn
flag** — a `yolo` maestro that calls `AskUserQuestion` will still
hang. The bridge is the real fix.

## Non-goals

- Disabling plan mode for Entities (the user explicitly wants
  planning kept).
- Screen-scraping the TUI (transcript is the source of truth — see
  ADR 0001 / commit `88110b5`).
- A general human-in-the-loop framework beyond the two named gates.

## Acceptance

- A maestro in `plan` mode completes a Telegram turn without hanging:
  the plan reaches the user and (per chosen model) the turn either
  proceeds on approval or is parked cleanly.
- An `AskUserQuestion` mid-turn no longer hangs to the 180s timeout.
- No screen-scraping introduced; detection reads the transcript.
- Tests cover gate detection + the round-trip (or notify-only) path.

## Status

Design **in progress** — brainstorm started, paused at the core
interaction-model question. See `questions.md` for where to resume and
`research.md` for the full diagnosis so you don't have to re-investigate.
