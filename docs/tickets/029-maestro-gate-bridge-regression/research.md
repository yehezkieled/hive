# Research — Ticket 029

Root-cause of "a maestro `AskUserQuestion` gate did not reach the user; the
turn sat unbridged until the 180s timeout (zero coordinator activity)."

Method: read the full gate subsystem first-hand (`pty_session.py`,
`transcript_reader.py`, `gates.py`, `gate_coordinator.py`,
`lifecycle_manager.py`, `__main__.py`), cross-checked by a 4-reader parallel
sweep + synthesis, then verified against a **real frozen-gate transcript** on
disk. Where the synthesis and the on-disk evidence disagreed, the transcript
wins (noted below).

---

## TL;DR

- **Detection logic is sound.** A gate that follows assistant text IS detected
  correctly — proven by the code path *and* a real frozen `AskUserQuestion`
  transcript on disk. The bug is **not** in `gates.py`. (Q2 ✓)
- **The gate was never detected in Run 1** — that is the only state consistent
  with *both* observed symptoms (zero coordinator activity **and** a 180s
  timeout). A *detected* gate parks the turn **forever** (no timeout), so a
  timeout proves detection did not fire. (Q6 ✓)
- **The ticket's premise is unconfirmed.** No surviving otter transcript
  contains the string `AskUserQuestion` at all. Either the Run 1 transcript is
  gone, or otter never emitted a *structured* `AskUserQuestion` tool_use. (Q4 ⚠)
- **Root cause narrows to three candidates** (C1/C2/C3 below); disambiguating
  them needs a **deployed reproduction** — which is also the ticket's own
  acceptance test and the S6 "deployed re-smoke" DoD.
- **The fix has a safe part and a confirm-then-fix part.** The reader
  hardening (make the gate check authoritative) is safe to ship regardless;
  the root-cause-specific fix is chosen after the repro.

---

## How the bridge is supposed to work (control flow)

```
send(prompt)                                   pty_session.py:295
  └─ inject prompt into PTY                     pty_session.py:317
  └─ loop: await_next_assistant_turn(...)       pty_session.py:342-348
        ├─ returns (text, usage)  → done
        └─ returns Gated(gate)    → _handle_gate → coordinator.resolve()
                                                  pty_session.py:390-403
await_next_assistant_turn  (acceptance ladder)  transcript_reader.py:211-302
  per poll, in THIS order:
    1. sentinel acceptance (NO quiescence wait) transcript_reader.py:242-246
    2. strict acceptance (needs 500ms quiet):   transcript_reader.py:253-255
         a. gate check  → return Gated          transcript_reader.py:262-265
         b. pending-tool guard → keep polling    transcript_reader.py:272-275
         c. heuristic fallback (30s + end_turn)  transcript_reader.py:283-300
  no progress for 180s → TimeoutError           transcript_reader.py:213-227
GateDetector.detect(entries)                    gates.py:68-95
  - plan gate: plan_mode ATTACHMENT  OR  ExitPlanMode tool_use (unresolved)
  - ask  gate: AskUserQuestion tool_use (unresolved) — NO attachment fallback
  - scans ALL assistant entries / ALL tool_use blocks  gates.py:131-141
GateCoordinator.resolve(...)                    gate_coordinator.py:88-117
  - creates a durable "gate" approval row (mode_request_store)
  - parks on a doorbell FOREVER (no timeout, never auto-decides)  :119-134
```

Two load-bearing facts fall out of this:

1. **`resolve()` parks forever** (`gate_coordinator.py:128-134`). So if a gate
   is ever detected, the turn cannot 180s-timeout — it waits for `/approve`.
   Run 1 *did* timeout → **the gate was never detected.** (answers Q6)
2. **The coordinator surface (the "gate row") is created only inside
   `resolve()`** (`gate_coordinator.py:95-101`), which is reached only via a
   `Gated` outcome. "Zero coordinator activity / no gate row" therefore means
   exactly: the reader never returned `Gated`.

---

## Q1 — Detector wiring: passed to ALL entities, set before any turn

`lifecycle_manager._get_or_create_adapter` passes
`gate_coordinator=self._mgr.gate_coordinator` to **every** entity's adapter —
no role conditional (`lifecycle_manager.py:269-275`). `ClaudeAdapter` forwards
it to `PtySession` unconditionally (`claude_adapter.py:148`).

