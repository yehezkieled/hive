# 050 — Audit & trim the command set (keep the useful, cut the vestigial)

> Feeds the web redesign. Do the trim BEFORE promoting commands to UI — don't
> build UI for commands we're about to delete.

## What

Review all ~39 slash commands and classify each **keep / consolidate / cut**.
The 2026-06-30 web inventory flagged clear cut/consolidate candidates:

- `/swarm` — largely vestigial post-Worker-retirement (ADR 0013 / Ticket 018).
- `/heartbeat` — Telegram-only bridge command; not in the dispatcher at all.
- `/broadcast` — blast-to-all; rarely useful.
- `/eval`, `/budget`, `/audit`, `/comms` — debug/introspection; candidates to
  fold into an inspector or drop.
- advisor — retired (Ticket 013), no surface.

Keep the core control + read set: `status`/`org`/`m:`/`t:`/`a:`, `task`/`priority`,
`team`/`new`, `mode`/`model`/`kill`/`reset`/`compact`/`loop`, `vault`, decision
reply, git `commit`/`pr`/`merge`, `quota`, `blueprint`, `project`, `help`.

## Why

~30 of ~35 commands are "type it in chat" with no UI. Trimming to the
genuinely-useful set (a) makes the surface easier to learn, (b) cuts
maintenance, and (c) gives the web redesign a **clean, smaller set** to decide
"which become first-class UI vs stay typed vs get cut."

## Acceptance

- Every command classified keep / consolidate / cut with a one-line rationale
  (a short decision table in this ticket's design.md).
- Cut commands removed from the dispatcher (`commands/dispatch.py`) + `help_text`
  + the autocomplete list; the `/help` drift test (`tests/test_help.py`) updated.
- `CONTEXT.md` / help reflect the trimmed set.
- No behaviour change to kept commands.

## Non-goals

- Building the UI for the kept commands (that's the web redesign).
- Changing what the kept commands do.
