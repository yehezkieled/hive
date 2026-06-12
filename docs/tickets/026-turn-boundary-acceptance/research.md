# Research — Ticket 026: turn-boundary acceptance

All findings from 2026-06-12, this host (556 transcripts in
`~/.claude/projects/`, CC versions 2.1.113 → 2.1.173). Every claim below
is measured, not inferred.

## 1. The bug is the default, not a race

Acceptance today (`src/hive/runtime/transcript_reader.py:198-221`):
a new assistant entry exists AND mtime stable ≥ 500 ms AND no unresolved
`tool_use` in the last assistant entry. The #82 pending-tool guard passes
the moment a `tool_result` is written; post-tool *thinking* writes
nothing, so a thinking gap > 500 ms is indistinguishable from turn end.

Fleet-wide distribution of `tool_result` → next-assistant-entry gaps
(n = 6,945):

| window | gaps exceeding it (= premature-accept rate) |
|--------|---------------------------------------------|
| 0.5 s  | **93.2 %** |
| 2 s    | 81.4 % |
| 5 s    | 49.0 % (p50 = 4.8 s) |
| 30 s   | 9.8 % |

Conclusion: no quiescence window fixes this. Tool-using turns are
mis-accepted by *default*; the visible 023 failures (lost `hive_actions`,
consumed kickoff turn) are the subset where it mattered.

## 2. The `turn_duration` sentinel is deterministic

CC writes `{"type": "system", "subtype": "turn_duration", ...}` when a
turn actually completes. Measured across 1,942 sentinels:

- **File order: 1,942/1,942 after the turn's last assistant entry.**
  Zero exceptions, all CC versions present on this host.
- Lag after the final assistant entry: p50 = 119 ms, p95 = 2.3 s,
  p99 = 16 s, max = 43.6 s. (p50 beats today's 500 ms dead-wait.)
- The 023 smoke transcript (`a012a36d…`, CC 2.1.173): sentinel 158 ms
  after the lost final message.

Sentinel-less transcripts (172 with assistant entries): all are sessions
killed/abandoned mid-turn — they end on `permission-mode`/`last-prompt`
metadata entries. No *completed* turn without a sentinel was observed on
any fleet version.

`--continue` sessions retain prior turns' sentinels → matching must be
**count-based** (new sentinel since call start), same pattern as the
existing assistant-entry count.

## 3. Entry timestamps are event times, NOT write times

Verified live mid-turn: this session's transcript was 81 s stale while
the in-flight turn's text/thinking/tool_use were already produced —
entries flush in batches at API-response boundaries. Consequences:

- Only **file order** is trustworthy; never timestamp-vs-mtime
  arithmetic.
- The sentinel survives (file-order guarantee above); per-entry timing
  heuristics do not.
- A final multi-block message (thinking + text) lands as one flush, all
  entries stamped with the response's final `stop_reason`.

## 4. `stop_reason == end_turn` is a filter, not a signal

Of 2,650 `end_turn`-stamped assistant entries, **1,271 (48 %) are
non-final** — more assistant entries follow before any sentinel or new
prompt (multi-block final messages + organic multi-response turns; no
hooks are configured on this host, so not hook continuations). Of those
non-final `end_turn` entries, the quiet gap to the next entry exceeds
30 s in 2.5 % of cases and 60 s in 1.0 %.

So: `end_turn` on the last entry + quiescence is a usable *hardened
fallback* (it kills the exact smoke failure — the wrongly-accepted entry
was stamped `tool_use`) but can never be deterministic.

## 5. Gates never emit a sentinel

A gated turn (ExitPlanMode / AskUserQuestion / permission prompt) is
mid-turn — the PTY freezes on a menu and no sentinel arrives until the
gate is answered and the turn completes. Gate detection must stay on the
quiescence path and run before turn acceptance, exactly as today
(`transcript_reader.py:201-210`).

## 6. Rejected alternatives (with the killing constraint)

| Alternative | Killed by |
|-------------|-----------|
| Widen the quiescence window | §1 — 9.8 % loss even at 30 s; adds latency to every turn |
| `stop_reason`-primary | §4 — 48 % of `end_turn` entries are non-final |
| Model-emitted done-marker (prompt/skill) | In-band: arrives inside the racing channel, lost with the message it protects; per-turn model compliance is probabilistic (cf. the hive_actions parse-loop incident) |
| CC Stop hook → push to Hive | Harness-layer and deterministic (right family), but needs a transport + per-entity settings injection + a guard so global `~/.claude/settings.json` hooks don't fire on the developer's own sessions — three 013-shaped moving parts to win latency we don't need. Parked as the future event-driven option. |

## 7. Blast radius

- One production caller: `PtySession.send()`
  (`src/hive/runtime/pty_session.py:319`), timeout = 180 s, gate
  re-await loop. No call-site changes needed — the contract
  (`(text, usage) | Gated`, TimeoutError on no progress) is unchanged.
- Tests: `tests/runtime/test_transcript_reader.py` (699 lines,
  synthetic-transcript fixtures, injectable `quiescence_ms`). Existing
  fixtures are sentinel-less → they route to the fallback path under the
  new logic; the fallback window must be injectable so the suite stays
  fast.
