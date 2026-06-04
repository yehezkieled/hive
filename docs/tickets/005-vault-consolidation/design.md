# Design — Vault config consolidation

## Chosen approach

A `VaultConfig` frozen dataclass with a `from_env()` classmethod, living
in a new `src/hive/vault/config.py`. The five env reads move out of
`hive.config` and into `from_env()`. `__main__.py` builds one
`VaultConfig` and reads its fields.

```
# src/hive/vault/config.py  (new)
from __future__ import annotations
from dataclasses import dataclass
from dotenv import load_dotenv
import os


@dataclass(frozen=True)
class VaultConfig:
    """Configuration surface for the security-gated Vault payment boundary.

    One object so the trust boundary's inputs read in one place.
    """

    enabled: bool
    provider: str
    daily_cap_cents: int
    monthly_cap_cents: int
    cap_currencies: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "VaultConfig":
        load_dotenv()  # idempotent; self-contained, no hive.config dep
        return cls(
            enabled=os.environ.get("HIVE_VAULT_ENABLED", "false").lower() == "true",
            provider=os.environ.get("HIVE_VAULT_PROVIDER", "stub"),
            daily_cap_cents=int(os.environ.get("HIVE_VAULT_DAILY_CAP_CENTS", "5000")),
            monthly_cap_cents=int(os.environ.get("HIVE_VAULT_MONTHLY_CAP_CENTS", "50000")),
            cap_currencies=tuple(
                sorted(
                    {
                        c.strip().upper()
                        for c in os.environ.get("HIVE_VAULT_CAP_CURRENCIES", "AUD,USD").split(",")
                        if c.strip()
                    }
                )
            ),
        )
```

The parsing logic is moved **verbatim** from `config.py` — same env-var
names, same defaults, same currency normalisation — so behaviour is
identical.

### Reader change (`__main__.py`)

```
# before:  from hive.config import (VAULT_CAP_CURRENCIES, VAULT_DAILY_CAP_CENTS,
#                                   VAULT_ENABLED, VAULT_MONTHLY_CAP_CENTS, VAULT_PROVIDER)
# after:
from hive.vault.config import VaultConfig
...
vault_cfg = VaultConfig.from_env()
...
payment_provider = build_provider(vault_cfg.provider) if vault_cfg.enabled else None
process_manager = ProcessManager(
    ...
    vault_daily_cap_cents=vault_cfg.daily_cap_cents,
    vault_monthly_cap_cents=vault_cfg.monthly_cap_cents,
    vault_cap_currencies=vault_cfg.cap_currencies,
    ...
)
...
if vault_cfg.enabled and "vault" not in process_manager.entities:   # :280
    ...
    logger.info("Registered default vault entity (provider=%s)", vault_cfg.provider)  # :291
```

`ProcessManager`'s signature is **unchanged** — it still takes the three
cap kwargs individually. Passing the whole `VaultConfig` into
`ProcessManager` is deliberately out of scope (that would be an API
change, not a config consolidation).

### Exposure

`__main__` imports from `hive.vault.config` directly. Re-exporting
`VaultConfig` from `vault/__init__.py` is **not** done — `__init__`
currently exports the payment API (`provider`, `spend_caps`); adding
config there mixes concerns for no caller benefit (there is one reader).

## Alternative considered — flat constants module (rejected)

Move the five `NAME = os.environ.get(...)` lines verbatim into
`vault/config.py` and keep `from hive.vault.config import VAULT_ENABLED,
…`. Smallest diff, matches the dominant flat-import style.

**Rejected because:** it relocates the scatter rather than consolidating
it — there's still no single named object for "the Vault's config
surface." The ticket's *why* is that the money-spending boundary's
config should read as one obvious thing; a `VaultConfig` object delivers
that and matches the codebase's existing "group settings into a
dataclass" precedent (`ClaudeAdapterConfig`, `PersonalityConfig`). The
extra cost is three lines in `__main__` (`from_env()` + field access).

## Behaviour & compatibility

- **Zero behaviour change.** Same env vars, defaults, parsing. The only
  observable difference is the Python import path of the values.
- **Env-var contract unchanged.** `HIVE_VAULT_*` names are untouched, so
  `.env`, deploy config, and the `spend_caps` error message stay valid.
- **No test changes.** No test imports the old constants.

## Side-effect docs

None. No new glossary term (Vault is not being redefined); not ADR-scale
(zero-behaviour relocation). `CONTEXT.md` and `docs/adr/` untouched.
