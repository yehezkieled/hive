# 006 — Rename `WorkerAgent` → `Worker`

## What

Rename the `WorkerAgent` class to `Worker` across `src/` and `tests/`.
Internal-only change. No string-literal `"worker"` touched, no
database migration, no public-API breakage.

## Why

`CONTEXT.md` flags this drift explicitly: the canonical role term is
**Worker**, but the class is `WorkerAgent`. It is also the **only**
entity class with the `*Agent` suffix — siblings are `Maestro` and
`TeamLead`. The outlier name slows reading and signals that the
naming decision was abandoned mid-flight.

Phase 2 lists this as one of three Restructure targets. It is the
cheapest of the three (~3-4 hours, near-zero risk) and a useful
warmup before the heavier `manager.py` and Vault refactors.

## Acceptance

- `grep -rn "WorkerAgent" --include="*.py" src/ tests/` returns
  zero matches.
- All `"worker"` role strings remain unchanged.
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- Full `pytest` passes — no behavioural change expected.
- Blast radius matches research (≈ 14 files, 66 references).
- Smoke check: `hive.service` starts, a maestro turn spawns a worker,
  the worker completes a task end-to-end.

## Sprint

Committed to Sprint **2026-Q2-S3** (2026-06-01 → 2026-06-15) as the
Phase 2 warmup — cheap, mechanical, low-risk. (Originally drafted
against S2; S2 closed on the Ticket 003 commitment only.)
