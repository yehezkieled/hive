# Design — Ticket 026: turn-boundary acceptance

Decisions grilled and locked 2026-06-12. Evidence in
[`research.md`](research.md); rationale recorded in
[ADR 0012](../../adr/0012-turn-end-sentinel-acceptance.md); the
**Turn-end sentinel** term added to [`CONTEXT.md`](../../../CONTEXT.md).

## Chosen approach

`TranscriptReader.await_next_assistant_turn` gets a three-rung
acceptance ladder, evaluated on every poll:

```
 every poll tick
      │
      ├─ 1. GATE CHECK (quiescence ≥ 500 ms, unchanged)
      │     unanswered interactive gate?  → return Gated
      │     (a gated turn never emits a sentinel — this must
      │      stay heuristic, and must run before acceptance)
      │
      ├─ 2. SENTINEL ACCEPTANCE (primary, deterministic)
      │     new turn_duration entry since call start?
      │     → accept NOW: extract last assistant entry
      │     (no quiescence wait — file order guarantees the
      │      final message precedes the sentinel, 1,942/1,942)
      │
      ├─ 3. FALLBACK ACCEPTANCE (heuristic, sentinel-less only)
      │     last assistant entry stop_reason == "end_turn"
      │     AND it has a text block
      │     AND file quiet ≥ 30 s (injectable)
      │     → accept + log (first fire per session: ERROR;
      │       after: WARNING)
      │
      └─ otherwise: keep polling; pending tool / mtime advance
         still reset the 180 s no-progress deadline (#78,
         unchanged)
```

### Decisions

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Primary signal | Count-based `turn_duration` sentinel | Only deterministic turn-end marker that exists; file-order verified 1,942/1,942; median 119 ms after the final message — faster than today's 500 ms dead-wait. Count-based because `--continue` transcripts hold stale sentinels. |
| 2 | Failure posture when sentinel never arrives | **Degrade loudly** (fallback accepts; ERROR-level on first fire per session) | A CC update that changes the transcript format must not brick the fleet — every turn failing on a 180 s timeout looks like a jammed PTY and misleads diagnosis. Degraded mode (~2.5 % misfire) is strictly better than today's 93 %, and the ERROR line names the real cause. |
| 3 | Fallback window | **30 s**, injectable parameter | 2.5 % misfire vs 1.0 % at 60 s — but the host-wide stats overstate fleet risk (they include heavy interactive dev sessions), and +60 s per degraded turn makes Telegram feel dead, defeating the point of choosing degrade-over-die. Injectable so sentinel-less test fixtures run fast. |
| 4 | Fallback hardening | `stop_reason == end_turn` + text-bearing entry required | Kills the exact smoke failure class (the wrongly-accepted entry was stamped `tool_use`; bare thinking entries have no text). Cannot be primary: 48 % of `end_turn` entries are non-final. |
| 5 | Gate detection | Unchanged, quiescence-based, checked before acceptance | Gated turns are mid-turn — no sentinel will ever arrive. Same ordering as today (gate check before pending-tool guard, ADR 0004/0005 semantics intact). |
| 6 | Extraction | Unchanged (`_extract_last_turn`) | Under sentinel acceptance the last assistant entry is the final message (file-order guarantee). A turn legitimately ended on an unresolved tool (denied gate, interrupt) honestly yields empty text. |
| 7 | Caller contract | Unchanged | `PtySession.send()` keeps `(text, usage) | Gated | TimeoutError`. No call-site changes. |

## Alternatives considered — rejected

1. **Widen the quiescence window.** Measured dead end: 9.8 % of post-tool
   gaps exceed even 30 s, and every widened second is added latency on
   every turn. (research §1)
2. **`stop_reason`-primary.** 48 % of `end_turn` entries are non-final;
   2.5 % precede > 30 s of silence. Filter, not signal. (research §4)
3. **Model-emitted done-marker** (prompt instruction / skill / extra
   hive_action). In-band: it travels in the same channel the reader
   abandons early, so it is lost with the message it protects. Also
   per-turn model compliance is probabilistic where the sentinel is
   structural. (research §6)
4. **CC Stop hook pushing to Hive.** Right layer (harness-written,
   deterministic) — the sentinel's sibling. Rejected for now on moving
   parts: needs a transport into Hive, per-entity settings injection,
   and a guard so hooks in the shared `~/.claude/settings.json` don't
   fire on the developer's own sessions. Three 013-shaped risks to win
   push latency we don't need. **Parked**: this is the natural primitive
   if Hive ever goes event-driven (entities waking Hive instead of Hive
   polling transcripts) — that would be its own ticket and grill.

## Out of scope

- Auto-bounce of genuinely jammed sessions (Ticket 020).
- Event-driven turn notification (Stop hook architecture — parked above).
- Any change to gate bridging, extraction semantics, or the
  `PtySession.send()` contract.