Startup order in `__main__.py`: coordinator is set at **line 216**, *before*
`restore(persisted)` at **279** and `register_maestro` at **293**. Adapters are
created lazily on an entity's first turn, which is after startup. So a fresh
maestro gets a **non-None** coordinator and therefore a live `GateDetector`
(`pty_session.py:224`).

→ "Maestro never gets a coordinator" is **unlikely in production**. It survives
only as a low-probability edge (a cached gate-blind adapter,
`lifecycle_manager.py:249-251`), kept as **C3** below.

## Q2 — Gate-after-text IS detected (proven on disk)

`GateDetector._tool_use_blocks` scans **all** assistant entries
(`gates.py:131-141`); `detect()` skips every *resolved* tool_use and returns
the first *unresolved* `ExitPlanMode`/`AskUserQuestion` (`gates.py:85-93`).
Preceding text and earlier (resolved) tool calls do not shadow it.

**Verified against a real frozen capture.** A development transcript from the
Ticket-003 build (`…/fix-pty-output/f26472e7-…jsonl`) holds the exact Run 1
shape:

```
[entry 84] stop_reason=tool_use :: TEXT('The codebase already has several rate limiting…')
[entry 85] stop_reason=tool_use :: TOOL:AskUserQuestion  <<UNRESOLVED>>
turn_duration sentinels in the whole turn: 0
```

Running the detector over these entries: every earlier tool (Skill, Bash,
Read, …) is resolved and skipped; the unresolved `AskUserQuestion` is
returned as `Gate(kind="ask")`. **Detection works on the real shape.** (Q2 ✓)

This capture is the single most important piece of evidence in the ticket —
it is the literal text-then-ask shape, frozen, and detection handles it.

## Q3 — The sentinel race is NOT the Run 1 cause (synthesis overruled)

The parallel synthesis's top hypothesis was: "Claude Code writes a
`turn_duration` sentinel for the proposal-text portion; sentinel acceptance
(`transcript_reader.py:242-246`, no quiescence wait) fires before the gate
check and returns the text."

**The on-disk capture refutes this for the live shape.** In `f26472e7` the
text block is `stop_reason=tool_use` (not `end_turn`) and **the entire
incomplete turn has zero `turn_duration` sentinels** — Claude Code writes the
sentinel only when a turn *truly completes*, and a turn parked on
`AskUserQuestion` has not completed. With no sentinel, the sentinel-acceptance
branch never matches; the reader reaches the strict-acceptance branch, the
frozen menu quiesces (mtime stops), and the gate check at
`transcript_reader.py:262-265` runs and returns `Gated` — **if the detector is
wired.**

→ The sentinel/quiescence races are *real theoretical gaps* (a multi-block
flush could keep mtime moving; a future CC version could emit a sentinel), but
they are **not** what bit Run 1. They are worth closing defensively (see
design.md F1), not as the claimed root cause. (Q3 answered)

## Q4 — The premise is unconfirmed: no `AskUserQuestion` in any otter transcript ⚠

A grep for `AskUserQuestion` across **all** of `~/.claude/projects/` (224
files match) returns **zero** files in any `otter-*` project dir. The smoke
dirs that exist:

| dir | last mtime | assistant entries | tool_use seen | sentinels |
|-----|-----------|-------------------|---------------|-----------|
| `otter-hive-smoke` | 06-13T03:07 | **0** (frozen / `/exit`) | none | 0 |
| `otter-smoke2` | 06-13T02:40 | 15 | ToolSearch, Workflow, TaskOutput, Bash | 2 (Run 2, clean) |
| `otter-smoke23/26/strutils` | 06-11..13 | many | — | 2–5 |

`otter-smoke2` is the clean **Run 2** (it ran a `Workflow`, no gate, two
completed turns — matches "Run 2 avoided it by dropping the wait step").
`otter-hive-smoke` is a *lead's* worktree (its first user entry is the
spawn-contract boilerplate naming otter as parent, then `/exit`), not otter's
Run 1 maestro turn.

