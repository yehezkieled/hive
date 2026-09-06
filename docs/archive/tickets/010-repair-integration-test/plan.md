# Plan — Ticket 010: Repair the stale lead-worker integration test

Direct lane — one PR, two files. Re-base the test onto the `FakeAdapter`
seam, drop the `integration` marker so CI runs it, and correct the
marker description.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `tests/integration/test_lead_worker_roundtrip.py` | modify | Rewrite per `design.md`: facade setup (`register_maestro`/`create_team`/`spawn_worker`), inject `FakeAdapter` via `using_adapter` around `send_to_entity`, deterministic `<hive_actions>` JSON reply, keep the worker→own-lead routing + delivery + `peer_message_sent` audit assertions. Remove `@pytest.mark.integration`. Rewrite the module docstring (no more "real `claude -p`"). |
| `pyproject.toml` | modify | Replace the `integration` marker description — drop the `claude -p` wording; describe real-external tests going forward (marker stays declared, reserved). |

## Verification

- `.venv/bin/python -m pytest tests/integration/test_lead_worker_roundtrip.py -v`
  → passes, hermetic (no real `claude` spawned; the `_no_real_pty`
  guard would raise if one were requested).
- `.venv/bin/python -m pytest -m "not integration"` → now **collects and
  passes** this test (it's unmarked); full suite green.
- `grep -n "claude -p" pyproject.toml` → no match.
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.

## Out of scope

- Changing CI's `-m "not integration"` filter or adding an integration
  CI job (the marker stays reserved; that's a separate decision).
- The coverage ratchet — Ticket 011.
- Any production code under `src/` — test + config only.

## Cross-cutting impact

- None. No reference docs (`README`, `DEPLOYMENT.md`, `ARCHITECTURE.md`,
  ADRs) touched; `pyproject.toml` is project config.
