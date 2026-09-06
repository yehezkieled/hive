# Plan — Ticket 007: Remove the headless (non-PTY) runtime path

PTY-only commitment. Single PR (DIRECT lane). Built on the post-004 module
layout. Commit sequence in [`outline.md`](outline.md); approach in
[`design.md`](design.md); decision in
[ADR 0007](../../adr/0007-pty-only-runtime.md).

**Dependency:** assumes Ticket 004 has landed (it has — #41–#45). Edits target
`lifecycle_manager.py`, not the old monolithic `manager.py`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/claude_session.py` | **delete** | c5 |
| `src/hive/runtime/claude_adapter.py` | modify — strip subprocess path, PTY-only | c4 |
| `src/hive/process/lifecycle_manager.py` | modify — delete `spawn_entity`/`_preempt`; `_get_or_create_adapter` PTY-only; trim shim | c3,c4,c5 |
| `src/hive/process/manager.py` | modify — drop `_sessions`/`ClaudeSession`/`HIVE_USE_PTY`; repoint `active_count`/`get_status`/`health_check`; reword docstrings | c2,c3,c5 |
| `src/hive/config.py` | modify — delete `HIVE_USE_PTY`, `PRIORITY_PREEMPT_ENABLED` (keep `MAX_CONCURRENT_SESSIONS`) | c5 |
| `tests/conftest.py` | modify — add `FakeAdapter`/`inject_adapter`; remove `HIVE_USE_PTY` pin | c1,c5 |
| `tests/test_process_manager.py` | modify — rebase ~30 sites; delete `TestPreemption` | c1 |
| `tests/test_preempt.py` | **delete** | c1 |
| `tests/test_claude_session.py` | **delete** | c5 |
| `tests/runtime/test_claude_adapter.py` | modify — drop subprocess tests, keep PTY | c4 |
| `tests/process/test_auto_retrieve.py` | modify — rebase onto `FakeAdapter` | c1 |
| `tests/test_peer_messaging.py` | modify — rebase | c1 |
| `tests/test_advisor_mcp.py` | modify — rebase | c1 |
| `tests/process/test_lifecycle_manager.py` | modify — rebase / drop spawn_entity cases | c1,c3 |
| `tests/process/test_thin_core_smoke.py` | modify — drop `ClaudeSession`/`HIVE_USE_PTY` parametrize rows | c5 |
| `docs/adr/0007-pty-only-runtime.md` | **create** | c6 |

## Verification

- `grep -rn "ClaudeSession" src/ tests/` → **0 matches** (acceptance).
- `grep -rn "HIVE_USE_PTY\|use_pty" src/ tests/` → **0 matches**.
- Advisor one-shot `claude -p` (`mcp/advisor_server.py`) unchanged; its tests pass.
- `ruff check src/ tests/` **and** `ruff format --check src/ tests/` (separate gates).
- Full `pytest -m "not integration"` green.
- **Smoke (not suite-only):** `systemctl --user restart hive.service`, then a
  maestro turn end-to-end on PTY; verify from the Tailscale IP, not loopback.

## Cross-cutting impact (✱ declared up front)

- **`docs/DEPLOYMENT.md`** — 4 edits: :32 (`claude -p must work` — reword: the
  *advisor* still needs it, the runtime no longer does), :229–230 (headless
  permission-mode note), :1091 (`HIVE_USE_PTY` env-var row — remove), :1108–1116
  (`claude -p` runtime / `--resume` notes — reword to PTY `--continue`).
- **`CONTEXT.md`** — Interactive-gate glossary entry: drop the headless clause.
- **`docs/tickets/004-manager-py-breakup/research.md`** — one-line `_sessions`
  note so it doesn't read as live state.
- **`docs/tickets/INDEX.md`** — 007 row → `in progress` now, `done` at close.
- **Append-only / frozen, do NOT touch:** ADRs 0001/0004, `docs/archive/*`,
  `001-*`, closed sprint files.

## Out of scope

- Advisor's one-shot `claude -p` — stays.
- A real adapter-based capacity cap / preemption — future work (the dropped
  enforcement is documented in ADR 0007, not replaced here).
- `entity.session_id` transcript-resume — untouched.
- Any roadmap edit (high-altitude; updated at phase close, not in this PR).

## Behaviour delta (NOT a pure no-op — see ADR 0007)

Re-pointing capacity/status onto `_adapters` makes three currently-lying signals
truthful (scheduler `free_slots`, heartbeat "N running", dashboard `alive`). No
Entity execution path changes. Hard capacity enforcement + preemption are
dropped (were non-functional under PTY). This is the one place 007 deviates from
the sprint's "zero behaviour change" line, by necessity.
