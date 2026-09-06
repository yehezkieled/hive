---
id: T008
title: Project isolation design (sandbox model)
epic: E04
milestone: M2
status: todo
priority: P1
depends_on: []
owner:
auto: no
plan: required
ready: no
issue: 275
pr:
---
## What
Decide how a project built on Hive is isolated so its DB / env / ports / deps cannot bleed into Hive's. The fork: lightweight convention + env-scoping (the project maestro's spawn env excludes Hive's DSN and points at the project's own resources) vs full containerization (a container per project). The output is the chosen model, recorded as an ADR, plus the shape of the isolation work in T009 and T010.

## Why
Today the ownership guard fences file writes only, not Bash or subprocesses (ADR 0017). A project's `psql` / `pip install` / `alembic upgrade` can reach Hive's own Postgres, venv, and ports. The finance app having its own DB is exactly the case that breaks. This must be settled before an unattended build runs.

## Acceptance
- [ ] An ADR records the chosen isolation model (env-scoping vs container) with the trade-offs considered.
- [ ] T009 and T010 are re-grilled with acceptance lines derived from the chosen model.

## Subtasks
- [ ] Brainstorm the two models against the finance-app case
- [ ] Write the ADR
- [ ] Re-grill T009 and T010

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/055-project-isolation-design/`. Design-first; acceptance was "TBD at grilling" in the original and is a placeholder until /pm:grill.

## Proposed changes
