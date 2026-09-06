# Questions — Ticket 023

The unknowns going in, seeded by [`ticket.md`](ticket.md). Factual
questions get answered by code research (→ `research.md`); design forks
go to the grill (→ `design.md`).

## Factual — for research

### Floor wiring (the DI gap)

1. **What does `WorktreeManager` need to be constructed?** Constructor
   signature, repo path, worktrees directory — and which of those
   already exist as config keys in `config.py` vs need adding.
2. **Where should production worktrees live?** What directory does the
   current (test-only) usage assume, and what's safe on the VPS — a
   sibling of the repo, inside `~/.hive`, or elsewhere? What does the
   deployed service's `WorkingDirectory`/permissions allow?
3. **Is re-attach actually idempotent across a service restart?** 015
   claims idempotent re-attach for the lead worktree. With live entities
   persisted and the service bounced, does `create()` re-attach to an
   existing worktree or fail (`'<branch>' already used by worktree`)?
4. **What happens to orphaned worktrees?** Entity killed while the
   service is down, or a crash mid-spawn — does anything sweep stale
   worktrees/branches at startup, or do they accumulate?
5. **Does the wiring change any test assumptions?** 015's hermetic tests
   inject a fake `WorktreeManager`; what additional test proves the
   *production wiring* (the exact gap 023 exists to close)?

### Message delivery (fold-in 1)

6. **Root cause of the lost lead→maestro proposal** — the 015 live smoke
   stalled because otter never received the lead's breakdown. Mechanism?
   Candidates: message queued but the idle maestro is never woken (no
   turn trigger on enqueue); dropped by a permission check; router has
   no queue for the recipient (021's gap, wider than `user`). What does
   `router.py` / `message_dispatcher.py` actually do when a peer message
   arrives for an idle entity?
7. **Is lead→maestro the same root cause as maestro→user (021)?** Same
   fix, or two fixes? Decides whether 021 gets widened or 023 fixes peer
   delivery and cross-links.
8. **Is there incident evidence?** Audit log / journal entries from
   2026-06-10/11 showing the message was parsed, permitted, and then
   went where?

### Defaults audit (fold-in 2)

9. **Inventory of "hardcoded default above the canonical default".**
   PR #90 fixed `model` at the dispatch + facade layers. Sweep the spawn
   paths (`create_team`, `spawn_worker`, `/worker spawn`,
   `register_maestro`, scheduler) for remaining shadow defaults across
   the knobs: model, cwd, permission_mode, advisor. Where is each knob's
   single source of truth today?

## Open — for the design grill

10. **Wake-on-message semantics** — when a peer message lands for an
    idle entity, should delivery *trigger a turn* (immediate dispatch),
    or queue until something else wakes it? What does that do to the
    idle-reaper and to plan-quota burn? (This is the design heart of
    fold-in 1.)
11. **021 scope call** — widen 021 to all dead-letter recipients and do
    it here, or fix peer delivery in 023 and leave maestro→user to 021?
12. **Where the defaults consolidate** — one `config.py` source of truth
    per knob, or per-role defaults in `role_tool_denylist`-style code?
    (Mirrors 015's markdown→code move for tool policy.)
13. **Worktree directory policy** — sibling-of-repo vs `~/.hive/worktrees`;
    interaction with the observed sibling placement of Workflow
    `isolation:'worktree'` agents under a lead that itself runs in a
    worktree.
14. **Scope guard** — 023 must not creep into 016 (changed-worktree
    commit→PR→merge cleanup) or 017 (visibility). What's the minimal
    "floor is live and the org can talk" cut?
15. **Live verification procedure** — the exact deployed smoke:
    maestro→lead→leaf turn that edits a repo file, main checkout stays
    clean, `git worktree list` shows the lead worktree, kill removes it.
