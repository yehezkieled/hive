# Hive Roadmap

Vision and themes over months. Concrete work lives in
`docs/sprints/` (current 2-week window) and `docs/tickets/`
(individual work units). When a phase completes, mark it done and
trim the section.

See [`../CONTEXT.md`](../CONTEXT.md) for terminology and
[`adr/`](adr/) for the decisions behind these phases.

## Phase 1 — Runtime migration  ·  ✅ DONE 2026-06-01

Hive runs plan-billed on a harness-agnostic foundation — the PTY
harness is deployed and live (Tickets 001 + 003). The 2026-06-15
cutoff that moves headless `claude -p` to API billing no longer
threatens Hive; retiring the leftover headless path is Phase 2 cleanup
(Ticket 007).

## Phase 2 — Restructure  ·  ✅ DONE 2026-06-09

Hive is cleanly structured and documented: the `manager.py` god object
is a facade + collaborators (004), the Vault is consolidated (005),
naming drift is resolved (`WorkerAgent` → `Worker`, 006), and the
headless runtime is gone — PTY-only (007). Sprint S4 hardened the live
fleet — tracked async tasks (008), pinned Claude version (009),
repaired integration test (010), CI coverage floor (011), and per-role
skill curation (012). Phase 3 (PWA) opens next.

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
