# ADR 0011 — Pin adapter transcripts by session id, not directory heuristics

- **Status:** Accepted
- **Date:** 2026-06-11
- **Ticket:** [023](../tickets/023-activate-worktree-floor/)

## Context

Hive reads an Entity's turns from the Claude Code transcript
(`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`). The adapter
finds "its" transcript with a heuristic: snapshot the directory's
`*.jsonl` sizes before spawning, then bind to the **first file that is
new or has grown** (`transcript_reader.py:60-96`).

The heuristic was written when every spawned entity had its own
worktree, hence its own project dir. Ticket 015's inert floor put all
entities in one shared dir, and the 2026-06-11 smoke test produced the
predicted failure live (failure class F3, 023 `research.md`): a lead's
turn completed with a perfectly-formed, correctly-addressed proposal,
but its adapter had bound to a concurrent session's growing file — the
proposal was never read. **No error is raised anywhere; the failure is
silent.**

Activating the floor (023) fixes the geometry for leads and workers,
but not structurally: the PA Maestro shares the live checkout with the
developer's own `claude` sessions today, and a future project Maestro
shares its project dir with the developer's sessions *by design*
(per-project Maestro model). At least one entity always races human
activity for a directory guess.

## Decision

Bind each adapter to its exact session. Claude Code maintains
`~/.claude/sessions/<pid>.json` for every live process, containing
`{pid, sessionId, cwd, status, ...}` (verified on the fleet's pinned
binary). Hive spawned the process, so it knows the pid:

```
spawn PTY → read ~/.claude/sessions/<pid>.json → sessionId
          → transcript = <project_dir>/<sessionId>.jsonl
```

The new-or-growing heuristic is retained **only as a fallback** when
the pid-state file is missing or late, and a fallback bind is logged
loudly.

## Consequences

- Transcript reads become deterministic for every Entity in every
  dir-sharing scenario — concurrent maestros, developer sessions in
  the same repo, future multi-instance setups. The class is closed,
  not patched.
- **We depend on an undocumented Claude Code interface.** Accepted
  because: (a) the dependency already exists in plan — Ticket 020's
  jam detection trusts the same file's `status`/`waitingFor`; (b) the
  fallback keeps Hive functional if the file vanishes in an upgrade;
  (c) the fleet pins its CC version (Ticket 009), so a breaking change
  arrives only with a deliberate pin bump.
- The pin must be re-established when the PTY respawns (`--continue`
  produces a new pid and may resume a prior session id) — the pin is
  per-process, not per-entity.
- 016's long sync-wait turns inherit a reader that cannot
  mis-attribute turns across entities.
