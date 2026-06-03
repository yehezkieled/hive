# 007 — Outline (implementation structure)

One cohesive change (DIRECT lane → single PR). The commits below map 1:1 onto
the ordered steps in [`design.md`](design.md); the suite is green after each.

## Commit sequence

```
 ┌─ c1  TEST REBASE (headless path still present → suite green throughout)
 │     + tests/conftest.py: FakeAdapter + inject_adapter helper
 │     ~ test_process_manager.py: ClaudeSession-mock sites → inject_adapter(FakeAdapter)
 │     ~ test_auto_retrieve / test_peer_messaging / test_advisor_mcp / test_lifecycle_manager
 │     - test_claude_session.py            (delete — module still exists, but its
 │                                           coverage moves; keep until c5? NO: keep
 │                                           file until claude_session.py is removed)
 │     - test_preempt.py, TestPreemption   (delete — feature removed in c3)
 │   ↳ gate: full suite green on the FakeAdapter, headless code untouched
 │
 ├─ c2  REPOINT capacity/status onto _adapters
 │     ~ manager.py active_count(:298) → sum is_alive() over _adapters   (PARENS!)
 │     ~ manager.py get_status(:482) / health_check(:503) → _adapters.get(name)
 │   ↳ gate: scheduler/heartbeat/dashboard now read live adapters; suite green
 │
 ├─ c3  DELETE the dead lifecycle
 │     - lifecycle_manager.py spawn_entity(:299) + _preempt_for_priority(:214)
 │     - manager.py spawn_entity(:315) + _preempt delegate(:301)
 │     ~ kill_entity / stop_all: drop _sessions block, keep _adapters block
 │     - manager.py self._sessions(:124)  ← LAST data member to drop
 │   ↳ gate: suite green
 │
 ├─ c4  ADAPTER → PTY-only
 │     ~ lifecycle_manager._get_or_create_adapter: drop HIVE_USE_PTY / use_pty /
 │       session_factory / initial_session_id branches; unconditional PTY
 │     - claude_adapter.py: ClaudeSession + DANGEROUS_MODES imports, SessionFactory,
 │       _default_session_factory, _build_args, _send_via_subprocess, session_id
 │       property, ctor use_pty/session_factory/initial_session_id + fields
 │     ~ claude_adapter.py: de-branch start / is_alive / send_turn
 │     ~ test_claude_adapter.py: drop subprocess tests, keep PTY (mock PtySession)
 │   ↳ gate: suite green
 │
 ├─ c5  KILL THE FLAG + the shim residue + the module
 │     - config.py HIVE_USE_PTY(:185) + PRIORITY_PREEMPT_ENABLED
 │     - manager.py ClaudeSession import(:57) + HIVE_USE_PTY re-export(:42)
 │     ~ lifecycle_manager _manager_module: drop ClaudeSession/HIVE_USE_PTY reads
 │       (KEEP ADVISOR_ENABLED / generate_mcp_config / ClaudeAdapter)
 │     - tests/conftest.py HIVE_USE_PTY pin(:22)        ← safe only after c1
 │     - git rm src/hive/process/claude_session.py
 │     - tests/test_claude_session.py (delete with its module)
 │     ~ test_thin_core_smoke.py: drop ClaudeSession + HIVE_USE_PTY parametrize rows
 │   ↳ gate: grep ClaudeSession / HIVE_USE_PTY = 0 in src/+tests/; suite green
 │
 └─ c6  DOCS + ADR (cross-cutting ✱)
       + docs/adr/0007-pty-only-runtime.md
       ~ docs/DEPLOYMENT.md (:32, :229, :1091 env row, :1108–1116 — advisor claude -p STAYS)
       ~ CONTEXT.md Interactive-gate definition (drop headless clause)
       ~ docs/tickets/004-manager-py-breakup/research.md (one-line _sessions note)
       ~ docs/tickets/INDEX.md (007 → done, at close)
     ↳ gate: ruff check + ruff format --check + full pytest -m "not integration"
```

> Refinement on c1/c5: `test_claude_session.py` and the subprocess half of
> `test_claude_adapter.py` import `ClaudeSession`, so they survive until the
> module is deleted in c5 — but `test_claude_session.py` covers *only* the
> dead module, so it can be `git rm`'d as early as c4/c5. Keep the import-bearing
> tests valid until the symbol they import is removed, to keep each commit
> collectable.

## Module-by-module after-state

- **`claude_session.py`** → gone.
- **`claude_adapter.py`** → one turn path (`_send_via_pty`), no flags, no
  factory; `ClaudeAdapterConfig` + `_build_pty_*` unchanged.
- **`lifecycle_manager.py`** → no `spawn_entity`/preempt; `_get_or_create_adapter`
  is plain PTY; `_manager_module` keeps only `ADVISOR_ENABLED` /
  `generate_mcp_config` / `ClaudeAdapter`.
- **`manager.py`** → no `_sessions`/`ClaudeSession`/`HIVE_USE_PTY`;
  `active_count`/`get_status`/`health_check` read `_adapters`; docstrings
  reworded off "subprocess" onto "PTY adapters".
- **`config.py`** → no `HIVE_USE_PTY` / `PRIORITY_PREEMPT_ENABLED`;
  `MAX_CONCURRENT_SESSIONS` stays.

## Risk-ordered watch-list (from research §7)

1. **`is_alive` property→method** — add `()` at every repoint (c2). #1 silent-bug risk.
2. **conftest pin before rebase** — c1 must precede c5's pin removal, or tests hang on a real PTY.
3. **`_state_lock` scope** — repoints (c2) swap the lookup inside the loop, never the lock scope.
4. **`_manager_module` partial trim** — keep the 3 non-headless reads (c5).
