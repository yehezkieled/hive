# Isolate projects by spawn-env scoping and per-project resources, not containers

## Status

Accepted, 2026-10-01. Extends [ADR 0017](0017-ownership-guard-pretooluse-hook.md)
(its "OS-level isolation — revisit only if the threat model changes" clause);
does not reverse it.

## Context

A project built on Hive (the finance app is the first) must not bleed into
Hive's own DB, env, ports, or deps. The ownership guard (ADR 0017) fences only
Claude's file-edit tools, not `Bash` or subprocesses. A project's `psql`,
`pip install`, or `alembic upgrade` can therefore reach Hive's Postgres, venv,
and ports.

Facts checked in the code:

- `PtySession.start` calls `PtyProcess.spawn(args, cwd=..., dimensions=...)`
  with no `env=` (`src/hive/runtime/pty_session.py`). Every Entity inherits the
  full environment of the `hive.service` process. That environment holds
  `POSTGRES_*`, `TELEGRAM_BOT_TOKEN`, `HIVE_WEB_TOKEN`, `HIVE_VAPID_PRIVATE_KEY`,
  and the service venv's `PATH`/`VIRTUAL_ENV` (`src/hive/config.py`).
- The leak is a *default*, not a sandbox failure. Nothing is injected on
  purpose, so the exposure can be removed by choosing what a spawn passes on.
- The threat model is the one ADR 0017 set: cooperative Entities making
  *accidental* cross-project mistakes, not a hostile breakout.
- Hive is single-user, plan-billed, and uses per-user Claude Code state
  (`~/.claude`). Entities run PTY sessions of the user's own `claude` binary.

## Decision

Isolate with **a scrubbed spawn environment plus per-project resources**. Do
not containerize.

1. **Allowlist spawn env for project Entities.** A Maestro that owns a Project,
   and the Leads under it, spawn with an explicit environment built from a small
   allowlist (`HOME`, `PATH` minus the service venv, `LANG`/`TERM`, the Claude
   auth and config vars Claude Code needs), plus the project's own variables.
   Hive's `POSTGRES_*`, `HIVE_*`, `TELEGRAM_*`, `VAPID_*`, `VIRTUAL_ENV`, and
   `PYTHONPATH` are never passed. An allowlist, not a denylist, so a future
   Hive setting does not leak by default.
2. **Hive-side Entities keep today's env.** The PA Maestro owns no Project and
   keeps the full inherited environment (it needs Hive's MCP servers). The
   scrub applies only to Entities with a Project.
3. **Own resources, declared per Project.** A Project record carries an env
   file (`<root>/.hive/env`, gitignored) that supplies its DSN or SQLite path,
   its venv location, and its port range. Hive reads it at spawn and merges it
   into the allowlisted env. Hive reserves its own ports and refuses a Project
   range that overlaps them.
4. **Own repo and worktrees.** Leads build in the project's repo, cut from
   `Project.root_path`, not Hive's. This is the worktree floor and is a
   prerequisite for a build that can finish.
5. **The ownership guard stays as is.** Env scoping closes the *default* path to
   Hive's resources. It does not make `Bash` a wall. A project subprocess that
   deliberately reads Hive's `config.py` and dials Postgres still can.

## Alternatives considered

- **Container per project.** Strongest boundary: it also fences filesystem,
  network, and ports. Rejected for now. Each Entity is a PTY session of the
  user's `claude` binary using `~/.claude` state and plan auth; running that
  inside a container needs the auth, config, hooks, MCP servers, and PTY bridge
  mounted in or reimplemented. That is a large change to the harness-agnostic
  runtime (ADR 0001, 0007) for a threat the model does not include, and the
  M2 goal is to unblock the finance app, not to harden against hostile code.
- **Convention only (no code).** Rejected. The leak is the inherited env, so a
  written rule leaves the DSN in every spawned shell. The scrub is cheap and
  testable.
- **Per-project Unix users.** Rejected in ADR 0017 and still rejected: it breaks
  per-user Claude state and plan billing.
- **Denylist of Hive variables.** Rejected: a new Hive variable leaks until
  someone remembers to list it.

## Consequences

- The fix is small and lives at one chokepoint: build the spawn env where
  `PtySession.start` is called, from the Entity's Project.
- The isolation is only as good as the allowlist. A tool that needs an
  unlisted variable fails loudly (missing var), which is the safe direction.
- It does not stop a hostile Entity. If the threat model ever includes
  untrusted Entities or third-party project code, revisit containers; the
  per-project env file and port range from this ADR carry over unchanged.
- The Project registry gains an env-file pointer and a port range (shape for
  T010).

## Shape of T009 and T010 (acceptance derived from this model)

**T009 — Per-project worktree floor.** Acceptance lines already match this
model; add:

- [ ] A Lead's worktree path is under the project's root, never under Hive's
  `WORKTREES_DIR`.
- [ ] Orphan-worktree reconciliation runs once per Project root and is still
  scoped to that Project's worktree dir.

**T010 — Project isolation: own DB, env, ports.** Replace the acceptance with:

- [ ] A spawn env builder returns an allowlisted env for any Entity with a
  Project, and unit tests assert `POSTGRES_*`, `HIVE_*`, `TELEGRAM_*`,
  `VAPID_*`, `VIRTUAL_ENV`, and the service venv on `PATH` are absent.
- [ ] `PtySession.start` passes that env to `PtyProcess.spawn`; the PA Maestro
  path is unchanged and tested as such.
- [ ] The Project's env file (DSN or SQLite path, venv, port range) is merged
  into the spawn env; a missing file is a clear spawn-time error, not a silent
  fallback to Hive's values.
- [ ] A Project port range overlapping a port Hive binds is rejected at
  registration.
- [ ] An integration smoke spawns a project Entity, runs `env` and a DB
  command inside it, and shows the project's DB is used and Hive's Postgres is
  not reachable by default.
