---
id: T017
title: Remove typed /approve /deny /vault once needs-you buttons exist
epic: E03
milestone: M1
status: todo
priority: P2
depends_on: [T004]
owner:
auto: no
plan: required
ready: no
issue: 293
pr:
---
## What
Remove `/approve` `/deny` `/vault` as typed commands, replaced by the needs-you lane's card + inline buttons built in T004. Backend untouched — the buttons call the same vault/approval/mode-request logic; only the typed surface is retired. Split out of T007 (item 4 of the 064 command-surface work) because it is hard-gated on T004's button surface, which is not built yet, whereas T007's other items are independent.

## Why
The typed approve/deny/vault commands become first-class UI in the redesign; keeping them as typed commands after the buttons ship is a redundant, soon-confusing surface. Removing them early (before the buttons) would break the only way to approve a payment, so this waits on T004.

## Acceptance
- [ ] `/approve` `/deny` `/vault` are removed as typed commands only after T004's button surface exists; the vault backend and money-approval rail are unchanged; a payment is approvable end to end at all times (buttons); the /help drift test passes.

## Subtasks
- [ ] (first step)

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes

## Proposed changes
