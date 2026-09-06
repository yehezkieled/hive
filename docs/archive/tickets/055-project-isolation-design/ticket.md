# 055 — Project isolation design (sandbox model)

> **Backlog · design-first — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> **Prerequisite** for any real product build on Hive.

## What

Decide how a project built on Hive is **isolated** so its DB / env / ports / deps
can't bleed into Hive's. The fork: lightweight **convention + env-scoping** (the
project maestro's spawn env excludes Hive's DSN, points at the project's own
resources) vs **full containerization** (a container per project). Own brainstorm.

## Why

Today the ownership guard fences *file writes* only — **not** Bash/subprocess
(ADR 0017). A project's `psql` / `pip install` / `alembic upgrade` can reach
Hive's own Postgres, venv, and ports. The finance app having its own DB is exactly
the case that breaks — this must be settled before an unattended build runs.

## Acceptance

TBD at grilling (the design produces the model + the isolation tickets 056/057).
