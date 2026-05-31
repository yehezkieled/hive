# Design — interactive-gate bridge for the PTY harness

Chosen approach. Resolves every fork in [`questions.md`](questions.md);
follows the diagnosis in [`research.md`](research.md). The core
interaction decision is recorded in
[ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md) — this file
is the implementation-level design that flows from it.

## Resolved forks

| Fork | Decision |
|------|----------|
| **Q1** interaction model | **B1 — hold-and-inject.** Freeze the menu, inject the user's keypress into the *live* Turn. (Not notify-only; not escape-and-re-prompt.) |
| **Q2** no-answer policy | **Park forever, never auto-decide.** Light nudge only. Requires a `WAITING`/`GATED` Entity state, idle-kill exemption, and reader-timeout suspension. |
| **round-trip wake** | **W1 — in-memory doorbell** (`asyncio.Event`) keyed to the gate; `/approve` rings it. The persistent approval row is the durable surface; the doorbell is the wake path. |
| **Q3** gates + order | **All three:** plan-approval → `AskUserQuestion` → permission prompt. |
| **Q4** surfaces | **Telegram + web** — both free by reusing the approval-row pattern. |
| **Q5** reuse vs new | **Reuse** the pending-approval row *pattern*; **new** in-memory doorbell for the live wake. |
| **Q6** detection placement | Detect **inside `TranscriptReader`**; handle in `PtySession`. |

## The three gates

All three freeze the PTY identically (subprocess sits on a TUI menu, no key
pressed, no assistant entry written). They differ only in how they appear in
the transcript and which keys answer them.

| Gate | What the user sees | Transcript marker | Verified? |
|------|--------------------|-------------------|-----------|
| ① plan-approval | "Here's my plan. Proceed?" | `attachment.type == "plan_mode"` and/or a `tool_use` named `ExitPlanMode` with no `tool_result` | ✅ (research.md) |
| ② `AskUserQuestion` | "Which option? 1/2/…" | a `tool_use` named `AskUserQuestion` with no `tool_result`; the options live in the tool_use **input** | ✅ (research.md) |
| ③ permission prompt | "Allow `Bash(rm …)`? Yes/No" | **unverified** — must capture a real one; likely an unmatched `tool_use`, to be confirmed | ❌ first impl task |

> Detection never reads the screen. The *option list* for ② also comes from the
> transcript (the tool_use input), so we know which option index to select
> without scraping.

## End-to-end flow (gate ①, the reference case)

```
1  user: "/m:dev build X"   ──▶  send("build X") runs; TranscriptReader tails .jsonl
2  dev plans, calls ExitPlanMode  ──▶  PTY freezes on the menu
3  TranscriptReader spots the unanswered gate   (Q6: detection lives here)
      └─ returns a 3rd outcome "GATED(plan, payload)" instead of (text,usage)/Timeout
4  PtySession handles it          (Q6: handling lives here)
      ├─ suspend the 180s reader timeout
      ├─ set Entity state → WAITING/GATED   (now exempt from idle-kill)
      ├─ create a pending-approval row (reuse the /approve pattern)   → surface
      ├─ register a doorbell (asyncio.Event) keyed to the gate        → wake path
      └─ notify the user (Telegram buttons + web)                     → Q4
5  …Entity parks indefinitely. Nudge re-ping at ~60 min. No auto-decide.   (Q2)
6  user taps Approve (Telegram) or POSTs approve (web)
      ├─ mark the row approved
      └─ ring the doorbell  ─────────────────────────────────────────▶ wakes step 4
7  PtySession injects the keypress for the decision, un-suspends the reader
8  dev executes its plan; TranscriptReader returns the real (text, usage)
9  reply reaches the user; Entity state → back to normal
```

`AskUserQuestion` (②) is the same flow; the injected keystrokes select the
chosen option (`Down × index` + `Enter`) instead of a fixed yes/no. Permission
prompts (③) are the same flow once ③'s transcript shape is verified.

## Components to change

| Component | Change |
|-----------|--------|
| `runtime/transcript_reader.py` | In `await_next_assistant_turn`, also scan for an unanswered gate. Add a third outcome ("gated", with gate kind + payload) alongside completed / `TimeoutError`. Match an actual `tool_use` block, **not** the bare string (research.md false-positive note). |
| `runtime/pty_session.py` | Gate-handling: on "gated", park, suspend the 180s timeout, await the doorbell, then inject via `_inject`/`sendKeySequence`, then resume awaiting the real turn. |
| Entity model / state | New `WAITING`/`GATED` state. |
| `process/manager.py` | Doorbell registry (`asyncio.Event` keyed by gate/entity). Exempt gated Entities from `kill_idle_entities` (use the existing `exempt_names`). Wire `/approve`→ring-doorbell. |
| `commands/dispatch.py` | `/approve` / `/deny` resolve a **gate** approval (ring the doorbell) in addition to today's mode-request flow. |
| Web (`web/app.py`) | Gate approval reuses the mode-request approve/deny endpoints (or a sibling kind). |

## Open implementation choices (defer to outline.md / plan.md)

1. **New approval *kind* vs. reuse `mode_request` rows.** The existing row is
   semantically a *permission-mode elevation* (`request_mode_change` validates
   against `DANGEROUS_MODES`). A gate decision is a different thing. Reuse the
   *pattern* but most likely add a `kind` discriminator or a sibling store.
   **Lean:** add a `kind` to the approval row rather than a whole new store.
2. **Exact keypress per gate.** The plan menu may have 3 rows (yes-auto /
   yes-manual / no), not a clean 1/2. Confirm the live key layout per gate.
   This is the one spot coupled to the TUI's menu layout — a known sensitivity
   (ADR 0001 consequence), even though detection stays transcript-only.
3. **Restart-while-parked recovery.** A restart kills the held Turn; the row
   survives with no coroutine behind it. Re-spawn + re-detect, or mark stale and
   re-ask. (ADR 0004 deferred this here.)
4. **③ permission-prompt transcript shape.** Capture a real one first; confirm
   it's structurally detectable. Gate for shipping ③.
5. **Nudge interval.** Default ~60 min, then silence. Tunable.

## Non-goals (carried from ticket.md, reaffirmed)

- No auto-decide (no auto-approve, no auto-deny on a timer).
- No screen-scraping — detection is transcript-only.
- No general human-in-the-loop framework beyond these three gates.
- Plan mode is **not** disabled for Entities.

## Reference-doc impact

- `CONTEXT.md` — added **Interactive gate** (done, this ticket).
- `docs/adr/0004-…` — added (done, this ticket).
- `README.md` — if implementation surfaces the new `WAITING`/`GATED` state to
  users, add a short note. **If so, this becomes a cross-cutting Ticket and the
  README edit must be declared in `plan.md`.**
