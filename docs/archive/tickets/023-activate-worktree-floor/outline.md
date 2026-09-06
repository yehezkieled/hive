# 023 — Outline (implementation structure)

Approach in [`design.md`](design.md); pinning decision in
[ADR 0011](../../adr/0011-session-pinning-over-directory-heuristics.md).
Four independent seams; each is green on its own.

## Seam map

```
c1 FLOOR + FACTORY   build_process_manager() extracted from __main__
   (D1a, D5, D6)     constructs WorktreeManager(PROJECT_ROOT, WORKTREES_DIR)
                     unit test: production composition has a real worktree_mgr
                     + the maestro-yolo shadow comment (D6, doc-only)

c2 SESSION PINNING   pty_session: knows child pid → reads
   (D1b, ADR 0011)     ~/.claude/sessions/<pid>.json → sessionId
                     transcript_reader: pinned-path mode; heuristic = fallback
                     re-pin on every PTY respawn (pin is per-process)

c3 MESSAGING         _handle_actions: alias maestro/parent → resolve;
   SELF-HEAL           rejection → audit(action_rejected) + system note
   (D2, D3)            queued to SENDER → wake
                     spawn/kickoff failure → system note to the MAESTRO

c4 TURN-END          send_to_entity completion: router.has_pending(name)
   INBOX CHECK         → WakeScheduler wake (budget-respecting)
   (D4)
```

## c1 — Floor + factory

- **`src/hive/__main__.py`**: extract the `ProcessManager` composition
  (~lines 187-203) into `build_process_manager(...)` (new module or
  `__main__`-adjacent factory; keep `__main__` a thin caller).
  Construct `WorktreeManager(config.PROJECT_ROOT, config.WORKTREES_DIR)`
  and pass it — first-ever import of `WORKTREES_DIR` (`config.py:58`).
- **`src/hive/process/lifecycle_manager.py:211`**: comment documenting
  the intentional maestro-`yolo` shadow (D6). No behavior change.
- Tests: factory returns a manager whose `worktree_mgr` is a real
  `WorktreeManager` pointed at config paths; existing
  fake-injection tests untouched.
- The three dormant guards (`lifecycle_manager.py:255,311,392`) go
  live; their behavior is already covered by 015's tests.

## c2 — Session pinning

- **`src/hive/runtime/pty_session.py`**: after spawn, poll
  `~/.claude/sessions/<pid>.json` (short timeout) for `sessionId`;
  expose the pinned transcript path; re-pin on respawn/`--continue`
  (new pid ⇒ new pin).
- **`src/hive/runtime/transcript_reader.py`**: accept a pinned path;
  `identify_session` becomes the fallback only — a fallback bind logs
  loudly (`WARNING: session pin unavailable, falling back to
  directory heuristic`).
- Tests: fake pid-state file → reader reads the pinned transcript even
  while a decoy `.jsonl` grows in the same dir (the F3 reproduction);
  missing file → fallback + warning; respawn → re-pin.

## c3 — Messaging self-heal

- **`src/hive/process/message_dispatcher.py`** (`_handle_actions`,
  message branch ~:282-322):
  - resolve `to:"maestro"` → first dotted segment of sender;
    `to:"parent"` → sender name minus last segment (maestro parent =
    none → reject). Resolution happens before the entity lookup;
    permissions unchanged (they see the resolved name).
  - on rejection (unknown recipient, permission denied): audit
    `action_rejected` + `router.route("system", sender, "<what failed,
    correct form>")` → existing wake delivers it.
- **`src/hive/process/wake_scheduler.py`** (`_auto_kickoff`) +
  **`lifecycle_manager`** spawn path: on kickoff/spawn failure, route a
  system note to the owning maestro (D3) instead of log-only.
- **`personalities/role-lead.md` / `role-worker.md`**: teach the alias
  ("address your maestro as `maestro`") — small JD edit.
- Tests: alias resolution (lead→maestro, worker→parent, maestro
  self-alias rejected with feedback); rejection produces audit + sender
  note; kickoff failure produces maestro note. Reuse the
  fail-fast fakes from `test_message_dispatcher.py`.

## c4 — Turn-end inbox check

- **`src/hive/process/message_dispatcher.py`** (`send_to_entity`,
  completion path): after a turn completes (success or gate-resume),
  if `router.has_pending(entity_name)` → schedule a wake via
  `WakeScheduler` (same budget; a throttled check is safe — the 120m
  tick remains the backstop).
- Tests: queue mail while a fake adapter is mid-turn → wake skipped
  (existing) → turn completes → wake scheduled exactly once; empty
  queue → no wake; budget exhausted → no spin.

## Slice independence

c1–c4 are logically independent (no slice needs another's output).
c3 + c4 touch `message_dispatcher.py` — merge-order territory, not a
blocker. D3 rides c3 because it *uses* c3's feedback channel.

## Verification gate (every slice)

`ruff check src/ tests/ && ruff format --check src/ tests/` (separate
gates) + `pytest -m "not integration"` (75% floor).

Live smoke (after all slices, deployed): the 015 DoD run — a
maestro→lead→leaf turn where the lead's worktree shows in
`git worktree list`, `/proc/<pid>/cwd` ≠ repo root, the lead's
proposal arrives addressed as `maestro`, and the main checkout stays
clean.
