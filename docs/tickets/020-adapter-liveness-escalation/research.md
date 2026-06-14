# 020 — Research

Findings from an 8-seam code exploration plus direct reads. Every claim
carries a file:line ref. Where a fact is an undocumented Claude Code
interface, it is flagged `CONFIRM AT BUILD`.

## R1 — Where a stalled turn surfaces (the bounce hook-point)

A turn's `TimeoutError` propagates **uncaught** through the whole send
stack today:

```
transcript_reader.await_next_assistant_turn   raises  transcript_reader.py:224
  → PtySession.send                           awaits  pty_session.py:343  (no catch)
  → ClaudeAdapter.send_turn                    awaits  claude_adapter.py:191 (no catch)
  → MessageDispatcher.send_to_entity           awaits  message_dispatcher.py:218 (no catch)
```

`send_to_entity` is the **single chokepoint** every entity turn flows
through, and it already hosts a recovery pattern with the exact shape we
need — **auto-compact** (`message_dispatcher.py:217-248`): guard on a
per-entity set (`_compacting`), do the recovery, `_notify` + `_audit`,
`finally` discard the guard. The bounce is a sibling of that block.

## R2 — The recovery mechanism (all primitives already exist)

| Step | Call | Ref |
|------|------|-----|
| Kill the frozen process | `ClaudeAdapter.stop()` → `PtySession.stop()` (`/exit`, then `terminate(force=True)`) | `claude_adapter.py:155`, `pty_session.py:266-276` |
| Evict from cache | `self._adapters.pop(name)` (cache is keyed by entity name) | `manager.py:121`; precedent in `kill_entity` `lifecycle_manager.py:379` |
| Respawn | `_get_or_create_adapter(entity)` — builds a fresh `ClaudeAdapter`, `start()`s it, re-caches | `manager.py:300` → `lifecycle_manager.py:243-279` |
| **Conversation continuity** | `--continue` is added automatically when the prior `.jsonl` exists | `pty_session._has_prior_session` / `_build_spawn_args` `pty_session.py:141-142` |
| Liveness probe | `ClaudeAdapter.is_alive()` → `PtySession.is_alive()` (`_proc.isalive()`) | `claude_adapter.py:160`, `pty_session.py:278` |

