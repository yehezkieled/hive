# 032 — Validate entity/team names before they reach git/worktree paths

> Spun off from Ticket 030. 030's root cause was an entity name (`hive_dev`)
> whose underscore broke a name→path derivation (the Claude Code transcript
> slug). 030 fixed that one derivation by matching CC's slug rule, but the
> **names themselves are still unvalidated** — other name→path derivations
> remain exposed.

## What

Validate entity and team names at the point they are created (spawn / team
creation), rejecting names that aren't safe to embed in a filesystem path, a
git branch ref, or the addressing scheme — **before** any worktree or branch is
created. Restrict to a conservative allowlist (proposed `[A-Za-z0-9._-]`) and
fail with a clear error naming the offending character.

## Why

Names flow **raw and unsanitized** into multiple path/ref derivations (see
030 `research.md` §6):

- **Worktree dir:** `WORKTREES_DIR / name` (`process/worktree.py`).
- **Lead name:** `f"{maestro_name}.{team_name}"` (`lifecycle_manager.py`), and
  `team_name` is user-supplied.
- **Git branch:** `hive/{lead_name}`.
- **CC transcript slug:** handled by 030 — but that was one symptom, not the
  class.

A name with a `/`, space, `..`, or shell-meta char would break worktree
creation or branch refs (or worse) in ways 030's slug fix does **not** cover.
030 made Hive *agree with* whatever dir CC picks for a given cwd; it did not
make the names themselves safe. The bug class is "an unvalidated name becomes a
path/ref" — close it at the source.

## Acceptance

- Entity and team names are validated at creation against an allowlist
  (`[A-Za-z0-9._-]` proposed); an invalid name is **rejected with a clear,
  actionable error** before any worktree dir or git branch is created.
- The validation is enforced on every creation path (maestro spawn, team
  creation via command, maestro-driven team creation).
- Tests cover acceptance of valid names and rejection of the boundary cases
  (`/`, space, `..`, leading `-`, empty, shell-meta).
- Existing valid names (`otter`, `dev`, `hive_dev`, `<maestro>.<team>`) keep
  working unchanged.

## Non-goals

- Changing Claude Code's slug handling — **done in Ticket 030**.
- Renaming or migrating existing entities.
- Any path-derivation redesign (worktree layout, branch scheme) — validation
  only, not restructuring.

## Notes

Decide the allowlist and the rejection UX in `design.md`. Consider whether to
normalize (e.g. lowercase, collapse) vs. strictly reject — strict rejection is
simpler and more predictable, and matches "fail loud" over "silently rewrite."
Likely a small, well-bounded ticket; run the artifact pipeline when S7 opens.
