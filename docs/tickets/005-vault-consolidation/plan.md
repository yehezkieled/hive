# Plan — Ticket 005: Consolidate the Vault (config submodule)

Direct lane — one branch, one PR. Move the five `HIVE_VAULT_*` env reads
out of `config.py` into a self-contained `VaultConfig` dataclass under
`src/hive/vault/`. Zero behaviour change.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/vault/config.py` | create | Frozen `VaultConfig` dataclass (`enabled`, `provider`, `daily_cap_cents`, `monthly_cap_cents`, `cap_currencies`) + `from_env()` (calls `load_dotenv()`; parsing moved verbatim from `config.py`). |
| `src/hive/config.py` | modify | Delete the Vault block, lines **211–234** (comment + five constants). |
| `src/hive/__main__.py` | modify | Drop the five Vault names from the `hive.config` import; add `from hive.vault.config import VaultConfig`; build `vault_cfg = VaultConfig.from_env()`; repoint uses at `:189`, `:202–204`, `:280`, `:291` to `vault_cfg.*`. |

No change to `vault/__init__.py`, `spend_caps.py`, `ProcessManager`, or
any test.

## Verification

Run from the worktree root:

- `grep -rn "VAULT_ENABLED\|VAULT_CAP_CURRENCIES\|VAULT_DAILY_CAP_CENTS\|VAULT_MONTHLY_CAP_CENTS\|VAULT_PROVIDER" src/` →
  expect hits **only** in `src/hive/vault/config.py` and
  `src/hive/__main__.py` (and the env-name string in `spend_caps.py:65`).
  No remaining reference in `config.py`.
- `python -c "from hive.vault.config import VaultConfig; print(VaultConfig.from_env())"` →
  prints defaults (`enabled=False`, `provider='stub'`, caps 5000/50000,
  `cap_currencies=('AUD', 'USD')`).
- `pytest tests/test_vault*.py tests/test_web_vault_endpoints.py` →
  all 7 files green, **unmodified**.
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.
- `pytest -m "not integration"` → full suite green.
- Smoke: service boots with `HIVE_VAULT_ENABLED=true` and registers the
  default vault entity (log line at `__main__.py:291`).

## Out of scope

- Passing the whole `VaultConfig` into `ProcessManager` (API change).
- Moving the `Vault` entity (`models/`) or `VaultStore` (`bus/`) — the
  re-scope dropped file co-location.
- Any `VaultOrchestrator` extraction — delivered by Ticket 004.
- Renaming env vars or changing defaults.

## Cross-cutting impact

None. No reference-doc edits (`README`, `DEPLOYMENT.md`, `ARCHITECTURE`),
no `CONTEXT.md` term, no ADR. The `HIVE_VAULT_*` env contract is
unchanged, so deploy config and `.env` need no update.

## Build

Direct lane — implement the three file ops above on a branch, run the
verification gate, open one PR. No fleet Workflow needed.
