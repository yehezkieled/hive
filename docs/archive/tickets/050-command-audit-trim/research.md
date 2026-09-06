# Research — Ticket 050: audit & trim the command set

Method: a 9-agent audit pass (2026-07-02) — four group-auditors classified all
39 commands with code evidence; then one adversarial verifier per proposed
cut/consolidate tried to **refute** that removal is safe. Findings below are
file:line-grounded; the audit **refuted several of the ticket's guesses**.

## Q1 — The complete inventory and where each command lives

**39 commands** total:

- **38 in the dispatch registry** (`src/hive/commands/dispatch.py:89-127`,
  `_ROUTES`, post-045 shape): each maps to a group method
  (`formatter`/`datastore`/`git`) or an `_h_<name>` handler in `dispatch.py`.
  (`"empty"` at line 89 is an internal no-command route, not a user command.)
- **1 bridge-only**: `heartbeat` — deliberately *not* in the registry; routed in
  `telegram/bridge.py:382` → `_execute_heartbeat` (`:416`) because it mutates
  Telegram transport state. Exported to the drift guard via
  `BRIDGE_COMMANDS = KNOWN_COMMANDS | {"heartbeat"}` (`bridge.py:48`).

**Derivation chain (what a removal touches automatically):**

```
_ROUTES (dispatch.py)  ──▶ KNOWN_COMMANDS = frozenset(_ROUTES) - {"empty"}  (dispatch.py:843)
                            └─▶ BRIDGE_COMMANDS = KNOWN_COMMANDS | {"heartbeat"}  (bridge.py:48)
HELP_TEXT (telegram/help_text.py) ──▶ web autocomplete (landing.html:1158, `commands|tojson`)
tests/test_help.py: BIDIRECTIONAL drift guards — every BRIDGE_COMMAND needs a
HELP_TEXT entry AND every HELP_TEXT entry needs a bridge command.
```

→ Removing a command = delete the `_ROUTES` entry + its handler(s) + the
`HELP_TEXT` entry + any `telegram/commands.py` parser set membership,
**together** — the drift tests then stay green with no test edits (they compare
derived sets), and the web autocomplete follows `HELP_TEXT` automatically.
Exception: `tests/test_command_dispatcher.py:72` spot-checks a literal list that
includes `broadcast` — that one line needs updating.

## Q2/Q3 — What the audit found (deltas from the ticket's guesses)

**Confirmed cuts:**

- **`/swarm` — dead code.** `Team.workers` (`src/hive/models/team.py:19`) is a
  dataclass field **never populated anywhere in `src/`** post-Worker-retirement
  (018/ADR 0013). `_execute_swarm` (`dispatch.py:666`) hits
  `if not team.workers: return "...has no workers."` **every time** — the
  command is functionally a canned error message.
- **`/broadcast` — vestigial blast-to-all.** Mechanically works
  (`_execute_broadcast`, `dispatch.py:771-788`, loops all entities) but nothing
  depends on it: no role file tells an Entity to emit it, no caller, flagged by
  the 2026-06-30 web inventory.
- **`/budget` — debug dry-run twin of `/eval`.** Shares
  `scheduler.build_facts_prompt` (`scheduler.py:105`) with `/eval`; owns nothing.
  Verifier: code surface fully self-contained
  (`dispatch.py:127/308/471` + `telegram/commands.py:91`); **the one blocker is
  `docs/DEPLOYMENT.md:674`** presenting it as current — deleted in the same PR.
  All other "budget" hits in src/tests are the unrelated spawn-budget concept.

**Refuted cuts (the ticket was wrong — keep these):**

- **`/heartbeat` — NOT help_text-only.** Parser entry
  (`telegram/commands.py:89`), real bridge handler (`bridge.py:416`), a running
  background scheduler (`__main__.py:122/455`), config env vars
  (`config.py:143-144`), a health probe (`health_monitor.py:151`), a web
  view_model row (`view_model.py:490`), and its own tests
  (`tests/test_heartbeat.py`). Its absence from the dispatcher is the
  *documented design* (transport-state command), not drift.
