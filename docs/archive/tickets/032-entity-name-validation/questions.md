# Questions — Ticket 032: Validate entity/team names

The unknowns going in. All resolved by `research.md` / `design.md`.

1. **Where does a new, user-supplied name actually enter the system?**
   → research §1. Two chokepoints: `register_maestro` (maestro names) and
   `create_team` (team names) in `lifecycle_manager.py`. Every command and
   hive_actions path funnels through them.
2. **Where do raw names become filesystem paths / git refs / addresses?**
   → research §2. Worktree dir, git branch, the `maestro.team` address, and the
   CC transcript slug (the last already handled by Ticket 030).
3. **Is there any name validation today?** → research §6. None.
4. **Reject bad names, or silently normalize them?** → design D1. Reject
   (fail loud) — a name is also an address, so silent rewrites cause collisions
   and identity drift.
5. **Exact allowlist — is `.` allowed inside a name?** → design D2.
   `[A-Za-z0-9_-]` per component; **no dot** — the dot is the `maestro.team`
   address separator Hive parses by splitting (research §3).
6. **Do the rejection errors already reach a human?** → research §4. The two
   human paths (`/team create`, `/new maestro`) surface a `ValueError` as-is;
   the maestro `spawn_team` path is **log-only** (the gap).
7. **Scope: also close the maestro-feedback gap?** → design D3. Yes
   (minimal + feedback), via the existing `_handle_parse_errors` channel.
8. **Does any creation path bypass the two chokepoints?** → research §1. No —
   every other `_entities[...] =` site re-registers an *existing* entity.
