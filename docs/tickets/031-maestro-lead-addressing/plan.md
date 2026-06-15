# Plan — Ticket 031: maestro addresses its own lead as `self.<team>`  (issue [#161](https://github.com/yehezkieled/hive/issues/161))

Direct lane — one PR. Mirror Hive's existing **upward** addressing aliases
(`maestro`/`parent`) with a **downward** `self`/`me` alias in the recipient
resolver, plus sharpen the maestro JD and the rejection hint so `self.<team>` is
the documented, self-correcting path. Full spec (acceptance + tests) lives in
[#161](https://github.com/yehezkieled/hive/issues/161).

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/message_dispatcher.py` | edit | `_resolve_message_alias` (~:617): add `self`/`me` downward branches + docstring note |
| `src/hive/process/message_dispatcher.py` | edit | `_addressing_hint` org-root branch (~:670): advertise `self.<team>` |
| `personalities/role-maestro.md` | edit | `spawn_team` bullet (~:98): "...address it as `self.<team_name>` (no name needed)" |
| `tests/process/test_message_dispatcher*.py` | add/edit | unit: resolver in→out table; flow: `self.<team>` delivers, bare-`self` ban, invalid rejects |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"` green.
- Unit `_resolve_message_alias`: `self.smoke`→`hive_dev.smoke`; `me.smoke`→same;
  bare `self`/`me`→sender; `maestro`/`parent` unchanged; `foo.bar` passes through.
- Flow `_handle_actions`: maestro → `to:"self.smoke"` routes to the lead (no
  `Unknown recipient`); bare `self` → self-message-ban feedback; `<maestro>.ghost`
  → rejected with the addressing hint.
- **Deployed re-smoke (S6 rule):** a real maestro spawns a team, addresses
  `self.<team>`, and the goal lands on the first attempt — no manual workaround.
  Holds the issue open until this passes.

## Out of scope

- maestro→user delivery (`Unknown recipient: user`) — Ticket 021.
- Bridging maestro interactive gates — Ticket 029.
- Reserving `self`/`me`/`maestro`/`parent` as entity names — separate, broader change.

## Cross-cutting impact

- **None to reference docs.** No CONTEXT.md change (addressing mechanics, not a
  domain entity); no new ADR (extends Ticket 023's documented alias decision —
  the resolver docstring carries the one-line note). `README`/`DEPLOYMENT`
  untouched (no new service/port; ships in `src/` + one personality file).

## Build

One branch (`ticket-031/issue-161-self-alias`), one PR that closes
[#161](https://github.com/yehezkieled/hive/issues/161). Build directly (you or a
single agent) — not a fleet ticket.
