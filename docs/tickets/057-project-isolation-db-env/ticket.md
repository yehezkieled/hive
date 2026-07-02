# 057 — Project isolation: own DB / env / ports

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> Depends on the isolation design (055).

## What

Fence a project off Hive's runtime resources per the 055 design: its **own DB**
(own DSN / SQLite in its repo, never `HIVE_POSTGRES_DSN`), its **own env/venv**,
and **own ports** — so a project's subprocesses can't touch Hive's.

## Why

Beyond files (056), a project's `psql`/`pip`/server subprocesses share Hive's
Postgres, Python env, and ports today. Real isolation is what stops an autonomous
build from corrupting Hive's own state.

## Acceptance

TBD at grilling (shaped by the 055 model — env-scoping vs container).
