# Questions — Ticket 024: Project ownership & PA write-policy

Unknowns to resolve before designing. Code-discoverable ones are
answered in `research.md`; the rest are design calls settled in the
grill and recorded in `design.md`.

## Code-discoverable (→ research.md)

1. **Is there any project concept today?** Where, if anywhere, are
   "projects" represented in code/config (registry, Vault, role
   files)? Is `cwd`/root already tracked per Entity?
2. **Spawn permission seam.** Where does spawn build CC permission
   rules, and how does `tool_policy.py` inject per-path rules today
   (015, ADR 0008/0010)? Is path-scoped `Edit`/`Write` deny already
   expressible, or only tool-name allow/deny?
3. **cwd today.** Where is a Maestro's `cwd` set at spawn? Confirm the
   `cwd=None → systemd WorkingDirectory` fall-through the ticket cites.
4. **PA identity at spawn.** How is the PA Maestro detected
   (`HIVE_DEFAULT_MAESTRO=otter`)? Is that identity visible at the
   policy seam?
5. **Maestro↔project binding.** Is there any create-maestro / assign
   flow where ownership would naturally be set?
6. **023 floor reuse.** Does the live worktree floor already produce
   per-path write-scoping we can reuse rather than reinvent?
7. **Restart path.** Where is spawn config regenerated on restart, so
   the policy is restart-proof (an acceptance requirement)?
8. **Persistence options.** What does Hive already use for small
   structured state (Vault? JSON? sqlite?) a registry could ride.

## Design calls (→ grill → design.md)

- **A. Registry storage & schema.** New store vs. extend an existing
  one; schema (name → root path → owner Maestro, nullable).
- **B. Policy shape.** Express "PA reads any, edits only ownerless" as
  *deny `Edit`/`Write` under every owned root* (default-allow) or
  *allow only under ownerless roots* (default-deny)? Must be
  restart-proof and hermetically testable at the seam.
- **C. Ownership source of truth & cwd derivation.** Does cwd come
  from the registry root? What is the PA's cwd (it owns no project)?
- **D. Assignment enforcement point.** Where the "2nd Maestro
  rejected" check lives, and what raises on violation.
- **E. Lane.** One PR (direct) or vertical slices (fan-out)? Finalised
  at `plan.md` once research/design reveal the slice structure.
