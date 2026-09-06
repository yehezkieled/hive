# 007 — Research (code-grounded findings)

All line references are against the **post-004** tree (the manager breakup
landed first — slices #41–#45 — so 007 edits the new module layout). Findings
were independently verified by a 7-agent map-and-verify pass (5 surface
mappers + 2 adversarial skeptics); the adversarial verdicts are in §6.

---

## 1. The headless path is two machines, not one

`ClaudeSession` is referenced by **two separate concerns**. Only the first is
"the runtime path" the ticket names; the second is older and only entangled
because it is *typed on* `ClaudeSession`.

```
 Concern                              Where (post-004)                 In PTY prod
 ──────────────────────────────────   ──────────────────────────────   ───────────
 1. Adapter headless TURN path        claude_adapter.py                DEAD
    use_pty=False, _send_via_           :55/65 use_pty, :208 subproc,    (use_pty
    subprocess, _build_args,            :75 _build_args, :35/38          always True
    session_factory, --resume           SessionFactory                   in prod)

 2. Legacy entity LIFECYCLE           lifecycle_manager.py             DEAD
    spawn_entity → _sessions,           :299 spawn_entity, :369 write,   (_sessions
    active_count, preemption,           :214 _preempt; manager.py        always empty)
    status/health "alive"               :298 active_count, :482/503
                                        get_status/health_check
```

**Why deleting `ClaudeSession` forces touching machine 2:** the acceptance
criterion `grep ClaudeSession = 0` cannot be met while `_sessions:
dict[str, ClaudeSession]` (manager.py:124) and `spawn_entity` (returns
`ClaudeSession`) still exist. So 007's blast radius *must* include machine 2.

---

## 2. Machine 2 is dead in PTY production (the load-bearing premise)

Verified, with evidence:

- **`_sessions` has exactly one writer:** `lifecycle_manager.py:369`
  (inside `spawn_entity`). Guard read at `:329`. No other writer in `src/`.
- **`spawn_entity` has zero production callers.** `grep '.spawn_entity('`
  across `src/` + `tests/` → only `manager.py:316` (an unused facade
  delegate) and three test files. Production spawn goes through
  `spawn_worker` (lifecycle_manager.py:482) / `create_team` / 
  `register_maestro`, all of which **only register the entity IDLE** and
  return — their own docstrings say "stays IDLE until … `send_to_entity`".
- **The live turn path never touches `_sessions`.** `send_to_entity`
  (message_dispatcher.py:86) → `_get_or_create_adapter`
  (lifecycle_manager.py:387) → writes `_adapters`, builds a **PTY**
  `ClaudeAdapter`. `HIVE_USE_PTY=true` confirmed in the host
  `~/projects/hive/.env`.

**Consequence:** in production `_sessions` is permanently empty, so:

| Signal | Reads | Production value today |
|--------|-------|------------------------|
| `active_count` (manager.py:298) | `_sessions` | always **0** |
| scheduler `free_slots` (scheduler.py:113) | `active_count` | always "all slots free" |
| `get_status[].alive` (manager.py:497) | `_sessions` | always **False** |
| heartbeat "N running" (telegram/bridge.py:381) | `get_status.alive` | always **0** |
| preemption (`_preempt_for_priority`, lifecycle_manager.py:214) | `active_count` | never fires |

These are **latent, already-broken signals from the Phase-1 migration** — the
capacity/status reporting was never re-pointed from the headless `_sessions`
onto the PTY `_adapters`. 007 inherits the decision of what to do with them.

---

## 3. What 004 left behind (must unwind)

004 relocated machine 2 into `lifecycle_manager.py` **without** changing
behaviour, and added two things 007 has to reverse:

1. **The `_manager_module()` test-compat shim** (lifecycle_manager.py:52).
   The moved code reads `ClaudeSession` / `HIVE_USE_PTY` / `ClaudeAdapter` /
   `ADVISOR_ENABLED` / `generate_mcp_config` **off the `hive.process.manager`
   module at call time**, purely so `patch("hive.process.manager.X")` in
   existing tests still bites. `manager.py:57` imports `ClaudeSession` and
   `manager.py:42` re-exports `HIVE_USE_PTY` (`noqa F401`) only to feed this
   shim. Once tests stop patching `ClaudeSession`/`HIVE_USE_PTY`, those two
   reads + re-exports are dead and must go. **Do not delete `_manager_module`
   wholesale** — `generate_mcp_config`/`ADVISOR_ENABLED`/`ClaudeAdapter` still
   flow through it (`test_advisor_mcp` patches those).
2. **`test_thin_core_smoke.py`** asserts the manager module still exports
   `ClaudeSession` and `HIVE_USE_PTY` (parametrize lists at :127, :136). Both
   entries must be removed or the file goes red.

---

## 4. The change surface (105 sites; grouped)

Full per-site table archived from the map pass. Summary by file:

**`src/hive/runtime/claude_adapter.py`** — PTY-only after 007.
Remove: `ClaudeSession` import (:12), `DANGEROUS_MODES` import (:11, only used
by `_build_args`), `SessionFactory` (:35), `_default_session_factory` (:38),
ctor params `session_factory`/`initial_session_id`/`use_pty` + their fields,
`_build_args` (:75), `_send_via_subprocess` (:208), `session_id` property
(:234, **provably dead** — no caller reads `adapter.session_id`).
Rewrite (de-branch): `start` (:159), `is_alive` (:180), `send_turn` (:185).
Keep: `_send_via_pty`, `_build_pty_*`, `stop`, `ClaudeAdapterConfig`.

**`src/hive/process/lifecycle_manager.py` + `manager.py`** — see §2/§3.
Delete `spawn_entity` (both), `_preempt_for_priority` (both), `self._sessions`
(manager.py:124), `ClaudeSession` import + `HIVE_USE_PTY` re-export. Repoint
`active_count`/`get_status`/`health_check` onto `_adapters`. Rewrite
`_get_or_create_adapter` PTY-only. Drop `_sessions` handling from
`kill_entity`/`stop_all` (keep the `_adapters` handling).

**`src/hive/config.py`** — delete `HIVE_USE_PTY` (:185). `PRIORITY_PREEMPT_ENABLED`
becomes dead (only readers are the deleted preemption). **`MAX_CONCURRENT_SESSIONS`
/ `max_sessions` SURVIVE** — still read by `scheduler.py:113/165` as an
informational planning input; the *enforcement* (in `spawn_entity`) is what goes.

**`tests/conftest.py`** — remove the `HIVE_USE_PTY=false` pin (:22). **Hard
prerequisite:** the suite must already run on a mocked harness before this line
goes, or every `send_to_entity` test spawns a real `claude` PTY and hangs on
`read()` (the exact failure the pin's comment warns about).

**Docs** — `docs/DEPLOYMENT.md` (4 spots: :32, :229, the `HIVE_USE_PTY` env-var
row :1091, the `claude -p` runtime notes :1108–1116). `CONTEXT.md` Interactive-gate
definition (drops the "resolved non-interactively under headless `claude -p`"
clause). **No `ARCHITECTURE.md`** exists (ticket/sprint name it speculatively).
Leave `docs/archive/*`, `001-*`, frozen sprints, and ADRs 0001/0004 alone.

---

## 5. The test rebase (the crux)

The unit suite runs *on* the headless branch today: ~30 sites in
`test_process_manager.py` patch `hive.process.manager.ClaudeSession` and rely
on `HIVE_USE_PTY=false` so `_get_or_create_adapter` takes the subprocess path.

**Chosen seam: the `ClaudeAdapter` boundary** (`_get_or_create_adapter`) — the
manager's natural collaborator interface, already the injection point. A shared
`FakeAdapter` (PTY-shaped: `async start/stop`, `is_alive()` **method**,
`async send_turn(prompt)->(text, usage)`, `session_id` property) replaces the
per-test `ClaudeSession` mock. The dispatcher only consumes `send_turn`'s
return (`usage['session_id']` at message_dispatcher.py:235, `usage['input_tokens']`
at :209), so the fake's surface is small. Fixture sketch + a before/after
rewrite live in [`design.md`](design.md).

Per-file fate: `test_claude_session.py` → **delete** (module gone).
`test_preempt.py` + `test_process_manager.py::TestPreemption` → **delete**
(feature removed; a stub would assert nothing). `test_claude_adapter.py` → drop
the subprocess tests, keep the PTY tests (mock `PtySession`).
`test_auto_retrieve.py`, `test_peer_messaging.py`, `test_advisor_mcp.py`,
`test_lifecycle_manager.py` → rebase onto `FakeAdapter`. `test_thin_core_smoke.py`
→ update the two parametrize assertions.

---

## 6. Adversarial verdicts

Two skeptics were tasked to **refute** the design's premises.

**UPHELD (high confidence) — "machine 2 is dead in production."** Every
channel traced: only writer is `spawn_entity` (no prod caller); the only path
to `EntityState.RUNNING` is `spawn_entity` (also dead); prod runs on
`_adapters`. The one consumer of session-backed `alive`, the heartbeat
(bridge.py:381), builds a *display string* only — no control flow. Safe to
remove.

**REFUTED (high confidence) — "re-pointing is behaviour-preserving."** This is
the important catch. Re-pointing `active_count`/`alive` from the always-empty
`_sessions` onto the live `_adapters` is **not** behaviour-neutral:

- `scheduler.py:113` `free_slots` flips from a constant ("all slots free") to a
  real number → the **maestro-facing capacity prompt changes**.
- `telegram/bridge.py:381` heartbeat flips from always-"0 running" to the real
  live count.
- `get_status[].alive` (web dashboard, dispatch, telegram) flips from
  always-False to true.

The skeptic's *specific worry* — that `health_check` re-pointed onto `_adapters`
would wrongly flip an IDLE-registered entity (no adapter yet) to `ERROR` — is
**not** a bug (health_check only flags entities already in `RUNNING`). But the
reporting change is real.

**Implication:** 007 **cannot** be "zero behaviour change" while satisfying
`grep ClaudeSession = 0`. Deleting `_sessions` forces its readers onto
`_adapters`, which makes a set of *currently-lying* signals truthful. The only
alternative — deleting the reporting features outright — is a larger behaviour
change. So 007 makes the capacity/status reporting **correct**, as a
deliberate, documented delta. This is recorded in
[ADR 0007](../../adr/0007-pty-only-runtime.md) and called out in
[`design.md`](design.md) §"Behaviour delta".

---

## 7. Continuity & safety notes

- **PTY resume is NOT lost.** Deleting the adapter's `initial_session_id` /
  `--resume` is safe: PTY continuity comes from `pty_session._build_spawn_args`'
  `--continue` (pty_session.py:78–79), independent of the deleted logic.
  `entity.session_id` (the transcript-derived id, models/entity.py:184,
  read/written by message_dispatcher) is a **separate** concern that **stays**.
- **`is_alive` shape hazard (highest-risk edit).** `ClaudeSession.is_alive`
  was a **property** (used without parens at manager.py:299/497/515,
  lifecycle:329); `ClaudeAdapter.is_alive()` is a **method**. Every repoint
  must add `()`. A slip makes a bound method (always truthy) report every
  adapter alive — silent corruption of `get_status`/`health_check`.
- **`_state_lock` discipline (004-flagged).** Sync `active_count`/`get_status`
  never took the async lock; `health_check` holds it only around the
  `_entities` snapshot. Repointing swaps the per-name lookup inside the loop,
  not the lock scope — preserve exactly.
