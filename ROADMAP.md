# Hive Roadmap

Hive is live — it runs in production on the VPS and you drive it from Telegram.
This roadmap is about getting it onto a sustainable footing and finishing it as
a product.

**Phase 1 is deadline-bound and gets its own task-level plan.** Phases 2+ are
deliberately high-level — each gets a detailed plan when its turn comes, not
before; details written too early go stale.

See `CONTEXT.md` for terminology and `docs/adr/` for the decisions behind this
plan.

## Phase 1 — Runtime migration + foundational restructure

**Deadline: 2026-06-15** — Anthropic moves headless `claude -p` to API billing.
**Goal:** Hive runs plan-billed again, on a harness-agnostic foundation.

- Introduce the turn-shaped `Runtime` adapter interface; carve a `runtime/`
  package.
- Claude adapter step 1 — wraps the existing `claude -p` path; behaviour-neutral,
  the test suite stays green.
- Claude adapter step 2 — swap internals to a persistent interactive PTY
  session; this is the billing fix.
- Plan-quota monitoring + Telegram notification on exhaustion.
- `/runtime <entity> <harness> [model]` command (Claude only for now).
- Restructure the migration forces: Claude-specifics quarantined in the Claude
  adapter; persistence stores moved out of `bus/`.
- Docs the migration needs: an accurate `README.md`, a runtime architecture
  doc. (`CONTEXT.md` + ADR 0001 already done.)

## Phase 2 — Complete the restructure and documentation

**Goal:** Hive is cleanly structured and properly documented — maintainable,
easy for both you and AI agents to navigate.

- Break up `manager.py` (2237-line god object) into focused modules.
- Consolidate the Vault — today it is spread across `bus/`, `models/`,
  `vault/`, and `commands/`.
- Resolve naming drift (`WorkerAgent` -> `Worker`).
- Dismantle `PROJECT_PLAN.md` (3242 lines): build history -> `CHANGELOG.md`;
  architecture -> the architecture doc; anything forward-looking -> this
  roadmap.
- Full documentation set finalised.

## Phase 3 — Web dashboard to PWA

**Goal:** control Hive from your phone, off Telegram.

- Finish the existing web dashboard (the `web/` package).
- Make it a responsive **PWA** — installable to the phone home screen,
  full-screen, push notifications.
- This replaces a native mobile app: one codebase, no app store, no second
  release cycle.

## Phase 4 — Codex and OpenCode adapters

**Goal:** vendor independence — the ability to pivot the fleet off Claude.

- Build the `codex` adapter (ChatGPT/Codex plan).
- Build the `opencode` adapter (provider-agnostic — cheap models such as GLM).
- Automatic quota-failover (needs the adapters to exist first).
- Closes the deferred multi-LLM feature from `docs/AUDIT_2026-05-05.md`.

## Phase 5 — Features

To be filled with your feature ideas. Seed recommendations:

- A plan-quota widget on the dashboard — the key metric now that cost is
  quota-based, not dollar-based.
- A harness view — which harness each entity runs on, and each plan's
  remaining quota.
- The 8 deferred spec features in `docs/AUDIT_2026-05-05.md` section 7 —
  review and pick any worth doing.
- _(your ideas here)_

## Priority note

Phases 3 and 4 can swap. Phase 3 (PWA) is daily-use value; Phase 4 (extra
harnesses) is insurance you may not need unless Claude quota becomes a real
ceiling. That ordering is your call.
