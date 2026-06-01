# Research — Vault consolidation

Investigated 2026-05-30 by an Explore agent during Phase 2 scoping.
All file references verified against `main` at that date.

## What is "the Vault"?

The Vault is three things at once:

1. **A security-gated Entity class** — `src/hive/models/vault.py`
   defines `Vault(Entity)`. From its docstring:
   > Security-critical entity with locked-down permissions. Cannot
   > run Bash, Write, or Edit. Cannot be killed by non-user actors.
   > All actions require user approval via Telegram.
2. **A persistent approval flow** — `VaultStore` in
   `src/hive/bus/vault_store.py` provides CRUD over the
   `vault_actions` table (migrations `009_vault_actions.sql` and
   `022_vault_actions_payment_fields.sql`).
3. **A payment subsystem** — `src/hive/vault/` contains the
   `PaymentProvider` protocol, a `StubPaymentProvider`, and
   `check_caps()` for per-currency daily/monthly cap enforcement.

## Where it lives now (8 layers)

| Layer | Files | Role |
|---|---|---|
| Entity class | `src/hive/models/vault.py` | `Vault(Entity)` dataclass |
| Storage | `src/hive/bus/vault_store.py`, `bus/migrations/009_*.sql`, `bus/migrations/022_*.sql` | CRUD + schema |
| Payment | `src/hive/vault/provider.py`, `vault/spend_caps.py`, `vault/__init__.py` | Provider protocol, stub, cap logic |
| Config | `src/hive/config.py` lines 216–239 | 5 env vars (`VAULT_ENABLED`, `VAULT_PROVIDER`, `VAULT_DAILY_CAP_CENTS`, …) |
| Orchestration | `src/hive/process/manager.py` ~lines 1680–1940 | Holds `vault_store`, `payment_provider`, cap constants; implements `request_payment`, `approve_vault_action`, `deny_vault_action` |
| Telegram | `src/hive/commands/dispatch.py` lines 904–969, `telegram/commands.py`, `telegram/help_text.py` | `/vault approve|deny|status|log` |
| Web | `src/hive/web/app.py` lines 336–368, `web/view_model.py`, `web/templates/_partials/vault.html` | HTTP endpoints + UI |
| Bootstrap | `src/hive/__main__.py` lines 46–50, 162–276 | Builds VaultStore + provider, wires into ProcessManager, registers default `vault` entity |

## What "consolidate" implies here

Five concrete improvements, each addressable separately:

1. **Group Vault config.** Move the 5 env vars to a `vault.config`
   submodule or a `VaultConfig` dataclass — separate from unrelated
   config.
2. **Co-locate the approval flow.** Place `VaultStore`, the Entity
   class, and command wiring under one boundary.
3. **Cut the cross-layer loop.** Today
   `process/manager.py` → `vault/spend_caps.py` → `bus/vault_store.py`
   forms a 3-layer chain reading the same table. Refactor so one
   module owns the table interface; spend caps consult it.
4. **Extract from `ProcessManager`.** Move `request_payment`,
   `approve_vault_action`, `deny_vault_action`, and the cap constants
   into a `VaultOrchestrator`. `ProcessManager` calls it.
5. **Align with `mode_requests`.** The mode-request flow is the
   cleaner sibling pattern; Vault should converge on the same shape
   for consistency and to halve the surface area future contributors
   need to learn.

## Coupling

**Readers:**

- `ProcessManager` — direct; `vault_store.create_action`, `get`,
  `approve`, `deny`, `mark_executed`, `mark_failed`.
- `CommandDispatcher` and web endpoints — `vault_store.pending()`,
  `vault_store.log()`.
- `spend_caps.check_caps()` — calls
  `vault_store.spend_total_cents()` to enforce caps.

**Writers:**

- Only `ProcessManager.request_payment()` creates actions, gated by
  `isinstance(entity, Vault)`.
- Only `ProcessManager.approve_vault_action()` /
  `deny_vault_action()` mutate.

**Circular risk:** the `process/manager.py` ↔ `vault/spend_caps.py` ↔
`bus/vault_store.py` chain crosses three layers. Today's import order
works, but any new caller hitting the table directly is a future hazard.

## Existing tests (full coverage — safe to refactor under)

- `tests/test_vault.py` — Entity class
- `tests/test_vault_store.py` — CRUD
- `tests/test_vault_provider.py` — StubPaymentProvider behaviour
- `tests/test_vault_spend_caps.py` — cap enforcement
- `tests/test_vault_payment_request.py` — request flow
- `tests/test_vault_approval_flow.py` — full approval lifecycle
- `tests/test_web_vault_endpoints.py` — HTTP endpoints

## Recommended staging (design-stage input)

1. Introduce `VaultConfig` dataclass; migrate config readers.
2. Introduce `VaultOrchestrator`; move `request_payment` first
   (smallest, well-tested).
3. Migrate `approve_vault_action` / `deny_vault_action`.
4. Refactor `spend_caps.check_caps()` to consult the orchestrator,
   not the store directly. Loop broken at this step.
5. Update `commands/dispatch.py`, `telegram/bridge.py`, `web/app.py`
   to call the orchestrator boundary.

Each step lands as its own PR; tests stay green between them.

## Open question for design stage

Should `Vault` Entity, `VaultOrchestrator`, `VaultConfig`, and
`VaultStore` live together under `src/hive/vault/`? Today the Entity
class lives in `models/` and the store in `bus/`. Moving them is
mechanical but cosmetic — design stage should decide based on the
project's general placement rules (models vs. domain-package layout).
