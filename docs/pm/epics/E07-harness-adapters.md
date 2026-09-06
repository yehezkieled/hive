---
id: E07
title: Harness adapters
milestone: M3
status: todo
issue: 290
---
## Goal
Entities run on Codex and OpenCode through the Adapter interface (ADR 0001), with the /model command harness-aware and billing-tagged, and automatic quota failover once both adapters exist.

## Tickets
- T015 todo P1 Codex adapter
- T016 todo P2 OpenCode adapter + quota failover

## Flow
<!-- drawn by pm flow, do not edit by hand -->
T015 -> T016
Ready now: none
To grill: T015
Blocked: T016 (waits on T015)

