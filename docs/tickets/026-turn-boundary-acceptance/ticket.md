# 026 — Turn boundary: reader accepts mid-turn during post-tool thinking gaps

## What

Replace the transcript reader's count + mtime-quiescence acceptance
heuristic with a **deterministic turn-end signal**. Claude Code writes
a `system` entry with `subtype: "turn_duration"` when a turn actually
completes — accept a turn only when that sentinel (or an equally
deterministic end marker) has appeared after the prompt; keep the
quiescence heuristic only as a fallback for transcripts that lack the
sentinel.

## Why — found live, 2026-06-11 (023 smoke)

The 500 ms quiescence window loses to ordinary model thinking time.
Observed twice in one run on the `otter.smoke23` lead (transcript
`a012a36d…`):

```
22:59:41.394  assistant entry: Bash tool_use
22:59:41.576  tool_result lands          ← pending-tool guard (#82) passes
              … 3.2 s of model thinking: file SILENT …
22:59:42.1    reader: count↑ + 500 ms quiet → ACCEPTS mid-turn
              → extracts empty/partial text → hive_actions never parsed
22:59:44.761  next assistant entry (too late — nobody is reading)
22:59:50.786  REAL final message, with to:"maestro" proposal — lost
```

The same mechanism silently consumed the lead's **kickoff turn**
(first turn after spawn): accepted early on a recon-tool gap, partial
text had no actions, lock released cleanly — no error, no timeout, no
routing. From the outside this is indistinguishable from "the lead
never replied".

**Re-attribution note:** the 06-11 `optest` "lead went silent" leg
(023 `research.md`, incident attempt 3) was attributed to F3
(transcript mis-bind). With session pinning now live and this race
demonstrated under pinning, premature acceptance is the *better*
explanation for that leg too — the optest lead also ran recon tools
before its proposal.

## Why now — this gates everything downstream

- 023's live DoD: the Workflow report-back leg cannot complete
  reliably — any turn that mixes tool calls with `hive_actions` can
  lose its actions.
- Sprint S5 DoD: "a maestro turn completes end-to-end on deployed
  code" — the maestro→lead handshake breaks exactly here.
- 016: leaf execution = long Workflow turns ending in a synthesis
  report — guaranteed tool gaps before the final message; this bug
  fires on every such turn that thinks > 500 ms after its last tool.

Verified working in the same smoke (so NOT in scope to re-fix): the
worktree floor, session pinning (pid → sessionId → transcript), the
`maestro` alias, peer routing, and wake-on-inbound — a tool-free turn
delivered the proposal end-to-end.

## Acceptance

- Reader accepts a turn only on the deterministic end-of-turn marker;
  a synthetic transcript with a resolved tool_use followed by a > 2 s
  silent gap and then more assistant entries is **not** accepted early.
- The smoke timeline above, replayed as a fixture, yields the final
  message (with its `hive_actions`) — not the partial.
- Fallback path (no sentinel in transcript) still terminates: hardened
  quiescence with a documented window.
- No-progress timeout semantics (#78) preserved.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Auto-bounce/escalation of genuinely jammed sessions (020).
- Re-running the 023 live smoke (its remaining legs become 026's
  verification).

## Notes

Found by 023's live smoke — empirical answer to 015 `questions.md`
Q14 ("premature accept on an intermediate entry"). Belongs in S5
ahead of 016: it gates the same DoD line 023 gated.
