# Design — Ticket 050: the keep / consolidate / cut decision table

Classification of all **39** commands, grounded in the audited evidence
(`research.md`). Net result: **35 keep · 1 consolidate · 3 cut**.

## Cut (3)

| Command | Rationale |
|---|---|
| `swarm` | Dead code: `Team.workers` is never populated post-Worker-retirement (018/ADR 0013); the handler always returns "no workers". |
| `broadcast` | Unscoped blast-to-all with zero callers/dependents; flagged vestigial by the web inventory. |
| `budget` | Debug dry-run twin of `/eval` (same `build_facts_prompt`); owns nothing; one `DEPLOYMENT.md` bullet removed alongside. |

## Consolidate (1)

| Command | Into | Rationale |
|---|---|---|
| `agent` | `message` | Legacy vocabulary (CONTEXT.md retires "agent"); `_h_agent` near-duplicates `_h_message`. The `a:` addressing form **stays** — the parser repoints it to `message`. Only user-visible delta: empty-target `a:` now gets message's "No target specified." guard instead of silently passing `''`. |

## Keep (35)

| Group | Commands | Note |
|---|---|---|
| Read / status | `status` `health` `maestros` `org` `comms` `cost` `quota` `tasks` `teams` `files` `audit` | `audit`/`comms` cuts **refuted** — live read surfaces (audit has a web twin). Doc-drift polish: `org` help text + `teams` output still say "workers". |
| Entity / team control | `kill` `message` `team` `project` `new` `personality` `reset` `cancel` `compact` | `cancel` is load-bearing for the stateful `/new` flow. |
| Task / mode / loop | `task` `priority` `mode` `loop` `model` `blueprint` | All wired to live stores / spawn args. |
| Authority / approval | `approve` `deny` `vault` | The human-in-the-loop floor (mode/gate + money). |
| Git pipeline | `commit` `pr` `merge` | The worktree write floor; `merge` keeps its env-var safety gate. |
| Debug / meta | `eval` `help` `heartbeat` | `eval` cut **refuted** — sole caller of tested `run_once_for()`; the manual scheduler-tick surface. `heartbeat` cut **refuted** — live bridge-local feature (scheduler + config + health probe + web row); its dispatcher absence is by design. |

## Decisions & alternatives

- **`/eval` stays as-is.** The audit floated folding `eval`+`budget` into a
  `/scheduler tick|facts` command — rejected for 050: it *renames* a kept
  command's surface, violating the "no behaviour change to kept commands"
  non-goal. Cutting only `budget` captures the trim; a `/scheduler` regroup can
  be its own ticket if wanted.
- **`/agent` consolidation is in scope.** It is exactly the vestigial-vocabulary
  cleanup 050 exists for, and deferring it would make the web redesign wrap UI
  around a command scheduled to die. The empty-target guard change is accepted
  (strictly better error). The `a:` alias behavior is otherwise unchanged.
- **`Team.workers` field removal — deferred.** The `/swarm` cut proves the field
  is dead, but deleting a model field touches `_format_teams` and serialized
  state; it's model cleanup, not command trim. Flag for a later ticket.
- **Cosmetic vocab drift** (`org` help text, `teams` "workers=" label) — folded
  in as doc-only polish; no behavior change.

## Side effects

- **`CONTEXT.md`:** none — no term changes (retirements already recorded).
- **ADR:** none — this executes the retirement decisions already in ADR 0013
  (Workers) and Ticket 013 (advisor); no new architectural choice.
- **Reference docs:** `docs/DEPLOYMENT.md` — delete the `/budget` bullet
  (`:674`). Declared in `plan.md` (cross-cutting).

## Verification (defines the build PR's done-ness)

- Drift tests (`tests/test_help.py`, `tests/test_command_dispatcher.py`) green
  **without weakening** — they compare derived sets, so a *coordinated* removal
  passes them; the one literal list (`test_command_dispatcher.py:72`) updated.
- `tests/test_commands.py` `a:` parse test updated to expect `message`.
- Cut commands return "Unknown command" end-to-end; kept commands byte-identical.
- Full `pytest -m "not integration"` + `ruff` green.
