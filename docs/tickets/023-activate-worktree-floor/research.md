# 023 — Research

Answers to [`questions.md`](questions.md), grounded in code, the live
audit log (Postgres `audit_log` + `messages`), the service journal, and
the smoke-test leads' own CC transcripts. Researched 2026-06-11.

## Headline findings

1. **The floor wiring is a ~3-line DI fix.** Everything already exists:
   `config.py:58` defines `WORKTREES_DIR` (unused — never imported
   anywhere), the directory exists on disk (empty), and `worktree_mgr`
   is already a `ProcessManager.__init__` parameter defaulting to
   `None` (`manager.py:84-105`). `__main__.py:187-203` just never
   constructs/passes it.
2. **The ticket's "lead→maestro delivery failed" premise is wrong in
   mechanism.** The smoke test failed three different ways across three
   attempts — and **none of them is a router-delivery gap**. See the
   incident timeline below. Critically, one of the three (silent
   transcript mis-binding) is **fixed by the worktree floor itself**,
   which upgrades 023 from "safety fix" to "correctness fix for turn
   reading".
3. **Wake-on-inbound is already enabled in production**
   (`__main__.py:218` calls `enable_wake_on_inbound()`), contrary to
   what a code-only reading suggests. The wake path has a real but
   *latent* weakness (single-shot, no retry on turn end) that did not
   cause this incident.
4. **The defaults audit is mostly already done** (PR #90 fixed the only
   live shadow bug). What remains is small: one intentional shadow to
   document, one hardcoded constant to consider lifting.

## Incident timeline — what actually happened (2026-06-11 UTC)

Three team-spawn attempts during the 015 live smoke; three distinct
failures:

```
ATTEMPT 1  strutils, 01:39  (service 2981648, lead model=sonnet pre-#90)
  01:39:30  create_team ok → lead PTY spawn
  01:39:31  PtySession: proc dead, retry 2s ─┐
  01:39:34  proc dead, retry 4s              ├─ 3 spawn failures
  01:39:38  proc dead, retry 8s             ─┘
  01:39:47  "auto-kickoff for otter.strutils failed: failed to recover
             after 3 attempts"          ← lead is STILLBORN
  02:13:48  idle-reaper kills it, silently. Maestro/user never told.
  FAILURE CLASS F1: spawn failure has no escalation path.

ATTEMPT 2  strutils, 02:31  (service 3044494, lead model=sonnet pre-#90)
  02:31:32  lead PTY up, kickoff ok, lead works
  02:34:43  lead emits breakdown → to: "maestro.strutils"   ← WRONG NAME
  02:34:43  message_dispatcher: "Unknown recipient: maestro.strutils"
             → logger.warning + continue  (message_dispatcher.py:284-286)
             No audit. No feedback to the lead. Lead believes it reported.
  03:02:34  idle-reaper kills the lead waiting for an approval that
             can never come.
  FAILURE CLASS F2: misaddressing is dropped silently — no alias
  resolution, no sender feedback. (The kickoff context DID say
  "Direct parent: otter"; the model still invented "maestro.strutils".
  A JD-only fix is demonstrably insufficient.)

ATTEMPT 3  optest, 02:58  (service 3058797, lead model=opus post-#90)
  02:58:08  lead PTY up (cwd = /home/hezki/projects/hive — NO floor)
  02:58:13  kickoff lands; lead reads contract, maps env, drafts plan
  02:59:04  lead's turn COMPLETES (CC logs turn_duration 50964ms) with a
             PERFECT <hive_actions> proposal addressed to "otter"
             (transcript 96c4d1a7…jsonl, project dir
             ~/.claude/projects/-home-hezki-projects-hive/)
  …         Hive routes NOTHING. No router log, no audit, no error,
             no timeout. The proposal is never seen.
  03:32:35  idle-reaper kills the lead.
  FAILURE CLASS F3: silent transcript mis-bind (see below).
```

Audit-log corroboration: `peer_message_sent` and `entity.wake_scheduled`
last fired 2026-06-03; the 06-11 window has only `create_team`/
`spawn_team` events. The `messages` table holds **no** row from either
lead — and `router.route()` persists *before* the queue check
(`router.py:64`), so neither lead's reply ever reached the router.

## F3 — the transcript mis-bind (shared project dir)

CC writes transcripts to `~/.claude/projects/<cwd-slug>/<session>.jsonl`.
With the floor inert, **every entity runs with the same cwd** (the live
checkout), so the maestro and all leads share ONE project dir. Hive
binds an adapter to "its" transcript with a heuristic
(`transcript_reader.py:60-96`): snapshot `*.jsonl` sizes pre-spawn, then
take the **first file that is new or has grown**:

```
shared ~/.claude/projects/-home-hezki-projects-hive/
   ├── otter (maestro) session.jsonl   ← actively growing (chatting)
   └── otter.optest session.jsonl      ← appears ~5s after PTY spawn

lead's reader snapshots → polls → maestro's file grew first?
   → binds the LEAD's adapter to the MAESTRO's transcript
   → every "turn" the lead's adapter reads is a maestro turn
   → the lead's real output is never read; no error is ever raised
```

This is the **mis-attribution race 015's research predicted**
(015 `research.md` §Turn model) — now observed live. The exact
micro-path inside attempt 3 (mis-bind at `identify_session` vs
mis-accept inside `await_next_assistant_turn`) is **CONFIRM IN CODE**
during implementation; the structural ambiguity is proven by
construction, and only exists because the floor is inert.

