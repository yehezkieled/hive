# Outline — Ticket 029

Implementation structure for the reader hardening + repro + cause-specific fix.
Direct lane: one PR carries A (hardening) + F3 (tests); B (repro) gates whether
C (cause-specific) is needed in the same PR or a follow-up.

## Module sketch

### 1. `src/hive/runtime/transcript_reader.py` — `await_next_assistant_turn`
Reorder the per-poll acceptance ladder so the gate check is first and
quiescence-independent.

```
# per poll, once current_count > initial_count:
entries = self._read_entries(session_path)          # parse once
gate = self._detect_gate(entries)                   # AUTHORITATIVE — first
if gate is not None:
    return Gated(gate)                              # no sentinel/quiescence gate

# only if NOT a gate, the existing ladder runs unchanged:
#   sentinel acceptance (242-246)
#   strict acceptance + quiescence (253-300):
#     pending-tool guard, hardened fallback
```
Notes:
- Read entries once per qualifying poll and reuse for the pending-tool /
  fallback branches (avoid double parse).
- `_detect_gate` returns `None` instantly when no detector → non-bridged
  sessions unaffected (`transcript_reader.py:314-315`).
- Keep the no-progress deadline + `workflow_active` reset (`:213-227`) intact.

### 2. Tests — `tests/runtime/test_transcript_reader.py`
- `test_await_returns_gated_for_ask_gate` — unanswered `AskUserQuestion` →
  `Gated`, `gate.kind == "ask"`. (fills the ask-gate gap)
- `test_ask_gate_after_text_with_sentinel` — **the Run 1 shape**: assistant
  text block + unresolved `AskUserQuestion`, *plus* a `turn_duration` sentinel
  appended → still `Gated`, not `(text, usage)`. Fails on current order, passes
  after the reorder. (the regression guard)
- `test_ask_gate_after_text_without_sentinel` — same minus the sentinel
  (covers the quiescence-race variant).
- Reuse existing `_assistant_entry` / `_sentinel_entry` / `_ask_*` helpers.

### 3. Tests — `tests/runtime/test_pty_session.py`
- `test_send_handles_ask_gate_then_resumes` — mirror
  `test_send_handles_plan_gate_then_resumes` (`:280`): reader yields
  `Gated(ask_gate)` then a real turn; assert `coordinator.resolve` awaited once
  and the chosen-option keys injected. (proves the bridge engages, no
  zero-activity)

### 4. Deployed reproduction (B) — not code, a smoke script/checklist
- `POST /api/command` to drive a maestro propose-and-wait turn on deployed
  code (memory: live-smoke-via-api-command).
- Capture: maestro `.jsonl` (is there an `AskUserQuestion` tool_use?),
  `journalctl --user -u hive.service` `PtySession` spawn line (detector
  wired?), and the gate row / Telegram surface.
- Outcome routes to the C-branch in `design.md`.

### 5. Cause-specific fix (C) — only the branch the repro selects
- C3 guard (cheap, include pre-emptively if repro inconclusive): at maestro
  adapter creation, assert / log-loud when `gate_coordinator is None` instead
  of silently building a detector-less session (`pty_session.py:224`,
  `lifecycle_manager.py:269-275`).
- C1 / C2: scoped as a follow-up slice if the repro shows tool-not-emitted or
  yolo-suppression (may need GateDetector extension or a spawn carve-out).

## Sequence

```
A (reader reorder) ─┬─ F3 tests (red → green)  ── one PR
                    └─ ADR 0015 (done)
        │
        ▼
B (deployed repro) ── identifies cause
        │
        ▼
C (cause-specific) ── same PR if cheap (C3 guard) / follow-up if C1/C2
```

## Risks / watch-items
- **Double-parse cost** — read entries once per poll, thread to later branches.
- **Don't disturb 030's liveness reset** — the `workflow_active` deadline reset
  must stay; 029 only reorders the acceptance checks above it.
- **Behaviour, not deletion (S6 risk)** — needs the deployed re-smoke (B), not
  just green units.
