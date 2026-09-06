# 061 — Codex adapter

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 7.

## What

A `codex` **Harness adapter** (ChatGPT/Codex plan) behind Hive's Adapter
interface — driving the Codex CLI at turn level like the Claude Code adapter.
Forces the `Entity` → `PersonalityLoader` / `CliArgsBuilder` split.

## Why

Vendor independence (pivot the fleet off Claude) + **more plan-quota headroom**
for long builds — a second plan-billed harness to spread load / fail over to.

## Acceptance

TBD at grilling. (See ADR 0001 — harness-agnostic runtime.)

## Note — extend the `/model` command (carry-in from the S10 command audit)

`/model` hard-codes a Claude-only valid-model set (`{opus, sonnet, haiku,
opusplan}`, `src/hive/commands/dispatch.py:792`) and spawns `--model <x>` with
**no billing guard**. When this adapter lands, its grilling must:

- **Extend `/model`'s valid set** with the Codex/ChatGPT models this harness
  drives (they only make sense once the adapter routes them).
- **Tag each model plan- vs API-billed** and warn on selecting an API-billed one
  — the audit found the command sets a model with zero cost signal (relevant
  now that `fable`, API-billed, is being added).
- Keep the valid set **harness-aware** so a Claude model is rejected on a Codex
  entity and vice-versa.
