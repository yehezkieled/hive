# 007 — Remove the headless (non-PTY) runtime path

## What

Retire the headless / non-PTY runtime path now that the PTY harness is
the production runtime. Remove `process/claude_session.py`
(`ClaudeSession`), the `use_pty=False` branches in
`runtime/claude_adapter.py`, the headless `initial_session_id` resume
logic, and the `HIVE_USE_PTY` flag itself — committing Hive to
PTY-only. Re-base the unit suite, which currently runs *on* the
headless path, onto a mocked PTY session.

The advisor's one-shot `claude -p` call (`mcp/advisor_server.py`) is a
separate raw subprocess, not `ClaudeSession`. It stays untouched.

## Why

The PTY harness is live and plan-billed in production (Phase 1, Tickets
001 + 003). The headless path is now dead weight kept only as a
fallback — and after the 2026-06-15 cutoff it would be API-billed
anyway, so it is not a fallback Hive ever wants to take. Two runtimes
mean every `send_to_entity` change has to reason about both branches,
and the `HIVE_USE_PTY` flag scatters conditionals across the manager
and the adapter. Removing it is the cleanup tail of the runtime
migration and a Phase 2 navigability win.

## Acceptance

- `process/claude_session.py` is gone;
  `grep -rn "ClaudeSession" src/ tests/` returns zero matches.
- No `use_pty` / `HIVE_USE_PTY` conditionals remain in
  `runtime/claude_adapter.py` or `process/manager.py`; the PTY path is
  unconditional.
- The advisor one-shot `claude -p` path is unchanged and its tests
  pass.
- The unit suite no longer depends on `HIVE_USE_PTY=false` — it runs on
  a mocked PTY session; `tests/conftest.py` no longer pins the flag.
- `ruff check`, `ruff format --check`, and full
  `pytest -m "not integration"` all green.
- An ADR records the decision to drop the headless runtime and go
  PTY-only.
- Smoke test: `hive.service` restarts, a maestro turn runs on PTY
  end-to-end.

## Sprint

Committed to Sprint **2026-Q2-S3** (2026-06-01 → 2026-06-15). The
heaviest risk in the sprint after 004; first to slip if the window
tightens.

## Cross-cutting / notes (✱)

- **ADR required.** Going PTY-only is a one-way architectural door —
  new append-only ADR (next number) authored at design stage.
- **Reference docs.** Check `docs/DEPLOYMENT.md` / `ARCHITECTURE.md`
  for headless-runtime mentions and update them in this ticket; declare
  the exact impact in `plan.md`.
- **Test-harness rework is the crux.** The conftest `HIVE_USE_PTY=false`
  pin (added in Ticket 003 to stop unit tests spawning a real `claude`)
  exists precisely because the suite exercises the headless branch.
  Design must define the mocked-PTY fixture before any deletion.
- Artifacts to author at grab time: `research.md`, `design.md`,
  `outline.md`, `plan.md`. No drafts exist yet — this ticket is new.
