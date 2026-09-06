# Questions — Ticket 045: CommandRouter registry + read-only Formatter split

The unknowns going in. Split into **code-answerable** (resolved in `research.md`
by reading the code) and **design decisions** (resolved in `design.md`, needs a
human call). A wrong answer here strands the whole refactor — this is a
*no-behaviour-change* ticket, so "what exactly is the current behaviour" must be
nailed before anything moves.

## Code-answerable (→ research.md)

1. **Caller contract.** Every site that constructs `CommandDispatcher` and what
   it passes — does the 8-store `__init__` signature have to stay
   byte-compatible? Who calls `.dispatch()` vs `.dispatch_command()`, and how is
   `CommandResult` (`.text`/`.metadata`/`.routed`/`.entity`) consumed per
   surface (Telegram bridge, web, scheduler)?

2. **Read-only need, concretely.** Is there an actual web read surface today
   that builds the whole dispatcher just to render a view? Quote it — it's the
   ticket's motivation and the acceptance bar for "Formatter needs only a
   ProcessManager".

3. **Dependency matrix.** For each of the ~35 `_execute_*` / `_format_*`
   handlers, exactly which instance stores does it touch? This decides whether
   the FORMATTER / DATASTORE / GIT grouping is clean or leaks.

4. **Cross-group coupling.** Shared private helpers (`_send_to_entity`,
   `_worktree_for`, any `_format_*` called by another handler) — who calls what?
   These couplings decide if the split is actually achievable without a circular
   dependency between facade and collaborator.

5. **ADR 0006 precedent.** How was `process/manager.py` split — facade holds
   collaborators as attributes, constructed in `__init__`? Do collaborators take
   the shared deps or a back-reference to the facade? File layout (one module
   each / a subpackage)? 045 must mirror this exactly.

6. **Help drift-guard.** `KNOWN_COMMANDS` (dispatch.py:69), `BRIDGE_COMMANDS`
   (telegram bridge), `help_text.py`, and any drift-guard test — can a
   `name → handler` registry become the single source of truth that
   `KNOWN_COMMANDS` derives from (`registry.keys()`)? What must the guard test
   assert after the change?

7. **Test + coverage baseline.** Which command groups have unit coverage today,
   what mock-`ProcessManager` fixture exists to reuse, and what's the CI
   coverage floor (ticket 011) the refactor must not drop below?

## Design decisions (→ design.md, human call)

8. **Registry signature normalisation.** Handlers have heterogeneous shapes:
   sync vs async; `(target, args)` vs `(target, args, actor)`; some embed
   pre-routing (`team` with a `.` routes to an entity; `message`/`agent` set
   `routed=True, entity=...`). What uniform callable shape does the registry
   map to — e.g. `name → async (cmd, actor) -> CommandResult` adapters — and
   where do the few special-cased commands (`empty`, `team`-dot-routing) live?

9. **Formatter purity vs reality.** Acceptance says Formatter needs "only a
   ProcessManager (no approval/mutation stores)". But `_format_cost` uses
   `token_store`, `_format_audit` uses `audit_log`, `_format_tasks_list` uses
   `task_store`. Are *read-only* stores allowed in the Formatter (only
   approval/mutation stores banned), or must cost/audit/tasks live elsewhere?
   Resolve the exact Formatter constructor.

10. **Lane: one PR or fan-out?** Every phase heavily rewrites the same
    `dispatch.py`. Direct lane (one PR, registry commit then group commits) vs
    fan-out (separate PRs that would rebase-collide on one file). Provisional:
    **direct**. Confirm at plan.md.

11. **Group scope this ticket.** Extract all three groups (Formatter +
    DataStore + Git), or land registry + Formatter (the web-serving slice) and
    bank DataStore/Git? Ticket commits to all three — confirm we hold that.

12. **Collaborator construction.** Do the groups get only the stores they need
    (constructed in the facade with a subset of deps), or the full set? And do
    they take a `ProcessManager` + specific stores, or the facade itself?