→ **The actual Run 1 maestro transcript is not on disk**, and nothing on disk
shows otter ever writing a structured `AskUserQuestion`. The ticket's "it
emitted an `AskUserQuestion`" is therefore an *inference from the prompt
pattern*, not a transcript-confirmed fact. This is the central open question.

## Q5 — The one maestro-vs-lead spawn divergence: `yolo`

`register_maestro` forces `maestro.permission_mode = "yolo"`
(`lifecycle_manager.py:214`), which `_build_spawn_args` maps to
`--dangerously-skip-permissions` (`pty_session.py:135-138`; `DANGEROUS_MODES`
in `models/entity.py`). Leads do not get this by default. That flag bypasses
**tool-permission prompts**, and per CONTEXT/CLAUDE docs it should *not* touch
`ExitPlanMode`/`AskUserQuestion` gates — but it is the **only** maestro-
specific spawn difference, so whether it changes how/whether `AskUserQuestion`
renders or is written must be **confirmed on deployed code**, not assumed.
Kept as **C2**.

## Q7 — Test coverage gap

Existing gate tests cover the **plan** gate only:
- `test_transcript_reader.py:319` `test_await_returns_gated_when_plan_gate_detected`
- `test_transcript_reader.py:514` `test_gate_detection_wins_over_pending_tool_guard`
- `test_pty_session.py:280` `test_send_handles_plan_gate_then_resumes`

**Missing:** any **ask**-gate reader test; any gate-**after-text** test; any
"sentinel co-present with an unanswered gate" test. These are the new tests
(see plan.md).

---

## Root cause — narrowed to three candidates

All three produce the identical symptom pair (no `Gated` → zero coordinator
activity → 180s timeout); the surviving transcripts cannot separate them.

| | Candidate | Mechanism | Code-plausibility |
|--|-----------|-----------|-------------------|
| **C1** | otter never emitted a *structured* `AskUserQuestion` tool_use | tool absent from the maestro's curated set, or the model emitted a textual "question" / a Thinking-skill pause that is **not** a recognized gate → nothing to detect; the PTY froze on a different un-bridged pause | **Highest** — matches the on-disk evidence (no `AskUserQuestion` anywhere in otter dirs) |
| **C2** | `--dangerously-skip-permissions` (yolo) changes gate emission | the maestro-only flag suppresses/auto-handles the gate so no detectable tool_use is written | Medium — the only maestro-specific divergence; CC-behaviour, must be checked live |
| **C3** | `gate_detector` was `None` for otter's adapter | a cached gate-blind adapter created before wiring → `_detect_gate` always returns `None` (`transcript_reader.py:314-315`) | Low — coordinator is set before restore/register; needs a caching edge |

**Decisive experiment (settles C1/C2/C3):** a deployed reproduction — spawn a
maestro, drive the propose-and-wait prompt, and capture (a) whether a real
`AskUserQuestion` tool_use lands in its `.jsonl`, (b) the `PtySession` spawn
log line (was `gate_detector` wired?), (c) whether the reader returns `Gated`.
This is the same reproduction the ticket's acceptance requires.

## What the fix must do (carried into design.md)

1. **F1 — Reader hardening (safe regardless of cause):** make the unanswered-
   gate check **authoritative** — evaluate it before sentinel acceptance and
   decouple it from the 500ms quiescence wait, so no sentinel or never-
   quiescing flush can short-circuit an unanswered `ExitPlanMode`/
   `AskUserQuestion`. Closes the C-of-synthesis theoretical races; touches
   the acceptance-ladder order of ADR 0012 → **ADR 0015**.
2. **F2 — Root-cause-specific (after the repro):** C1 → curate
   `AskUserQuestion` into the maestro toolset / make the real pause a
   recognized gate; C2 → carve yolo so ask/plan gates still render; C3 →
   assert `gate_coordinator is not None` at maestro adapter creation and fail
   loud instead of silently disabling detection (`pty_session.py:224`).
3. **F3 — Tests:** ask-gate reader test (`Gated`, `kind=="ask"`), gate-after-
   text-with-sentinel and without-sentinel variants, and a `send()`-level
   ask-gate test mirroring `test_send_handles_plan_gate_then_resumes`.

## Non-goals carried from `ticket.md`

- The scheduler poke that exploited the window — **Ticket 028** (done).
- The no-progress timeout firing during the wait — **Tickets 027/030**.
