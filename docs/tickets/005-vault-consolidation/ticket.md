# 005 — Consolidate the Vault

## What

Co-locate Vault logic that currently spans 8 layers of the codebase.
Cut the three-layer cross-import loop between `process/manager.py`,
`vault/spend_caps.py`, and `bus/vault_store.py`. Extract Vault-specific
methods from `ProcessManager` into a `VaultOrchestrator` (or
equivalent) so the main process module no longer owns payment-policy
knowledge.

Zero behaviour change. All existing Vault tests pass unmodified.

## Why

The Vault is a security-gated payment Entity — approvals route through
Telegram and the web dashboard, spend caps gate every action, and the
flow persists in `vault_actions`. Today its logic is scattered
horizontally across config, models, bus, vault, process, commands,
web, and `__main__`. That spread:

- makes the trust boundary hard to read at a glance,
- forces every change to touch 3+ modules,
- creates a cross-layer import loop (`process/manager.py` ↔
  `vault/spend_caps.py` ↔ `bus/vault_store.py`) that constrains
  future refactors,
- duplicates wiring across `commands/dispatch.py`,
  `telegram/bridge.py`, and `web/app.py`.

A consolidated boundary is also the natural prep work for two
roadmap items: the planned spend-cap policy rework, and bringing the
Vault into the same shape as `mode_requests` (already the cleaner
sibling pattern).

## Acceptance

- All Vault config lives under one section / submodule, not
  interleaved with unrelated config in `config.py`.
- One module owns the read/write interface to the `vault_actions`
  table. Spend caps consult that module, not the store directly.
- `ProcessManager` calls a `VaultOrchestrator` for `request_payment`,
  `approve_vault_action`, `deny_vault_action` — it does not implement
  them.
- Three-layer import loop is gone (verified by import graph or `pydeps`).
- Wiring in `commands/dispatch.py`, `telegram/bridge.py`, and
  `web/app.py` calls the consolidated boundary, not `vault_store`
  directly.
- All 7 existing Vault test files pass unmodified. Any new module
  gets its own focused tests.
- A full vault approval round-trip works end-to-end on Telegram +
  web (smoke test, not test-suite-only).

## Sprint

Drafted during Phase 2 scoping; **deferred to Sprint 2026-Q2-S4** to
give Ticket 004 room in S3. Research captured (see `research.md`);
`design.md`, `outline.md`, `plan.md` get authored when the ticket is
grabbed.
