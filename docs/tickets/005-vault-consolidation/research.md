# Research — Vault config consolidation

Regenerated 2026-06-04 against current `main` (post-004, post-007).
Supersedes the original 2026-05-30 research, which predated Ticket 004
and framed the work around a `VaultOrchestrator` that the re-scope
dropped. This version is config-only, matching the re-scoped ticket.

All line numbers verified on this date.

## The five vars and where they live now

`src/hive/config.py` is a flat module of `NAME = os.environ.get(...)`
constants (234 lines, no settings object). `load_dotenv()` runs at
import time (`config.py:52`). The Vault block sits at the **end** of the
file, interleaved with unrelated settings:

| Line | Var | Parse |
|---|---|---|
| `config.py:222` | `VAULT_ENABLED` | `… == "true"` (bool) |
| `config.py:223–231` | `VAULT_CAP_CURRENCIES` | sorted, upper-cased, de-duped `tuple[str, ...]` from a comma list (default `AUD,USD`) |
| `config.py:232` | `VAULT_DAILY_CAP_CENTS` | `int` (default 5000) |
| `config.py:233` | `VAULT_MONTHLY_CAP_CENTS` | `int` (default 50000) |
| `config.py:234` | `VAULT_PROVIDER` | `str` (default `stub`) |

A descriptive comment block precedes them (`config.py:211–221`). The
move removes lines **211–234** from `config.py`.

## Readers — exactly one

`grep` for all five names across `src/` and `tests/`:

- **`src/hive/__main__.py` — the only real reader.** Imports all five
  (`__main__.py:46–50`) and uses them in `main()`:
  - `build_provider(VAULT_PROVIDER) if VAULT_ENABLED else None` (`:189`)
  - `ProcessManager(vault_daily_cap_cents=…, vault_monthly_cap_cents=…,
    vault_cap_currencies=…)` (`:202–204`)
  - register default `vault` entity `if VAULT_ENABLED` (`:280`),
    logging `VAULT_PROVIDER` (`:291`)
- **`src/hive/vault/spend_caps.py:65` — NOT a reader.** The only hit is
  the env-var *name* embedded in a `ValueError` message ("Add it to
  `HIVE_VAULT_CAP_CURRENCIES`…"). Post-004 `check_caps()` takes
  `daily_cap_cents`, `monthly_cap_cents`, `cap_currencies` as **function
  arguments** (`spend_caps.py:41–51`). It never reads config. → The env
  name stays as-is; no edit needed.
- **No test reads them.** No `tests/` file imports these constants
  (verified across all seven vault test files). The vault tests that
  touch `hive.vault.*` import `provider` / `spend_caps` / `check_caps`
  only.

This corrects the original ticket acceptance, which listed "spend caps"
as a reader.

## Form precedent in the codebase

- Grouped settings → **`@dataclass`** is the established pattern:
  `ClaudeAdapterConfig` (`runtime/claude_adapter.py:24`) and
  `PersonalityConfig` (`models/entity.py:57`).
- `src/hive/mcp/config.py` exists but is **not** a settings holder — it
  is a JSON-file *generator* (`generate_mcp_config`). Not a model here.
- Dominant import style elsewhere is flat `from hive.config import X`.

So both a `VaultConfig` dataclass and a flat `vault/config.py` constants
module have precedent; the dataclass matches the "group related
settings" pattern and the ticket's "one `VaultConfig`" phrasing.

## Env-load ordering

`from_env()` reads `os.environ`. Today `.env` is loaded by
`config.py:52` at import time, and `__main__.py:19` imports `hive.config`
before `main()` runs — so ordering happens to work. To remove the
hidden dependency, the new module's `from_env()` calls `load_dotenv()`
itself (idempotent, `override=False`), making `vault/config.py`
self-contained with **zero import dependency on `hive.config`**.

## Tests (safe to refactor under — all unmodified)

`tests/test_vault.py`, `test_vault_store.py`, `test_vault_provider.py`,
`test_vault_spend_caps.py`, `test_vault_payment_request.py`,
`test_vault_approval_flow.py`, `test_web_vault_endpoints.py`.

None import the config constants, so all pass unmodified. The change
adds a module and moves env reads; it does not touch the vault public
API (`provider`, `spend_caps`, `check_caps`, the `Vault` entity).

## Decided in design

- **Form:** `VaultConfig` frozen dataclass + `from_env()` classmethod,
  in `src/hive/vault/config.py`.
- **Side-effect docs:** none. "Vault" is not a glossary term being
  changed; a zero-behaviour config relocation is not ADR-scale (cf.
  ADR 0006 / 0007). CONTEXT.md and `docs/adr/` are untouched.
- **Lane:** direct — one PR, three files.