- **`/eval` — live and load-bearing.** Sole production caller of
  `scheduler.run_once_for()` (`dispatch.py:465` → `scheduler.py:277`, docstring
  "(used by /eval)"), with two dedicated scheduler tests
  (`test_scheduler.py:218/335`). Cutting it orphans tested code; it is the only
  manual scheduler-tick debug surface.
- **`/audit` — live audit-log reader** (`formatter.py:158`) with its own web
  twin (`/api/audit`); the retirements removed entities, not the audit trail.
- **`/comms` — live inter-entity message log** (`formatter.py:80`), the audit
  trail for Maestro↔Lead traffic under the 029 model.

**New finding (not on the ticket's list):**

- **`/agent` — legacy vocabulary + near-duplicate handler.** `_h_agent`
  (`dispatch.py:259-264`) duplicates `_h_message` (`dispatch.py:233-240`) except
  it *lacks* the "No target specified." empty-target guard. "agent" is exactly
  the vocabulary CONTEXT.md retires. The verifier confirmed it is **not a clean
  deletion** — the `a:` addressing form is parsed to `name='agent'`
  (`telegram/commands.py:62`), asserted by `tests/test_commands.py:57`, and
  documented in both UIs — so it's a **consolidation** (repoint the `a:` parse
  to `message`), not a cut.
- **advisor** — already fully retired (013); no command surface exists. Nothing
  to do.

## Q4 — Safety verification summary

| Proposed | safeToCut | Gate |
|---|---|---|
| `/swarm` | **yes** | only self-references; `Team.workers` never populated |
| `/broadcast` | **yes** | only self-references + one literal in `test_command_dispatcher.py:72` spot-check list |
| `/budget` | **yes, with doc edit** | delete `docs/DEPLOYMENT.md:674` bullet in the same PR |
| `/agent` | no — consolidate | multi-file migration: parser + help + tests; behavior nuance below |
| `/eval` | no — keep | live callers + tests + docs |

`personalities/` is **clean for every candidate** — no role file tells an Entity
to emit any of them (Entities act via `<hive_actions>`, not slash commands).

## Q5 — Mechanical removal surface (per command)

- **swarm:** `_ROUTES` entry `dispatch.py:109`, `_h_swarm` `:275`,
  `_execute_swarm` `:666-…`, HELP_TEXT `"swarm"`. (Optional follow-up, not 050:
  the now-provably-dead `Team.workers` field itself.)
- **broadcast:** `_ROUTES` `:115`, `_h_broadcast` `:293`, `_execute_broadcast`
  `:771-788`, HELP_TEXT `"broadcast"`, + update the literal spot-check list
  `tests/test_command_dispatcher.py:72`.
- **budget:** `_ROUTES` `:127`, `_h_budget` `:308`, `_execute_budget` `:471`,
  HELP_TEXT `"budget"`, `telegram/commands.py:91` targeted_commands member,
  `docs/DEPLOYMENT.md:674` bullet.
- **agent → message:** repoint the `a:` regex parse (`telegram/commands.py:62`)
  to emit `name='message'`; delete `_ROUTES` `"agent"` `:105` + `_h_agent`
  `:259-264` + HELP_TEXT `"agent"`; update `tests/test_commands.py:57`.
  ⚠ Behavior nuance: `_h_message` guards empty targets ("No target specified.")
  while `_h_agent` passed `''` through — after consolidation the guard applies
  to `a:` too (strictly better error; the only user-visible delta).

## Q6 — Cross-cutting impact

- `docs/DEPLOYMENT.md` — remove the `/budget` bullet (`:674`); check the same
  "New commands" list for `/eval` wording (stays, it's kept).
- `CONTEXT.md` — no term changes needed (Worker/advisor retirement already
  recorded); the trim itself needs no glossary edit.
- Cosmetic drift found by the audit, worth folding into the same PR (doc-only):
  HELP_TEXT `org` description still says "maestros → teams → **workers**"
  (stale vocab; the formatter emits leads), and `_format_teams`
  (`formatter.py:217`) labels lead-count as `workers=<n>`.
