# Questions — Ticket 026: turn-boundary acceptance

The unknowns going into the grill. Most were answerable by code/transcript
exploration (marked ✅ with the answer); the rest are judgment calls for the
grill (marked 🔥).

## Answered by exploration (2026-06-12, 556 transcripts on this host)

**Q1 ✅ Is the bug real, and is it a race or the default?**
Real, and worse than the ticket says: it's the *default* on tool-using
turns. Acceptance = new assistant entry + 500 ms mtime quiescence
(`transcript_reader.py:198-221`); the #82 guard passes the moment a
`tool_result` lands. Fleet-wide, post-tool thinking gaps are p50 = 4.8 s;
**93.2 %** exceed 500 ms. Even a 30 s window still loses ~10 %. Widening
the window is not a fix at any value.

**Q2 ✅ Does the `turn_duration` sentinel exist, and is its ordering
reliable?**
Yes. 1,942 sentinels across the host, **1,942/1,942 in file order after
the turn's last assistant entry**, zero exceptions, all CC versions
present (2.1.113 → 2.1.173). Lag after the final assistant entry:
p50 = 119 ms, p95 = 2.3 s, p99 = 16 s, max = 43.6 s. The 023 smoke
transcript (`a012a36d`, CC 2.1.173) shows it 158 ms after the lost final
message.

**Q3 ✅ What about transcripts with no sentinel?**
172 transcripts have assistant entries but no sentinel — all are sessions
killed or abandoned mid-turn (they end on `permission-mode` /
`last-prompt` metadata entries). On the pinned fleet versions, a
*completed* turn without a sentinel was not observed.

**Q4 ✅ Are entry timestamps write times?**
**No — event times.** Verified live: mid-turn, this session's transcript
was 81 s stale; the in-flight turn's text/thinking/tool_use entries were
not yet on disk. CC flushes entries in batches at API-response
boundaries. Consequence: only **file order** can be trusted, never
timestamp arithmetic against mtime. The sentinel's file-order guarantee
(Q2) survives this; per-entry timing reasoning does not.

**Q5 ✅ Is `stop_reason == end_turn` a sufficient turn-end signal?**
**No.** 2,650 `end_turn` entries examined: 1,271 (48 %) are followed by
more assistant entries before any sentinel/new prompt (multi-block final
messages + organic multi-response turns — no Stop hooks are configured,
so it isn't hook continuations). Of those non-final `end_turn` entries,
2.5 % precede a quiet gap > 30 s — a quiescence+stop_reason fallback
misfires there. `stop_reason` survives as a *hardening filter* (the
wrongly-accepted smoke entry was stamped `tool_use`), not as a primary
signal.

**Q6 ✅ Do gates interact with the sentinel?**
A gated turn (ExitPlanMode / AskUserQuestion / permission prompt) is
mid-turn — no sentinel ever arrives. Gate detection must stay on the
quiescence path, checked before turn acceptance, exactly as today
(`transcript_reader.py:201-210`).

**Q7 ✅ What is the blast radius?**
One production caller: `PtySession.send()` (`pty_session.py:319`,
timeout=180 s, gate re-await loop). Tests: `test_transcript_reader.py`
(699 lines, synthetic-transcript fixtures, injectable `quiescence_ms`).
Existing fixtures are sentinel-less, so under sentinel-primary acceptance
they all route to the fallback path — the fallback window must be
injectable or the suite crawls.

**Q8 ✅ Must sentinel matching be count-based?**
Yes — `--continue` sessions hold stale sentinels from prior turns; same
count-since-call-start pattern as the existing assistant-entry check.

## For the grill 🔥

**Q9 🔥 Fallback philosophy: degrade loudly or fail loudly?**
When no sentinel ever arrives, does the reader (a) accept on hardened
quiescence (last entry `end_turn` + text-bearing + long quiet window)
with a loud warning, or (b) refuse heuristic acceptance and let the
no-progress timeout fail the turn? The 023 post-mortem argues for loud
failure over silent degradation; but per-turn 180 s timeouts mean a dead
fleet until a human intervenes.

**Q10 🔥 Fallback window value.**
30 s (2.5 % misfire on non-final `end_turn` quiet gaps) vs 60 s (1 %).
Latency irrelevant on the happy path (sentinel p50 119 ms beats any
window); only matters in a sentinel-less world.

**Q11 🔥 ADR?**
Candidate ADR 0012: deterministic turn-end sentinel over quiescence
heuristic — why `stop_reason` was rejected as primary, why a heuristic
path survives at all.

**Q12 🔥 Glossary.**
"Turn-end sentinel" as a CONTEXT.md term?
