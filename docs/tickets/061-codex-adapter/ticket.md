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
