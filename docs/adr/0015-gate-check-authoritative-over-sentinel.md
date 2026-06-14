# 0015 — Gate detection is authoritative over turn-end acceptance

**Status:** Accepted (2026-06-14)
**Ticket:** [029](../tickets/029-maestro-gate-bridge-regression/)
**Amends:** [ADR 0012](0012-turn-end-sentinel-acceptance.md) (acceptance ladder
order) · relates to [ADR 0004](0004-interactive-gate-hold-and-inject.md) (gate
coordinator), [ADR 0001](0001-harness-agnostic-runtime.md) (transcript is the
source of truth)

## Context

ADR 0012 made turn acceptance deterministic via the `turn_duration` sentinel,
and stated (point 3) that "gates stay heuristic … and run before acceptance."
The implementation only honoured that for the *fallback* branch: in
`TranscriptReader.await_next_assistant_turn` the **sentinel-acceptance** branch
(`transcript_reader.py:242-246`) runs *before* the gate check
(`transcript_reader.py:262-265`), and the gate check is additionally gated
behind a 500 ms quiescence wait (`:253-255`). Both were considered safe under
one assumption ADR 0012 leaned on:

> "A gated Turn is mid-turn — no sentinel will ever arrive."

Ticket 029 stress-tested that assumption against the maestro interactive-gate
bridge. The assumption holds for *today's* Claude Code on the live frozen-gate
shape (a real `AskUserQuestion` capture has zero sentinels across the
incomplete turn). But it is a **CC-behaviour assumption**, the same class that
broke Ticket 013 — and it makes correctness of the gate bridge depend on a
detail outside Hive's control. A multi-block flush that keeps the file from
quiescing, or a future CC that writes a sentinel beside an unanswered gate,
would let a non-gate acceptance branch win over an unanswered
`ExitPlanMode`/`AskUserQuestion` — exactly the "turn returns text, gate never
bridges, PTY left frozen on the menu" failure 029 investigates.

## Decision

**The unanswered-gate check is authoritative.** In
`await_next_assistant_turn`, evaluate `_detect_gate(entries)` **first** on
every poll once a new assistant entry exists — before sentinel acceptance and
independent of the quiescence wait. If a gate is present, return `Gated(gate)`
immediately. Only when `_detect_gate` returns `None` do the sentinel,
pending-tool, and hardened-fallback branches run, exactly as ADR 0012
specifies.

Rationale: an unanswered gate tool_use is **by definition not a completed
turn**, so no acceptance branch may outrank it. This realises ADR 0012's
stated intent ("gate detection runs before acceptance") for *all* branches and
removes Hive's dependence on the "a gated turn never emits a sentinel"
invariant.

This is a pure ordering/guard change. When no detector is wired
(`_gate_detector is None`, the default for non-bridged sessions),
`_detect_gate` returns `None` instantly and the ladder is unchanged — no
behaviour or measurable cost difference for leaf/quiet sessions.

## Alternatives rejected

- **Keep sentinel-first, trust the "no sentinel while gated" invariant.** The
  invariant is true today but is a CC-behaviour bet; the gate bridge should not
  rest on it. Cheap to remove the bet.
- **Wall-clock cap so a gate can't hang.** Contradicts the bridge's
  park-forever / never-auto-decide contract (ADR 0004; #25).
- **Attachment fallback for ask gates** (mirror `plan_mode`). No
  ask-equivalent attachment exists in the transcript; `AskUserQuestion` only
  surfaces as a tool_use.

## Consequences

- An unanswered `ExitPlanMode`/`AskUserQuestion` can never be mistaken for a
  finished turn, regardless of sentinel/quiescence timing — the gate bridge no
  longer depends on a CC-behaviour assumption.
- Gate detection fires as soon as the tool_use is in the transcript, not after
  a 500 ms quiescence dead-wait — marginally faster bridging.
- The acceptance ladder for *non-gated* turns is byte-for-byte ADR 0012;
  determinism and the loud heuristic-fallback degrade path are preserved.
- This ADR fixes the *reader ordering* only. Whether Run 1's specific stall was
  a detector-wiring, tool-availability (`AskUserQuestion` not emitted), or
  yolo-spawn issue is settled by Ticket 029's deployed reproduction; any
  cause-specific fix is recorded against 029, not here.
