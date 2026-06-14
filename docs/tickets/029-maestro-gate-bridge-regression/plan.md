# Plan — Ticket 029: maestro interactive gate not bridged to user  (issue #144)

**Lane:** direct (one bug → one PR). Reader hardening (A) + ask-gate tests (F3)
ship together; the deployed reproduction (B) decides whether a cause-specific
fix (C) lands in the same PR or a follow-up slice. See `design.md` for A/B/C and
`outline.md` for the module sketch.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/runtime/transcript_reader.py` | modify | A — reorder `await_next_assistant_turn`: run `_detect_gate` first, quiescence-independent; return `Gated` before sentinel/strict/fallback branches. Parse entries once per poll. |
| `tests/runtime/test_transcript_reader.py` | modify | F3 — add `test_await_returns_gated_for_ask_gate`, `test_ask_gate_after_text_with_sentinel` (Run 1 regression guard), `test_ask_gate_after_text_without_sentinel`. |
| `tests/runtime/test_pty_session.py` | modify | F3 — add `test_send_handles_ask_gate_then_resumes` (mirror plan-gate test at `:280`). |
| `src/hive/runtime/pty_session.py` | modify *(conditional, C3)* | If the repro is inconclusive: assert/log-loud when `gate_coordinator is None` at adapter creation instead of silently building a detector-less session (`:224`). |
| `docs/adr/0015-gate-check-authoritative-over-sentinel.md` | created | done (this ticket). |
| `docs/tickets/029-*/` artifacts | created | done. |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (full suite — scoped runs miss failures).
- New `test_ask_gate_after_text_with_sentinel` **fails on `main`** (sentinel
  accepted first) and **passes** after the reorder — the regression is pinned.
- **Deployed re-smoke (B), required by S6 DoD — not optional):** restart
  `hive.service`, drive a maestro propose-and-wait turn via `POST /api/command`,
  and confirm on deployed code:
  1. the gate row is created and the question reaches Telegram;
  2. the turn holds open (no 180s timeout, no raw error);
  3. an answer injected back resumes the turn;
  4. capture which root cause fired (C1/C2/C3) from the maestro `.jsonl` +
     `journalctl` spawn line → route to `design.md`'s C-branch.

## Out of scope

- The scheduler poke exploiting the window — Ticket 028 (done).
- The no-progress timeout on long Workflow turns — Tickets 027 / 030.
- 019 (phase-confirmation gate) builds *on* this bridge — separate ticket,
  blocked by 029.

## Cross-cutting impact

- **ADR 0015** (new) amends ADR 0012's acceptance-ladder order. Append-only;
  ADR 0012 referenced, not edited.
- **CONTEXT.md** — no new term (existing "Interactive gate" covers ask/plan).
  Revisit only if the repro lands on C1 and gate kinds are extended.
- **README / DEPLOYMENT** — no operator-facing change.

## Build

Direct lane: one branch `ticket-029/gate-bridge-regression`, one PR that closes
#144. (This artifact set ships first on its own docs branch and leaves #144
**open** for the implementation PR.)
