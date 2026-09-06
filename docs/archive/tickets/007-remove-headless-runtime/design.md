# 007 — Design (chosen approach)

Seeded by [`ticket.md`](ticket.md); grounded in [`research.md`](research.md).

## Decision in one line

Commit to **PTY-only**: delete the headless turn path, the `ClaudeSession`
wrapper, the `HIVE_USE_PTY` flag, and the dead headless-era entity lifecycle
(`spawn_entity`/`_sessions`/preemption); **re-point** the capacity/status
signals onto the live `_adapters` dict; rebase the unit suite onto a mocked
adapter. Recorded in [ADR 0007](../../adr/0007-pty-only-runtime.md).

## Chosen approach: Option A (re-point)

Deleting `ClaudeSession` forces a choice about machine 2 (the dead lifecycle).
Three were considered:

| Option | What happens to machine 2 | Verdict |
|--------|---------------------------|---------|
| **A. Re-point** (chosen) | delete `spawn_entity`/`_sessions`/preempt; `active_count`/status read `_adapters` | Satisfies `grep=0`; keeps the scheduler capacity line + dashboard meaningful (now truthful). |
| **B. Remove reporting too** | also delete `active_count` + the scheduler `free_slots` line + heartbeat count + `get_status.alive` | Larger behaviour change; throws away signals the VPS wants. Rejected. |
| **C. Freeze behaviour exactly** | keep `_sessions` typed on `ClaudeSession` | Impossible — the class is being deleted. Rejected. |

A is the least-disruptive option that still satisfies the acceptance criteria.

## Behaviour delta (must be acknowledged)

The sprint says 007 is "zero behaviour change." Adversarial verification
([research.md §6](research.md)) showed that is **not achievable**: re-pointing
turns three *currently-lying* signals truthful —

- scheduler `free_slots`: always "all free" → real live-adapter count;
- Telegram heartbeat "N running": always 0 → real count;
- dashboard/`get_status.alive`: always False → real liveness.

No **Entity execution** path changes — only the reporting accuracy. We treat
this as fixing a latent Phase-1 migration bug (the signals were never moved off
the headless `_sessions`), and document it in the ADR rather than pretending it
away. **Hard capacity enforcement + preemption are dropped** — they were
non-functional under PTY (cap never fired because `active_count` was always 0);
a real adapter-based cap is future work, not 007.

`max_sessions` / `MAX_CONCURRENT_SESSIONS` **stay** as an informational planning
input (scheduler still reports `N/max free`). `PRIORITY_PREEMPT_ENABLED` is
deleted with preemption.

## Test rebase: `FakeAdapter` at the `_get_or_create_adapter` seam

The suite currently runs *on* the headless branch (mocks `ClaudeSession`, pinned
`HIVE_USE_PTY=false`). After 007 the only turn path is PTY, so the mock must
move. We mock at the **`ClaudeAdapter` boundary**, not `PtySession` — the
adapter is the manager's natural collaborator interface and already the
injection point. Manager/lifecycle unit tests care about orchestration
(draining, action routing, token persistence), not PTY internals.

```python
# tests/conftest.py (shared)
class FakeAdapter:
    """PTY-shaped test double for ClaudeAdapter — no subprocess."""
    def __init__(self, responses="ok", *, session_id="sess-1", usage=None):
        self._responses = responses if isinstance(responses, list) else [responses]
        self._i = 0
        self._session_id = session_id
        self._usage = usage or {}
        self.started = self.stopped = False
        self.prompts: list[str] = []                 # capture for assertions
    async def start(self): self.started = True
    async def stop(self): self.stopped = True
    def is_alive(self): return self.started and not self.stopped   # METHOD
    @property
    def session_id(self): return self._session_id
    async def send_turn(self, prompt):
        self.prompts.append(prompt)
        text = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "session_id": self._session_id, **self._usage}
        return text, usage

def inject_adapter(mgr, adapter):
    async def _get(entity):
        mgr._adapters[entity.name] = adapter           # so kill/stop_all see it
        return adapter
    mgr._get_or_create_adapter = _get
```

Before → after (a `test_process_manager.py` action-routing test):

```python
# BEFORE
instance = self._mock_session(response_text)           # AsyncMock ClaudeSession
with patch("hive.process.manager.ClaudeSession", autospec=True) as mock_cls:
    mock_cls.side_effect = lambda args, **kw: instance
    result = await manager.send_to_entity("dev", "Review the project")

# AFTER
inject_adapter(manager, FakeAdapter(response_text))
result = await manager.send_to_entity("dev", "Review the project")
```

`TestAutonomousDispatch`'s ~16 tests follow by rewriting their single `_send`
helper. `TestCompactEntity`/`TestAutoCompact` pass a **list** of responses and
`usage={"input_tokens": 60000}` to trip the compaction threshold.

Deleted, not rebased: `test_claude_session.py` (module gone); `test_preempt.py`
+ `TestPreemption` (feature removed — a stub asserts nothing real).

## Ordering (each step independently importable / green)

1. Land the `FakeAdapter` fixture + rebase every `ClaudeSession`-mocking test
   onto it **while the headless path still exists** (suite stays green).
2. Repoint `active_count`/`get_status`/`health_check` onto `_adapters`
   (**add `()` — `is_alive` is now a method**).
3. Delete `spawn_entity` + `_preempt_for_priority` (both facade + collaborator)
   together (preempt's only caller is `spawn_entity`).
4. Drop `_sessions` handling from `kill_entity`/`stop_all`; delete the
   `self._sessions` declaration **last** (it must outlive every reader).
5. Make `_get_or_create_adapter` unconditionally PTY; strip the adapter's
   `use_pty`/subprocess members; remove `manager.py`'s `ClaudeSession` import +
   `HIVE_USE_PTY` re-export; trim the `_manager_module` shim's
   `ClaudeSession`/`HIVE_USE_PTY` reads (keep `ADVISOR_ENABLED` /
   `generate_mcp_config` / `ClaudeAdapter`).
6. Delete `HIVE_USE_PTY` + `PRIORITY_PREEMPT_ENABLED` from config; remove the
   conftest pin (**only now** — step 1 made it safe).
7. `git rm src/hive/process/claude_session.py`; update `test_thin_core_smoke.py`.
8. Reference-doc edits (DEPLOYMENT.md, CONTEXT.md) + ADR 0007.

## Side effects

- **ADR:** new `docs/adr/0007-pty-only-runtime.md` (next number; 0001–0006
  exist). Records the one-way-door PTY-only decision, the dropped
  enforcement/preemption, and the reporting behaviour delta.
- **CONTEXT.md (glossary):** the **Interactive gate** definition drops its
  trailing "...resolved non-interactively under headless `claude -p`" clause —
  that mechanism no longer exists. Glossary-only edit; no implementation detail.
- **No new glossary term.** The PTY-only commitment is a *decision* (ADR), not a
  term; CONTEXT.md stays a pure glossary.
- **004 cross-reference:** add a one-line note to
  `docs/tickets/004-manager-py-breakup/research.md` so its `_sessions`
  references don't read as live state.

## Non-goals

- The advisor's one-shot `claude -p` (`mcp/advisor_server.py`) — a separate raw
  subprocess; **stays**.
- A real adapter-based capacity cap / preemption — future work.
- `entity.session_id` transcript-resume logic — untouched.