Continuity is **free**: the respawn finds the prior transcript on disk and
resumes it; no session-id threading needed. (Session pinning, ADR 0011,
re-pins against the new pid's state file automatically.)

`_notify(message, kind="info", data)` (`manager.py:506-517`) and
`_audit(action, target, details, actor="system")` (`manager.py:193-210`)
are the notify/audit helpers — both None-guarded, audit is
fire-and-continue.

## R3 — The safety checks (why the bounce is safe)

Three different things all look like "no progress" to the reader; only one
should be killed.

1. **Bridged plan/ask gate** — entity is waiting for the *user*.
   `is_parked_at_gate(name)` (`manager.py:307-320`) reads
   `GateCoordinator.pending_request_id`, the coordinator-owned source of
   truth (Ticket 028), non-None exactly between gate-park and gate-resume.
   Ticket 028's scheduler guard (`scheduler.py:224,244`) consults the same
   method before poking — 020 mirrors that exact check.

2. **Live Workflow run** — a Lead's own transcript is quiet but its
   Workflow journal is advancing. `workflow_active(window)` is the same
   predicate the reader uses to reset its deadline
   (`transcript_reader.py:220-221`, `claude_adapter.py:178-182`,
   `workflow_progress.run_active`).

3. **Genuine jam** (permission prompt / wedged TUI) — both checks clear.

**Key result — the permission prompt separates cleanly, for free.** The
detector (`gates.py:68-93`) only ever returns `kind="plan"` (plan_mode /
`ExitPlanMode`) or `kind="ask"` (`AskUserQuestion`). There is **no code
path that produces `kind="permission"`** — ADR 0005 deferred that detector
(#26 never shipped), because a permission prompt has no transcript
signature. `resolve()` (which populates `_pending`) only fires on a
detected gate (`pty_session.py:398`). So for a permission-prompt jam,
`is_parked_at_gate` is **False** — exactly the jam we want to kill — while
a real plan/ask gate reads **True** and is protected. The *absence* of a
registered gate is the signal; we never need to detect the prompt itself.

## R4 — Interaction with Ticket 030 (the dangerous one)

The timeout 020 counts is the **same** 180s no-progress timeout that 030
says false-fires on healthy long Workflow turns
(`transcript_reader.py:148-227`). If 020 bounced on a blind count, an
unfixed 030 would make it **kill healthy Leads mid-run**.

Mitigation is structural, not sequencing-dependent: safety check #2
(`workflow_active`) is a second guard on that exact false-positive. A
healthy Workflow turn either never raises (the reader resets its own
deadline at `:220-221`) or, if it does, is held off by 020's
`workflow_active` re-check. So 020 is robust **whether or not 030 has
landed** — 030-first is *cleaner*, not *required*.

## R5 — Interaction with Ticket 029 (in flight, separate session)

029 (maestro gate bridge) is being built in worktree
`graceful-sparking-sutherland`; its docs are merged (#144/#145, INDEX row
`in progress`). 020 and 029 edit **different neighborhoods**: 029 owns the
gate bridge (`gate_coordinator`, `approval_handler`, the gate path in
`pty_session`); 020's core edit is `message_dispatcher.send_to_entity` +
a `ProcessManager` state dict. The only overlap is the notification path,
which 020 only *calls* (`_notify`/`_audit`), never restructures.

020's safety-check #1 reads gate-registration state that 029 repairs.
Therefore 020 **depends only on the public `is_parked_at_gate` contract**,
never gate internals, and assumes **post-029 semantics** (a maestro parked
at a gate is registered). 029 *strengthens* the check; it does not
conflict. A regression test ("a gated maestro is never bounced") pins this
and guards against future regressions.

## R6 — The diagnosis sources (best-effort "why")

The session-state file `~/.claude/sessions/<pid>.json` is **already read**
for `sessionId` only — `_parse_session_id` (`pty_session.py:376-388`)
parses the JSON and ignores everything else. The same file carries
`status` and `waitingFor`; in the otter incident (ticket.md §Why) it read
`{"status":"waiting","waitingFor":"permission prompt"}`. ADR 0011 documents
the file shape (`{pid, sessionId, cwd, status, ...}`).

A bounce reason can be assembled best-effort, first hit wins:

```
1. sessions/<pid>.json status/waitingFor → "waiting on a permission prompt"
2. adapter.is_alive() is False           → "the claude process had crashed"
3. last transcript entry (what it did)   → "stalled after a Bash(...) call"
4. nothing conclusive                    → "no output for <N> min — cause unknown"
```

`CONFIRM AT BUILD`: the exact `waitingFor` vocabulary across jam types
(only `"permission prompt"` observed live). It is an undocumented CC
interface, guarded only by the version pin (Ticket 009). This is why it is
used **advisory-only** — see `design.md` §D5.

## R7 — Test seam

Hermetic tests have a ready home — no real PTY needed:

- `FakeAdapter` + `using_adapter()` (`tests/fakes.py:15-96`) inject a fake
  into `manager._adapters`, shadowing `_get_or_create_adapter`. The
  `_no_real_pty` autouse guard (`conftest.py:24-41`) fails any test that
  would spawn a real process.
- `FakeAdapter.send_turn` does **not** raise today — a `TimeoutError`-raising
  variant (e.g. a scripted sequence of timeouts then a success) must be
  added.
- Notification/audit assertions: recording-channel pattern
  (`tests/test_process_manager.py:23-31`,
  `tests/test_notification_dispatcher.py:11-23`) and
  `audit_log.recent(action_prefix=...)` (`test_process_manager.py:107-120`).
- `TimeoutError` is already simulated in reader tests
  (`tests/runtime/test_transcript_reader.py:304-316`).

## R8 — Out-of-scope root cause (noted, not built)

The deepest fix for the credential/permission class is **per-Entity
credential isolation** (eliminates the rotation race at the source). That
is Phase 5 adapter-layer territory and a much larger change; 020 is the
**generic recovery net** that mitigates *any* jam, not the root-cause fix.
022 separately prevents the *specific* permission-prompt trigger.
