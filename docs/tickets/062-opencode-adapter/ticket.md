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

## Note — extend the `/model` command (carry-in from the S10 command audit)

`/model` hard-codes a Claude-only valid-model set (`{opus, sonnet, haiku,
opusplan}`, `src/hive/commands/dispatch.py:792`) with **no billing guard**. When
this adapter lands, its grilling must **extend `/model`'s valid set** with the
provider-agnostic models it drives (GLM, etc.), **tag each plan- vs API-billed**
(most opencode providers are API-billed — real money — so the warning matters
most here), and keep the set **harness-aware** so a model is only offered on the
entity whose harness can run it. Pairs with the same note in
[061](../061-codex-adapter/ticket.md).
