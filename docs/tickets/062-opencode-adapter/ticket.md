# 062 — OpenCode adapter

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 7.
> Enables automatic quota-failover once both adapters exist (with 061).

## What

An `opencode` **Harness adapter** (provider-agnostic, cheap models such as GLM)
behind Hive's Adapter interface. With the Codex adapter (061) in place, add
automatic **quota-failover** — move a stalled-on-quota Entity to a harness with
headroom.

## Why

Cheap tokens for grunt work (real cost relief) + a failover path when Claude plan
quota is exhausted mid-build.

## Acceptance

TBD at grilling.
