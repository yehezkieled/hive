# Questions — Vault consolidation (config)

The unknowns going into the design, after the 2026-06-04 re-scope shrank
this ticket to the config consolidation only. Answered during the
code-grounded pass on the same date (see `research.md`).

1. **What form should the consolidated config take?**
   A `VaultConfig` dataclass, a flat `vault/config.py` constants module,
   or a section? Does the codebase have a precedent to follow?

2. **Who actually reads the five vars?** The original acceptance named
   "`__main__` wiring, spend caps" — is `spend_caps` really a reader, or
   does it receive caps another way (post-004)?

3. **Does anything outside `src/hive/` read them** — tests, MCP servers,
   the web layer? If a test imports the constants, "tests pass
   unmodified" is at risk.

4. **Where under `src/hive/vault/`** should the config live, and does
   `vault/__init__.py` need to re-export it?

5. **Env-load ordering.** `from_env()` reads `os.environ` — is `.env`
   guaranteed loaded at the point the reader calls it, or does the new
   module need its own `load_dotenv()`?

6. **Side-effect docs.** Does this change warrant a `CONTEXT.md` glossary
   term or an ADR?

7. **Lane.** Is this one PR (direct) or does it slice into multiple
   shippable PRs (fan-out)?
