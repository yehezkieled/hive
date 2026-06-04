# 005 — Consolidate the Vault (config submodule)

## What

Gather the Vault's configuration into a single `vault/` submodule. The
five Vault config vars currently sit interleaved with unrelated config
in `config.py` (~lines 206–234); move them behind one `VaultConfig`
(or equivalent) under `src/hive/vault/`, so the security-gated payment
boundary's configuration reads in one place.

Zero behaviour change. All existing Vault tests pass unmodified.

> **Re-scoped 2026-06-04.** This ticket originally aimed to extract the
> Vault payment methods from `ProcessManager` into a `VaultOrchestrator`
> and cut the `manager.py ↔ spend_caps.py ↔ vault_store.py` import loop.
> **Ticket 004 already delivered both** as a side effect of the
> `manager.py` breakup: `request_payment` / `approve_vault_action` /
> `deny_vault_action` now live in `process/approval_handler.py` (the
> facade delegates), and `spend_caps.check_caps()` is a pure function
> taking `vault_store` as an argument, so the loop is a one-way path,
> not a cycle. A separate `VaultOrchestrator` would now violate
> [ADR 0006](../../adr/0006-god-object-breakup-composition.md)
> (collaborators don't call each other). So this ticket shrinks to the
> one genuine remainder — the scattered config. File co-location of
> `models/vault.py` + `bus/vault_store.py` is dropped as not worth it.

## Why

The Vault is a security-gated payment Entity. Its config being
interleaved with unrelated settings in `config.py` makes the trust
boundary harder to read than it should be — for the one part of Hive
that spends real money, the configuration surface should be obvious and
in one place.

## Acceptance

- The five Vault config vars (`VAULT_ENABLED`, `VAULT_CAP_CURRENCIES`,
  `VAULT_DAILY_CAP_CENTS`, `VAULT_MONTHLY_CAP_CENTS`, `VAULT_PROVIDER`)
  live behind one Vault config module/section under `src/hive/vault/`,
  not interleaved in `config.py`.
- Readers (`__main__` wiring, spend caps) consume the consolidated
  config, not the scattered vars.
- All 7 existing Vault test files pass unmodified.
- `ruff check` + `ruff format --check` + full `pytest -m "not integration"`
  green.

## Sprint

Committed to Sprint **2026-Q2-S4** as the Phase 2 close-out item.
Research captured (see `research.md`, written pre-004 — the re-scope
note above supersedes its `VaultOrchestrator` framing).
