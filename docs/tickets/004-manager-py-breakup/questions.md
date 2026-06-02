# Questions — Ticket 004: break up `process/manager.py`

The unknowns going into the design stage. `research.md` answered the
*shape* questions (size, responsibilities, coupling, staging); these
are the *design* questions that decide how the split is actually
built. Each is resolved in `design.md` — pointer noted inline.

## Structural

1. **Once a method leaves the `ProcessManager` class, how does it
   reach shared state (`_entities`, `_state_lock`, the stores)?**
   The 47→53 methods all read/write state through `self.`. Moving
   them to a sibling file breaks `self._entities`. Mixins (keep one
   class, split files), composition (real collaborator objects with a
   back-ref), or pure functions? → *design.md §Pattern.*

2. **Does the thinned core keep the name `manager.py`, or get renamed
   to `state_manager.py` as `research.md` proposed?** Constrained by
   who imports it. → *design.md §Core naming.*

3. **Which symbols must stay importable from `hive.process.manager`
   after the split, or tests / call sites break?** Determines what
   has to be re-exported. → *design.md §Import safety.*

## Boundaries

4. **Where do the six interactive-gate methods go?** They landed in
   Ticket 003 (`_on_gate_state`, `_notify_gate_waiting`, `_gate_nudge`,
   `approve_gate`, `deny_gate`, `reconcile_orphaned_gates`) *after*
   `research.md` was written, so the research's four-module table never
   placed them. → *design.md §Module map.*

5. **Is the 53-method partition into 5 buckets clean — does any method
   straddle two clusters or call private helpers across a boundary?**
   A leaky boundary baked into issues is expensive to unwind. →
   *design.md §Boundary verification.*

## Execution

6. **Can the slices run in parallel (normal fan-out), or must they
   serialize?** Every slice rewrites the same file (`manager.py`), and
   the first slice establishes the collaboration pattern the rest
   copy. → *design.md §Sequencing* and `plan.md §Execution waves.*

## Safety

7. **Does the split preserve the flagged concurrency hazards?** The
   non-reentrant `_state_lock` (must stay a single shared instance,
   no `await` across it), the fire-and-forget `_kickoff_tasks` /
   `_wake_tasks` GC-tracking, the `_last_*` introspection lists tests
   assert on, and the `_parse_failure_budget` deque. → *design.md
   §Hazard preservation.*

8. **Is the composition-vs-mixins decision ADR-worthy?** It sets the
   house pattern for the rest of the Phase 2 restructure. →
   `docs/adr/` (decided yes).
