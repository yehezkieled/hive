# Outline — Ticket 031

Implementation structure. One vertical change across the resolver + two guidance
surfaces + tests. Direct lane, single PR.

## Touch points

```
src/hive/process/message_dispatcher.py
  _resolve_message_alias()      (~:617)   ── add self/me downward branches
                                              + one-line docstring note (downward mirror)
  _addressing_hint()            (~:670)   ── org-root branch advertises self.<team>

personalities/role-maestro.md
  spawn_team bullet             (~:98)    ── "...address it as self.<team_name> (no name needed)"

tests/  (mirrors src/ — process/)
  test for _resolve_message_alias         ── self/me resolution + passthrough + existing aliases
  test for _handle_actions message route  ── self.<team> delivers; bare self → ban; ghost → reject
```

## Step sequence

1. **Resolver** — in `_resolve_message_alias`, before the final `return to`:
   - `if to in ("self", "me"): return sender.name`
   - `if to.startswith(("self.", "me.")): return sender.name + "." + to.split(".", 1)[1]`
   - Extend the docstring: note the downward mirror + the accepted shadow risk.
2. **Reactive hint** — in `_addressing_hint`, org-root branch, append the
   `self.team` form to the example.
3. **Proactive JD** — in `role-maestro.md`, extend the `spawn_team` bullet.
4. **Tests** — unit on the resolver (table of in→out), flow on `_handle_actions`
   covering the three acceptance prongs (deliver / bare-self-ban / invalid-reject).
5. **Validate** — `ruff check src/ tests/ && ruff format --check src/ tests/ &&
   pytest -m "not integration"`.
6. **Deployed re-smoke** (S6 rule) — real maestro spawns a team, addresses
   `self.<team>`, goal lands first try; then close the issue.

## Notes for the implementer

- Keep the new branches adjacent to the existing `maestro`/`parent` checks so the
  two directions read as one symmetric block.
- `str.startswith` accepts a tuple — `to.startswith(("self.", "me."))` covers both
  words in one check.
- Do **not** add role gating or fuzzy matching — both are explicit non-goals of
  the resolver's existing contract.
