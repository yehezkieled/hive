# Design — Ticket 029

Chosen approach for restoring the maestro interactive-gate bridge.

## Constraint that picks the shape

`research.md` establishes two facts that, together, force a **repro-first,
two-part** fix rather than a single code change:

1. The detection logic already handles the live text-then-ask shape (proven by
   `f26472e7`). So there is **no detector bug to fix** for that shape.
2. The actual Run 1 failure is one of three causes (C1 tool-not-emitted / C2
   yolo-suppression / C3 detector-None) that the surviving transcripts cannot
   separate. Committing to one blind would risk "fixing" the wrong layer.

So the design is: **(A) ship the cause-independent hardening now, (B) run the
deployed reproduction to identify the cause, (C) apply the cause-specific fix
the repro points to.** The hardening is safe and net-positive even if the
repro later shows it was not the proximate cause.

## A — Reader hardening (cause-independent) — **chosen, ship now**

Make the unanswered-gate check **authoritative** in
`TranscriptReader.await_next_assistant_turn`:

- Evaluate `_detect_gate(entries)` **before** sentinel acceptance
  (`transcript_reader.py:242-246`), and **before** the 500ms-quiescence-gated
  strict-acceptance branch (`:253-265`), on every poll once a new assistant
  entry exists.
- If a gate is present → `return Gated(gate)` immediately.
- Only if `_detect_gate` returns `None` do the sentinel / pending-tool /
  fallback branches run, exactly as today.

Why this is correct and safe:
- An unanswered `ExitPlanMode`/`AskUserQuestion` tool_use is, by definition,
  not a completed turn. Letting any *other* acceptance branch win over it is
  the latent defect. After the change, a gate can never be mistaken for a
  finished turn — even if a future Claude Code writes a sentinel beside the
  unanswered gate, or a multi-block flush keeps the file from quiescing.
- It removes the fragile invariant "a gated turn never emits a sentinel"
  (`transcript_reader.py:237-241`) that ADR 0012 leaned on.
- No behaviour change for normal turns: when `_detect_gate` is `None` (no
  detector, or no gate), the ladder is byte-for-byte what it is today.

**Cost:** one extra `_read_entries` + `detect` per poll when a new assistant
entry exists. `_detect_gate` short-circuits to `None` instantly when no
detector is wired (`transcript_reader.py:314-315`), so non-bridged sessions
(most leaf/quiet sessions) pay ~nothing. Bridged sessions already parse
entries on the quiescence branch; we move that parse earlier, not add a new
class of work.

→ Documented as **ADR 0015** (amends the acceptance-ladder ordering of ADR
0012). Append-only; ADR 0012 is referenced, not edited.

## B — Deployed reproduction (settles the root cause) — **chosen**

Drive a real maestro through the propose-and-wait pattern on deployed code
(via `POST /api/command`, the established live-smoke path), and capture:

1. Does a structured `AskUserQuestion` tool_use land in the maestro's
   `.jsonl`? (tests C1)
2. The `PtySession` spawn log line — was `gate_detector` wired / coordinator
   non-None? (tests C3)
3. Does the reader now return `Gated` and the gate row appear, with the turn
   parked (no timeout)? (confirms A end-to-end, tests C2)

This is the ticket's required "test reproducing Run 1" at the system level and
the S6 "deployed re-smoke" DoD — so it is not extra work, it is the
acceptance.

## C — Cause-specific fix (after B) — **decision tree**

```
Repro shows…                                   → Fix
─────────────────────────────────────────────────────────────────────
AskUserQuestion tool_use present, gate now      → A alone was the fix.
bridges (was a sentinel/quiescence race)           Close ticket on A.

NO AskUserQuestion tool_use written, model      → C1: curate AskUserQuestion
emitted text / a non-gate pause                    into the maestro toolset
                                                   (Ticket 012 denylist) and/or
                                                   teach GateDetector the real
                                                   pause shape. New slice.

tool_use present only WITHOUT yolo; suppressed  → C2: carve --dangerously-skip-
under --dangerously-skip-permissions               permissions so ask/plan gates
                                                   still render (or detect anyway).

coordinator/detector was None at spawn          → C3: assert gate_coordinator is
                                                   not None at maestro adapter
                                                   creation; fail loud, never
                                                   silently disable detection
                                                   (pty_session.py:224).
```

Whichever branch fires, A stays in (defensive).

## Alternatives considered & rejected

- **"Just reorder the ladder and call it fixed" (A only, no repro).** Rejected:
  the on-disk evidence shows the live frozen shape has *no* sentinel, so the
  reorder is *not proven* to fix Run 1 — it closes a theoretical race. Shipping
  it as "the fix" without the repro would risk re-opening the ticket when the
  maestro stalls again for C1/C2/C3 reasons. A is necessary, not sufficient.
- **Add a wall-clock cap so a gate can't hang forever.** Rejected — directly
  contradicts the bridge's "park forever, never auto-decide" invariant (#25,
  `gate_coordinator.py:119-134`). The whole point is to *wait* for the user.
- **Add an `attachment` fallback for ask gates (mirror the plan_mode path).**
  Rejected for now: there is no `ask`-equivalent attachment in the transcript
  to key off; `AskUserQuestion` only surfaces as a tool_use. Reconsider only if
  the repro shows a write-timing problem with the tool_use itself.
- **Screen-scrape the TUI menu as a detection fallback.** Rejected — violates
  ADR 0001 (transcript is the source of truth, never the screen).

## Side effects on shared docs

- **ADR 0015** (new) — gate check is authoritative over sentinel acceptance.
  References ADR 0012 (acceptance ladder) and ADR 0004 (gate coordinator).
- **CONTEXT.md** — no new term required; "Interactive gate" already covers
  ask/plan. (If the repro lands on C1 and we extend gate kinds, revisit then.)
- **README/DEPLOYMENT** — no change (no operator-facing surface changes).
