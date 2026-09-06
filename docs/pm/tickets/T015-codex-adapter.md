---
id: T015
title: Codex adapter
epic: E07
milestone: M3
status: todo
priority: P1
depends_on: []
owner:
auto: no
plan: required
ready: no
issue: 282
pr:
---
## What
A `codex` Harness adapter (ChatGPT/Codex plan) behind Hive's Adapter interface (ADR 0001), driving the Codex CLI at turn level like the Claude Code adapter. Forces the `Entity` → `PersonalityLoader` / `CliArgsBuilder` split. Also extends `/model`: add the Codex models to the valid set, tag each model plan- vs API-billed and warn on API-billed, and make the set harness-aware so a Claude model is rejected on a Codex entity and vice versa (today `/model` hard-codes a Claude-only set with no billing guard).

## Why
Vendor independence (pivot the fleet off Claude) and more plan-quota headroom for long builds: a second plan-billed harness to spread load and fail over to.

## Acceptance
- [ ] A maestro or lead assigned to the `codex` harness completes a turn end to end on the deployed host.
- [ ] `/model` accepts Codex models on a Codex entity, rejects them on a Claude entity, and warns on API-billed choices.
- [ ] `Entity` is split into `PersonalityLoader` / `CliArgsBuilder` following ADR 0006.

## Subtasks
- [ ] Entity split
- [ ] Codex adapter + session handling
- [ ] `/model` harness-aware set

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/061-codex-adapter/`. Acceptance is a first draft; settle at /pm:grill. Read ADR 0001 and ADR 0006 first.

## Proposed changes
