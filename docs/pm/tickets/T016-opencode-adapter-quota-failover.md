---
id: T016
title: OpenCode adapter + quota failover
epic: E07
milestone: M3
status: todo
priority: P2
depends_on: [T015]
owner:
auto: no
plan: required
ready: no
issue: 283
pr:
---
## What
An `opencode` Harness adapter (provider-agnostic, cheap models such as GLM) behind Hive's Adapter interface. With the Codex adapter (T015) in place, add automatic quota failover: move an entity stalled on quota to a harness with headroom. Extend `/model` with the provider-agnostic models, billing-tagged (most OpenCode providers are API-billed, so the warning matters most here) and harness-aware.

## Why
Cheap tokens for grunt work (real cost relief) plus a failover path when Claude plan quota is exhausted mid-build.

## Acceptance
- [ ] An entity on the `opencode` harness completes a turn end to end on the deployed host.
- [ ] An entity that hits a quota wall is moved to a harness with headroom and continues its conversation.
- [ ] `/model` offers OpenCode models only on OpenCode entities and warns on API-billed choices.

## Subtasks
- [ ] OpenCode adapter
- [ ] Quota failover
- [ ] `/model` extension

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/062-opencode-adapter/`. Acceptance is a first draft; settle at /pm:grill.

## Proposed changes
