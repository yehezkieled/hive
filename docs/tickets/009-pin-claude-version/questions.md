# Questions — Ticket 009

The unknowns going in, resolved during the design grill (2026-06-04 → 06-07).
Kept as the record of what was open at the start.

## Mechanism
- **How does the fleet resolve the `claude` binary today, and where?**
  → `research.md`.
- **Make resolution explicit via a config absolute path, or a systemd PATH
  fix?** → `design.md` — chose the **config absolute path** (the acceptance
  says "not via an ambiguous PATH lookup").

## Version policy
- **Freeze an exact version, or track-latest** (dev + fleet share the
  self-updating native install)? → `design.md` — chose **track-latest**.
- **Does the native install's self-pruning make a frozen `versions/X` path
  fragile?** → `research.md` — yes; a real freeze would mean npm, not a native
  version file. This tipped the policy toward track-latest.

## Version logging
- **Read the version once at startup (consistent, restart-granularity) or every
  spawn (instant follow)?** → `design.md` — chose **every spawn**.
- **How to read it cheaply without a `claude --version` subprocess per spawn?**
  → `design.md` — resolve the symlink target path; `claude --version` fallback.

## Scope
- **Does the fix also cover the Advisor's second `claude -p` spawn?** → No —
  spun out as Ticket [013](../013-retire-custom-advisor/) (retire the custom
  advisor for CC's native `/advisor`), which deletes that spawn site entirely.
  Out of 009's scope.
- **Keep a systemd PATH safety net?** → Dropped. With the advisor leaving via
  013, the harness is the only `claude` Hive spawns, and it uses the absolute
  path — there is no nested PATH lookup left to protect. 009 therefore does not
  touch the `.service` unit at all.
