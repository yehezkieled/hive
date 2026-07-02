# Plan — Ticket 050: audit & trim the command set  (issue #254)

**Lane:** direct (one cohesive trim, one PR). **Sprint:** 2026-Q2-S10.
**No `outline.md`** — one-shape ticket: the same removal recipe per cut command,
plus one small consolidation. Decision table in `design.md`; evidence in
`research.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/commands/dispatch.py` | modify | 1. Delete `_ROUTES` entries `"swarm"` (:109), `"broadcast"` (:115), `"budget"` (:127), `"agent"` (:105). 2. Delete handlers: `_h_swarm` (:275) + `_execute_swarm` (:666–), `_h_broadcast` (:293) + `_execute_broadcast` (:771–788), `_h_budget` (:308) + `_execute_budget` (:471–), `_h_agent` (:259–264). |
| `src/hive/telegram/commands.py` | modify | 3. Repoint the `a:` regex parse (:62) to emit `name="message"`. 4. Remove `budget` from the `targeted_commands` set (:91); drop `swarm`/`broadcast`/`agent` parser memberships if present. |
| `src/hive/telegram/help_text.py` | modify | 5. Delete `HELP_TEXT` entries `swarm`, `broadcast`, `budget`, `agent`. 6. Polish: fix `org` description "workers" → "leads". (Autocomplete derives from `HELP_TEXT` — no web edit.) |
| `src/hive/web/static/formatter…` `src/hive/commands/formatter.py` | modify | 7. Polish: `_format_teams` (:217) label `workers=<n>` → `leads=<n>` (cosmetic; keep the lead-count semantics). |
| `tests/test_commands.py` | modify | 8. Update the `a:` parse test (:57) to expect `name == "message"`. |
| `tests/test_command_dispatcher.py` | modify | 9. Replace `broadcast` in the literal spot-check list (:72) with a kept command. Add a regression test: dispatching `swarm`/`broadcast`/`budget`/`agent` returns the unknown-command path. |
| `docs/DEPLOYMENT.md` | modify | 10. Delete the `/budget [maestro]` bullet (:674). ✱ cross-cutting (declared). |

Drift guards (`tests/test_help.py`) need **no edits** — they compare derived
sets (`BRIDGE_COMMANDS` ↔ `HELP_TEXT`), which stay consistent when registry +
help entries are removed together.

## Verification

- RED→GREEN per removal: first add the "returns unknown-command" regression
  test (fails while the command exists), then remove the command (passes).
- `a:` addressing round-trip: `a:<lead> hello` parses to `message` and reaches
  the entity; empty-target `a:` returns "No target specified."
- Kept commands byte-identical (`/help` output diff shows only removed entries).
- `ruff check` + `ruff format --check` + full `pytest -m "not integration"`
  green; watch CI by run-ID before merge.
- Deployed smoke: `/help` over the web shows the trimmed set; `/swarm` returns
  unknown-command.

## Out of scope

- UI for kept commands (052/053 own that).
- Behaviour changes to kept commands (`/eval` stays as-is; the `/scheduler`
  regroup idea is rejected for now — see `design.md`).
- Removing the dead `Team.workers` model field (flagged follow-up, not 050).
- `CONTEXT.md` edits — no term changes.

## Cross-cutting impact

- `docs/DEPLOYMENT.md` — one bullet deleted (step 10). No other reference-doc,
  ADR, or CONTEXT.md impact.

## Build handoff

Direct lane: one branch `ticket-050/trim` → one PR that **closes #254** once
green + deployed smoke passes. run-ticket ends here.
