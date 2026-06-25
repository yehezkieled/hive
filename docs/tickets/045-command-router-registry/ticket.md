# 045 — CommandRouter registry + read-only Formatter split

> Opens the S9 **architecture-deepening** backend slot. From the 2026-06-25
> architecture audit (candidate #3). Follows the
> [ADR 0006](../../adr/0006-god-object-breakup-composition.md) composition pattern
> already used for `process/manager.py`.

## What

Decompose `commands/dispatch.py` (1367 LOC — the biggest file in the repo; ~35
`_execute_*` methods behind a flat 39-command if-else chain). Two phases:

1. **Registry** (mechanical, low-risk) — replace the if-chain with a
   `name → async handler` dict so `dispatch_command` becomes a short lookup; a new
   command is one registry line, not a chain edit in two places.
2. **Group extraction** (ADR 0006 collaborators) — peel cohesive handler groups
   off the facade: a read-only **Formatter** (status / org / teams / quota / cost
   / audit / help), **DataStore** commands (vault — hard-money semantics, ADR
   0017 — + blueprint), and **Git** commands (commit / pr / merge / files +
   `_worktree_for`). The facade keeps thin delegations.

## Why

`dispatch.py` conflates routing, read-only view building, data-store CRUD, git
ops, and entity lifecycle under one class carrying 8 store dependencies. A
read-only surface (e.g. a web status endpoint) must instantiate the whole
mutation + approval machinery just to render status. Deletion test =
**concentrates**: the routing + grouping logic reappears across callers if
removed. Splitting concentrates each concern behind a small interface and lets a
read-only surface use only the Formatter — directly serving the web track's
read-only-endpoint need.

## Acceptance

- `dispatch_command` routes via a registry lookup (no 39-arm if-chain); adding a
  command is one handler + one registry entry.
- Read-only commands render behind a Formatter that needs only a `ProcessManager`
  (no approval/mutation stores).
- DataStore + Git command groups extracted as collaborators; the facade keeps thin
  delegations — **no behaviour or output change**.
- Each group gets isolated unit tests with a mock `ProcessManager`; the full
  command test suite stays green. `ruff` + `pytest -m "not integration"` green.

## Non-goals

- A multi-transport command-adapter layer for Telegram / Web / CLI (a separate,
  larger effort — Phase 5 / a Telegram-cleanup ticket).
- Changing any command's behaviour or output (pure decomposition).
- The ActionRouter / `message_dispatcher` decomposition (audit #1 — banked to
  roadmap Phase 6; wants its own sprint).

## Notes / open

- Do the **registry first**, then the group extractions (two reviewable commits).
- Watch the CI coverage floor (Ticket 011) when moving logic across the unit /
  integration boundary.
- Hot spots: `commands/dispatch.py:194-353` (the chain), `:955-1247` (handlers).
