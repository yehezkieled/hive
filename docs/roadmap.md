# Hive Roadmap

Vision and themes over months. Concrete work lives in
`docs/sprints/` (current 2-week window) and `docs/tickets/`
(individual work units). When a phase completes, mark it done and
trim the section.

See [`../CONTEXT.md`](../CONTEXT.md) for terminology and
[`adr/`](adr/) for the decisions behind these phases.

## Phase 1 — Runtime migration  ·  IN PROGRESS · deadline 2026-06-15

Hive runs plan-billed again on a harness-agnostic foundation. On
2026-06-15 Anthropic moves headless `claude -p` to API billing; the
PTY harness keeps Hive on the Claude Max plan.

## Phase 2 — Restructure  ·  NEXT

Hive is cleanly structured and properly documented — maintainable,
easy for humans and agents to navigate. Targets: break up
`manager.py` (god object), consolidate the Vault, resolve naming
drift (`WorkerAgent` → `Worker`).

## Phase 3 — Web dashboard to PWA

Control Hive from a phone, off Telegram. The existing dashboard
becomes a responsive installable PWA — one codebase, no app store.

## Phase 4 — Codex + OpenCode adapters

Vendor independence — the ability to pivot the fleet off Claude.
Build the `codex` adapter (ChatGPT/Codex plan) and the `opencode`
adapter (provider-agnostic, cheap models such as GLM). Automatic
quota-failover once both adapters exist.

## Phase 5 — Features (ideas)

Each becomes a Ticket when its time comes; until then, just a
one-line bullet here.

- Plan-quota widget on the dashboard.
- Harness view — which Entity runs on which Harness, with each
  plan's remaining quota.
- Quota-aware planning — Maestros treat plan-quota as a shared,
  finite budget; quota becomes a planning input, not a wall it hits.
- The 8 deferred spec features in
  [`archive/AUDIT_2026-05-05.md`](archive/AUDIT_2026-05-05.md) § 7 —
  review and pick any worth doing.

## Priority note

Phases 3 and 4 can swap. Phase 3 (PWA) is daily-use value; Phase 4
(extra harnesses) is insurance against Claude quota becoming a real
ceiling. That ordering is a judgement call.
