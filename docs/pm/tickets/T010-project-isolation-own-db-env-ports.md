---
id: T010
title: Project isolation: own DB, env, ports
epic: E04
milestone: M2
status: todo
priority: P1
depends_on: [T008]
owner:
auto: no
plan: none
ready: no
issue: 277
pr:
---
## What
Fence a project off Hive's runtime resources per the T008 design: its own DB (own DSN or SQLite in its repo, never `HIVE_POSTGRES_DSN`), its own env/venv, and its own ports, so a project's subprocesses cannot touch Hive's.

## Why
Beyond files (T009), a project's `psql` / `pip` / server subprocesses share Hive's Postgres, Python env, and ports today. Real isolation is what stops an autonomous build from corrupting Hive's own state.

## Acceptance
- [ ] A project maestro's spawn environment carries no Hive DSN, venv, or port bindings; a subprocess it runs cannot reach Hive's Postgres.
- [ ] An integration smoke proves the project's own DB is used end to end.

## Subtasks
- [ ] Shape the spawn env per the chosen model
- [ ] Smoke against a throwaway project

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/057-project-isolation-db-env/`. Acceptance is a first draft shaped by T008; settle at /pm:grill.

## Proposed changes
