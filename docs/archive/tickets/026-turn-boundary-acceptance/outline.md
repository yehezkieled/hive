# Outline — Ticket 026: turn-boundary acceptance

Implementation structure for the design in [`design.md`](design.md).
One module changes (`transcript_reader.py`); the caller contract is
untouched.

## 1. `src/hive/runtime/transcript_reader.py`

### 1a. Sentinel counting

Mirror the existing count-based pattern (`_count_assistant_entries`):

```python
@staticmethod
def _count_turn_sentinels(session_path: Path) -> int:
    # entries with type == "system" and subtype == "turn_duration"
```

Implementer's choice: fold both counts into one file scan
(`_scan_counts() -> tuple[int, int]`) since each poll currently re-reads
the file per count — keep it minimal either way.

### 1b. `await_next_assistant_turn` — new signature

```python
async def await_next_assistant_turn(
    self,
    session_path: Path,
    *,
    timeout: float = 180.0,
    quiescence_ms: int = 500,          # gate-check quiescence (unchanged)
    fallback_quiescence_s: float = 30.0,  # NEW: heuristic-acceptance window
) -> tuple[str, dict] | Gated:
```

Snapshot at call start: `initial_count` (assistant entries, unchanged)
**and** `initial_sentinels` (new). Count-based matching is mandatory —
`--continue` transcripts retain prior turns' sentinels.

### 1c. Poll-loop ladder (replaces the single quiescence branch)

```
per tick:
  stat mtime; mtime advance → reset no-progress deadline   (unchanged)

  RUNG 1 — gate check (unchanged semantics):
    if new assistant entry AND quiet ≥ quiescence_ms:
        parse entries; unanswered gate? → return Gated

  RUNG 2 — sentinel acceptance (primary):
    if sentinel_count > initial_sentinels:
        return _extract_last_turn(...)        # no quiescence wait

  RUNG 3 — fallback acceptance (sentinel-less only):
    if new assistant entry AND quiet ≥ fallback_quiescence_s
       AND last assistant entry stop_reason == "end_turn"
       AND it has a non-empty text block:
        log ERROR (first fire for this session_path; WARNING after)
        return _extract_last_turn(...)

  pending tool_use in last entry → reset deadline           (unchanged)
  deadline exceeded → TimeoutError                          (unchanged)
```

Ordering notes:

- Rung 1 before rung 2 mirrors today's gate-before-acceptance rule
  (ADR 0004/0005). The two cannot both be true for one turn (a gated
  turn never emits a sentinel), so the order is defensive, not
  load-bearing.
- Rung 2 does **not** wait for quiescence: file order guarantees the
  final assistant entry precedes the sentinel (research §2), and the
  sentinel itself was the last write.

### 1d. Fallback log state

`self._fallback_seen: set[Path]` on the reader instance — first
acceptance per session path logs
`ERROR "turn-end sentinel absent in %s — acceptance is heuristic; CC
transcript format may have changed (ADR 0012)"`, later ones WARNING.

### 1e. New helpers

```python
@staticmethod
def _last_assistant_entry(entries) -> dict | None      # shared by guards
@staticmethod
def _is_heuristic_final(entry) -> bool                 # end_turn + has text
```

(`_has_pending_tool_use` already walks to the last assistant entry —
extract the shared walk.)

## 2. `src/hive/runtime/pty_session.py`

**No changes.** `send()` keeps calling with defaults; contract
(`(text, usage) | Gated`, TimeoutError) unchanged.

## 3. Tests — `tests/runtime/test_transcript_reader.py`

### New cases

| Test | Asserts |
|------|---------|
| `test_does_not_accept_during_post_tool_thinking_gap` | resolved tool_use + > 2 s silence + more entries later → reader still waiting (the ticket's headline acceptance criterion; kill-switch for the 023 bug) |
| `test_smoke_timeline_replay_yields_final_message` | the `a012a36d` sequence as a fixture → returns the final text (with its hive_actions), not the partial |
| `test_sentinel_acceptance_is_count_based` | pre-seeded transcript with stale sentinels (`--continue` shape) → waits for a NEW sentinel |
| `test_sentinel_accepts_without_quiescence_wait` | entry + sentinel written together → accepted on next poll, no 500 ms wait |
| `test_fallback_accepts_endturn_text_after_window` | sentinel-less, `end_turn` + text, quiet ≥ injected window → accepted; caplog shows ERROR first, WARNING second |
| `test_fallback_rejects_tool_use_stamped_entry` | sentinel-less, last entry `stop_reason=tool_use` → never accepted → TimeoutError |
| `test_fallback_rejects_textless_endturn` | sentinel-less, thinking-only `end_turn` entry → not accepted |

### Updated cases

Existing happy-path fixtures are sentinel-less and their entries carry no
`stop_reason` — under the new ladder they would time out. Update the
happy-path fixtures to append a `turn_duration` entry (the realistic
modern-transcript shape). Keep gate tests as-is (gate rung is
quiescence-based and unchanged); keep timeout tests as-is (no assistant
entry at all). Tests that exercise the fallback pass a small
`fallback_quiescence_s` so the suite stays fast.

### Sanity check

`tests/runtime/test_pty_session.py` mocks/feeds the reader — verify no
fixture there depends on quiescence-only acceptance.

## 4. Verification (full gate)

```
ruff check src/ tests/ && ruff format --check src/ tests/
pytest -m "not integration"
```

Then the live legs deferred from 023 (its smoke's remaining legs are
026's verification, per the ticket): deploy, spawn a lead through a
maestro turn that mixes tool calls with a final `hive_actions` proposal,
confirm delivery end-to-end on deployed code.
