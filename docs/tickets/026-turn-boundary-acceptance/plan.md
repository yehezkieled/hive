# Plan — Ticket 026: turn-boundary acceptance

Direct lane: one build PR. Replace quiescence-primary turn acceptance
with the sentinel ladder per [`design.md`](design.md) /
[ADR 0012](../../adr/0012-turn-end-sentinel-acceptance.md); structure per
[`outline.md`](outline.md).

## Issues

| Summary | Issue | Type | Blocked by |
|---------|-------|------|------------|
| Sentinel-primary 3-rung acceptance ladder + test matrix | [#104](https://github.com/yehezkieled/hive/issues/104) | AFK | — |
| Deploy + live verification (023's deferred smoke legs; closes the S5 DoD line) | [#105](https://github.com/yehezkieled/hive/issues/105) | AFK | #104 |


## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/runtime/transcript_reader.py` | modify | A: `_count_turn_sentinels` (count-based, `--continue`-safe); `fallback_quiescence_s: float = 30.0` param; replace the single quiescence branch with the 3-rung ladder (gate → sentinel → hardened fallback); `_fallback_seen` ERROR-then-WARNING logging; `_last_assistant_entry` / `_is_heuristic_final` helpers |
| `tests/runtime/test_transcript_reader.py` | modify | B: 7 new cases per outline §3 (headline: post-tool thinking gap NOT accepted; smoke-timeline replay yields the final message); update happy-path fixtures to carry a trailing `turn_duration` entry; fallback tests inject a small window |
| `tests/runtime/test_pty_session.py` | verify | B: confirm no fixture depends on quiescence-only acceptance; touch only if broken |
| `docs/tickets/026-turn-boundary-acceptance/ticket.md` | no-op | seed; already accurate |
| `docs/tickets/INDEX.md` | modify | C: flip 026 row to done at merge |

No changes: `src/hive/runtime/pty_session.py` (contract unchanged),
`gates.py` / `gate_coordinator.py` (gate rung is untouched).

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- Full `pytest -m "not integration"` green (full suite, not scoped — CI
  runs lint and format as separate gates)
- The two ticket acceptance fixtures pass: (1) resolved tool_use +
  > 2 s silent gap + later entries → not accepted early; (2) smoke
  timeline replay → final message with its `hive_actions`, not the
  partial
- No-progress timeout semantics (#78) intact: mtime advance and pending
  tool still reset the 180 s deadline
- **Live (deferred 023 smoke legs, per ticket Non-goals):** deploy from
  the main repo (`git -C ~/projects/hive pull` → restart
  `hive.service`), then a maestro→lead turn that mixes tool calls with a
  final `hive_actions` proposal delivers end-to-end on deployed code —
  this closes the S5 DoD line 023/026 both gate
- Spot-check `journalctl --user -u hive.service` for zero
  fallback-acceptance ERRORs during the live leg (sentinel path active,
  heuristic dormant)

## Out of scope

- Auto-bounce/escalation of jammed sessions (Ticket 020)
- Event-driven turn notification via CC Stop hooks (parked in design.md
  alternatives; own ticket if pursued)
- Extraction semantics (`_extract_last_turn`) and the
  `PtySession.send()` contract
- Re-running 023's already-verified legs (worktree floor, session
  pinning, maestro alias, peer routing)

## Cross-cutting impact

None remaining — the two side effects already landed with `design.md`:
ADR 0012 (new, append-only) and the **Turn-end sentinel** glossary entry
in `CONTEXT.md`. No `README.md`/`DEPLOYMENT.md` text describes the old
acceptance heuristic (checked 2026-06-12).