**The floor fixes F3 at the root**: per-entity worktree cwd → per-entity
`<cwd-slug>` → an entity's project dir contains only its own sessions →
the new-or-growing heuristic is unambiguous again (it was written when
Workers always got worktrees — the missing wiring created the shared-dir
case it never had to handle).

## Question-by-question answers

### Q1 — WorktreeManager constructor & config (CONFIRMED)

`WorktreeManager(repo_path: Path, worktree_dir: Path)`
(`worktree.py:15-18`); creates `worktree_dir/<entity_name>/` per entity,
branch per entity. `config.py:58` already defines
`WORKTREES_DIR = PROJECT_ROOT / "worktrees"` and even `mkdir`s it at
import (`config.py:64`) — **but no code imports it**. The live dir
`/home/hezki/projects/hive/worktrees/` exists, empty. No
`HIVE_WORKTREES_DIR` env override exists yet (decide in design whether
to add one for parity with other knobs).

### Q2 — Production wiring point (CONFIRMED)

`__main__.py:187-203` constructs `ProcessManager`; `worktree_mgr` is its
second parameter, default `None`. Only `__main__.py` constructs it in
production (`cli/local.py` receives a pre-built instance). Insertion:
construct `WorktreeManager(PROJECT_ROOT, WORKTREES_DIR)` after the
router (line 186), pass it in. Three guards in `lifecycle_manager.py`
(`:255, :311, :392`) go live with non-None.

### Q3 — Re-attach idempotency (CONFIRMED)

Two layers in `worktree.py:20-66`: existing path → returned as-is
(`:27-29`); `git worktree add -b` failing with "already exists" →
re-create without `-b`, attaching to the surviving branch (`:44-59`).
The 015-observed `'main' already used by worktree` quirk is therefore
handled for the *re-attach* case. Service-restart flow: lazy
re-provision via `_get_or_create_adapter` calls the idempotent
`create()` again (`lifecycle_manager.py:250-258`).

### Q4 — Orphans (CONFIRMED: no sweep exists)

No startup sweep, no orphan listing. Crash mid-spawn leaves the worktree
+ branch behind; `kill_entity` removes only on the normal path. Decide
in design whether a startup sweep is in 023's minimal cut or a flagged
follow-up.

### Q5 — Tests for the wiring gap (CONFIRMED)

015's tests inject fakes (`AsyncMock(spec=WorktreeManager)` in
`test_process_manager.py:317-361`; `FakeWorktreeManager` in
`test_lifecycle_manager.py:61-74`) — the composition root itself has
**zero coverage** and isn't factored for testing. Options for design:
extract a small factory from `__main__` and unit-test it, or accept the
live smoke as the only wiring proof (that's how this gap shipped).

### Q6 — Root cause of the lost proposal (CONFIRMED, revised)

Not a delivery gap. Three classes — F1 stillborn spawn (no escalation),
F2 silent misaddress drop (`message_dispatcher.py:284-286`: warning +
`continue`, no audit, no sender feedback), F3 transcript mis-bind
(above). The enqueued-but-never-woken scenario the ticket hypothesised
did not occur — no message ever reached a queue.

### Q7 — Relationship to 021 (CONFIRMED: different root causes)

021 (maestro→user) is a missing `user` queue in `router._queues` —
real, observed in the same journal window ("No queue for recipient
user" at every team spawn), still 021's scope. F1–F3 are upstream of
the router entirely. Cross-link, don't widen.

### Q8 — Incident evidence (CONFIRMED)

Quoted throughout: journal (`PtySession: proc dead ×3`, `auto-kickoff …
failed`, `Unknown recipient: maestro.strutils`, idle-reap lines), audit
log (no wake/peer events on 06-11), `messages` table (no lead rows), CC
transcript `96c4d1a7…` (completed turn with valid, correctly-addressed
`hive_actions`).

### Q9 — Defaults audit (CONFIRMED)

Post-#90 state per knob:

| Knob | State | Note |
|------|-------|------|
| model | CANONICAL (`entity.py:206` opus; #90 aligned dispatch+facade) | done |
| permission_mode | INTENTIONAL SHADOW — `lifecycle_manager.py:211` forces maestro `yolo` over `entity.py:215` `default` | document (ADR/comment), don't "fix" |
| advisor | BENIGN — `None` + opt-in `resolve_advisor()` (013) | leave |
| cwd | derived from worktree_path; 023 makes it real | this ticket |
| max_workers | hardcoded `TeamLead:27` = 2 | consider `config.py` lift |
| timeout_minutes | `config.py` env-driven | leave |

One latent adjacent fact (not a 023 driver, log for completeness): the
wake path is **single-shot** — `_wake_entity` swallows "already running"
silently and nothing re-checks `router.has_pending()` when a turn ends
(`wake_scheduler.py:143-159`); a wake landing while the recipient is
busy parks the mail until the 120m tick. Didn't fire in this incident;
candidate small fix here or in 021.

## Implications for design (the forks, sharpened)

1. **Floor scope** — minimal DI wiring vs + env override vs + startup
   orphan sweep vs + factory extraction for testability.
2. **F2 fix shape** — alias resolution (`maestro`/parent auto-resolve in
   the dispatcher), sender feedback on rejected actions (inject an error
   into the next turn), or both. JD-only is disproven.
3. **F3 hardening** — floor-only (per-entity project dir) vs also
   binding the reader to the session id CC reports (defense in depth vs
   scope creep; 016 will lean on this seam hard).
4. **F1 escalation** — in 023, or explicitly punted to 020 (it is 020's
   class: "jammed/failed session → notify + recover")?
5. **Wake single-shot retry** — `has_pending` check on turn end: here,
   021, or flagged-only?
6. **Defaults** — document-only vs also lift `max_workers`.
