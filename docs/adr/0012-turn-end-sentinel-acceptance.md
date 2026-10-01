# 0012 — Turn acceptance: deterministic turn-end sentinel over quiescence heuristic

**Status:** Accepted (2026-06-12)
**Ticket:** [026](../tickets/026-turn-boundary-acceptance/)

## Context

Hive learns an Entity's Turn result by polling the Claude Code session
transcript (`.jsonl`). Acceptance was heuristic: a new assistant entry +
500 ms of file quiescence + no unresolved `tool_use` (ADR 0010's
pending-tool guard). The 023 live smoke caught the failure: once a
`tool_result` lands, post-tool *thinking* writes nothing to the file, so
a thinking gap > 500 ms is indistinguishable from turn end. The reader
accepts the intermediate entry, extracts partial/empty text, and the
final message — carrying the Turn's `hive_actions` — arrives after
nobody is listening. Silent action loss; from the outside, "the lead
never replied."

Measurement (556 transcripts, CC 2.1.113–2.1.173) showed this is the
**default**, not a race: 93.2 % of post-tool gaps exceed 500 ms; even a
30 s window loses 9.8 %. No window value fixes it.

## Decision

1. **Primary — turn-end sentinel.** Accept a Turn when a new
   `{"type": "system", "subtype": "turn_duration"}` entry appears after
   the prompt (count-based, since `--continue` transcripts retain stale
   sentinels). CC writes this entry itself when the turn truly
   completes; measured file order is absolute (1,942/1,942 after the
   final assistant entry; median lag 119 ms).
2. **Fallback — hardened quiescence, degrade loudly.** If no sentinel
   ever arrives (future CC format change; the only observed sentinel-less
   transcripts are sessions killed mid-turn): accept when the last
   assistant entry is `stop_reason == "end_turn"` AND text-bearing AND
   the file has been quiet ≥ 30 s. First fallback acceptance per session
   logs at ERROR ("sentinel absent — acceptance is heuristic"), then
   WARNING. The fleet degrades visibly instead of dying on per-turn
   180 s timeouts that masquerade as jammed PTYs.
3. **Gates stay heuristic.** A gated Turn (ADR 0004/0005) is mid-turn —
   no sentinel will ever arrive — so gate detection remains
   quiescence-based and runs before acceptance.

## Alternatives rejected

- **Wider quiescence window** — measured dead end (9.8 % loss at 30 s)
  plus added latency on every Turn.
- **`stop_reason == end_turn` as primary** — 48 % of `end_turn`-stamped
  entries are non-final (multi-block flushes, multi-response turns);
  2.5 % precede > 30 s of silence. Kept only as a fallback filter.
- **Model-emitted done-marker** (prompt/skill/extra action) — in-band:
  it rides the same channel the reader abandons, so it is lost with the
  message it protects; per-turn model compliance is probabilistic where
  the sentinel is structural.
- **CC Stop hook pushing to Hive** — same trust layer as the sentinel
  (harness-executed) but needs a transport into Hive, per-entity
  settings injection, and a guard against firing on the developer's own
  sessions via the shared `~/.claude/settings.json`. Three new
  CC-behavior assumptions (the class that broke 013) to win latency the
  polling path doesn't need. Parked as the natural primitive for a
  future event-driven architecture.

## Consequences

- Turn acceptance is deterministic on every healthy fleet session, and
  *faster* (sentinel median 119 ms vs the 500 ms dead-wait).
- Long Workflow turns (Ticket 016's leaf engine) can mix tools and
  `hive_actions` freely — the failure mode that gated S5's
  end-to-end DoD is closed.
- A CC transcript-format change degrades the fleet to loud heuristic
  mode instead of bricking it; the ERROR log names the cause.
- The reader's correctness now depends on one CC transcript invariant
  (sentinel after final message, in file order) — re-verify it when
  bumping the pinned CC version (Ticket 009's pin is the control point).
- Timestamps in transcripts are event times, not write times (verified
  live); any future reader logic must reason in file order only.
