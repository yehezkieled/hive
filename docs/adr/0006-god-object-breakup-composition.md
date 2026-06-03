# God-object breakup uses composition, not mixins

## Context

`process/manager.py` is a 2,469-LOC `ProcessManager` class with 53
methods spanning nine responsibilities. Phase 2 (Restructure) breaks
it into a thin core plus four focused modules (Ticket 004). The
mechanical question — *once a method leaves the class, how does it
reach the shared state (`_entities`, the single `_state_lock`, the
stores) it used through `self`?* — has more than one answer, and the
choice sets a precedent: Vault consolidation (Ticket 005) and later
restructure work face the same fork. The QuotaMonitor split (commit
`30fa909`) set an informal precedent but never recorded it.

A binding constraint: acceptance requires *"each new module has its
own test file with isolated unit tests,"* and *zero public-API
breakage* (24 sites import `from hive.process.manager import
ProcessManager`).

## Decision

Break up stateful god objects with **composition**. The original class
stays as a **facade + shared-state holder**; extracted responsibilities
become **collaborator objects** instantiated in `__init__`, each
holding a back-reference (`self._mgr`) to the facade and reaching
shared state through it. Public methods on the facade become thin
delegations.

Rules that make it work:

- The facade keeps its file name and class name, so every import path
  survives.
- The facade re-binds **every method any external code or test
  references on the instance — public or private**. Private methods
  that move to a collaborator but are externally referenced get an
  explicit thin delegation.
- Collaborators call each other only **through the facade's public
  surface**, never collaborator-to-collaborator, so no collaborator
  imports another (no import cycle; the `self._mgr: T` type hint sits
  under `TYPE_CHECKING` with postponed annotations).
- Genuinely-pure logic inside a collaborator (rate-limit windows,
  debounce) is extracted as a pure helper — the QuotaMonitor pattern
  nested inside composition.

## Considered options

- **Mixins** — split methods into `*Mixin` classes the original
  multiply-inherits. Smallest diff: `self._entities` keeps working
  untouched. Rejected: a mixin can't be instantiated or unit-tested
  without the full host class's state surface, so it fails the
  isolated-test acceptance. It relocates code without decoupling it —
  cosmetic.
- **Pure functions only** (the QuotaMonitor shape) — extract stateless
  functions taking explicit args. Rejected as the *primary* pattern:
  it only fits stateless logic, and ~1,900 of `manager.py`'s LOC are
  stateful orchestration over shared dicts under a lock. Kept as a
  *nested* tactic for the pure pieces.
- **Separate `ProcessState` object** the facade and collaborators both
  hold — cleaner state ownership, but an extra indirection and a
  second new type with no caller benefit; the thinned facade already
  *is* the state holder.

## Consequences

- Bigger diff than mixins: every `self._foo` in a moved method becomes
  `self._mgr._foo`. More surface for an accidental behaviour change in
  a refactor that must be behaviour-preserving — mitigated by
  copying critical sections verbatim and keeping the suite green
  between every slice.
- The facade grows a block of thin delegations, including for the
  private methods external wiring binds to (`_handle_actions`,
  `_get_or_create_adapter`, `_on_gate_state`, `_gate_nudge`,
  `_auto_kickoff`). These must be real bound methods so monkeypatching
  (`pm._record_usage = AsyncMock()`) still works.
- Shared mutable state stays facade-owned and is mutated *through* the
  back-ref. The fragile case: state that is **rebound** rather than
  mutated in place (the `_last_*` introspection lists are reset with
  `self._last_x = []`) must be rebound on the facade
  (`self._mgr._last_x = []`), or the facade attribute tests read goes
  stale.
- This is now the house pattern for the Phase 2 restructure; later
  breakups (Ticket 005) should cite this ADR rather than re-deciding.
- Reversible with effort (it is a structural refactor, not a runtime
  or billing commitment), so the decision is recorded but not treated
  as a one-way door.
