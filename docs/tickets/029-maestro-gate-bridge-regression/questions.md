# Questions — Ticket 029

The unknowns going into the root-cause. Each is answered in `research.md`
with a file:line or transcript reference, or explicitly deferred to the live
re-smoke.

> **Redirect note (2026-06-14).** Q1–Q7 below scoped the original *bridge-fix*.
> The root-cause they established (the native-gate bridge is fragile and the
> only live maestro gate, `AskUserQuestion`, can be denied — `ExitPlanMode`
> already is) led to re-scoping 029 into the **conversational decision
> channel**. The redirect's own design questions (Q1 liveness, Q2 mechanism,
> Q3 native-gate fate, Q4 clearing, Q5 persistence) were resolved in a
> grill-with-docs session and are captured in `research.md` (Part 2),
> `design.md`, and [ADR 0017](../../adr/0017-conversational-decision-channel.md).

## Q1 — Is the gate *detector* even wired on a maestro's PTY?
`pty_session.py:224` builds a `GateDetector` only when `gate_coordinator is
not None`. Is a maestro's coordinator ever `None` at adapter-creation time
(startup order, a cached gate-blind adapter)?

## Q2 — Does detection handle a gate that *follows* assistant text?
The ticket's trigger is "proposal text, THEN `AskUserQuestion`." Does the
detector miss a gate when prior text/tool_use blocks precede it in the turn?

## Q3 — Could a `turn_duration` sentinel short-circuit the gate?
The acceptance ladder checks the sentinel before the gate. If Claude Code
writes a sentinel for the proposal-text portion, does the reader accept the
turn as complete and never run the gate check?

## Q4 — Did otter actually emit a *structured* `AskUserQuestion` tool_use?
The ticket asserts it did. Is that confirmed by the Run 1 transcript, or
inferred from the prompt pattern? (If no structured tool_use was written,
there is no gate to detect and the freeze cause is different.)

## Q5 — What is *maestro-specific* about the spawn?
A lead's gate is supposed to differ from a maestro's (maestro → user, lead →
parent). Is there a spawn-config divergence (`yolo` /
`--dangerously-skip-permissions`, skill curation) that changes whether the
gate renders or is written to the transcript?

## Q6 — If detection fired, what would the symptom be?
Does a detected gate park the turn open indefinitely (no timeout), or is it
still subject to the 180s deadline? (Distinguishes "detected but not bridged"
from "never detected.")

## Q7 — What test reproduces Run 1, and what coverage exists today?
Is there an existing reader/`send()` test for the **ask** gate, and for a
gate that follows assistant text + a sentinel? What is the minimal failing
test?
