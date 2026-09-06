# Outline — Vault config consolidation

Single shape (decided in `design.md`). Four steps, one PR.

1. **Add `src/hive/vault/config.py`.** Define the frozen `VaultConfig`
   dataclass (5 fields) + `from_env()` classmethod. Move the parsing
   logic verbatim from `config.py` (incl. the currency-tuple
   comprehension). `from_env()` calls `load_dotenv()` first so the
   module is self-contained.

2. **Remove the Vault block from `config.py`.** Delete lines 211–234
   (the comment block + the five constants). Leave the rest of the file
   intact; the file no longer references the Vault.

3. **Repoint the reader (`__main__.py`).**
   - Drop the five names from the `from hive.config import (...)` block.
   - Add `from hive.vault.config import VaultConfig`.
   - `vault_cfg = VaultConfig.from_env()` once, near the existing vault
     wiring (~`:189`).
   - Swap each use: `vault_cfg.enabled` / `.provider` / `.daily_cap_cents`
     / `.monthly_cap_cents` / `.cap_currencies` at `:189`, `:202–204`,
     `:280`, `:291`.

4. **Verify** (see `plan.md`): full vault test suite unmodified + ruff +
   `pytest -m "not integration"`, plus a grep proving no stale reference
   to the old `hive.config` Vault constants remains.

No `vault/__init__.py` change, no `ProcessManager` signature change, no
doc side effects.
