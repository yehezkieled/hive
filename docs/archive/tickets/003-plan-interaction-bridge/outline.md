# Outline — interactive-gate bridge

Implementation structure for the approach in [`design.md`](design.md)
(decisions) and [ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md).
This file sketches the **modules and their interfaces**; the actionable
slices live in [`plan.md`](plan.md).

The shape: three **deep modules** (pure-ish, unit-testable without a
live PTY) behind thin **glue** that wires them into the existing
`TranscriptReader` / `PtySession` / `ProcessManager`. The deep modules
hold the logic; the glue holds the I/O.

## Deep modules

### ① GateDetector — `detect(entries) -> Gate | None`

Pure function over parsed transcript entries. Decides whether the Turn
is parked on an unanswered gate, and which kind.

- A `Gate` = `{ kind: "plan" | "ask" | "permission", payload }`.
  - `plan` payload = the plan text (for surfacing).
  - `ask` payload = the question + the **option list**, read from the
    `tool_use` *input* (so we know the option index without scraping).
  - `permission` payload = the tool/command being requested.
- **Detection rule (structural, never screen):** a `tool_use` block
  (`ExitPlanMode` / `AskUserQuestion` / permission) with **no matching
  `tool_result`** for its `tool_use_id`, or an
  `attachment.type == "plan_mode"`. Match the structured block — **not**
  the bare string `"ExitPlanMode"` (research.md: it appears inside
  `deferred_tools_delta` and would false-positive).
- **Why deep:** the entire "is there a gate, what kind, what's its
  payload" question sits behind one signature over data. No PTY, no
  screen, no async. The `.jsonl` schema may drift; `entries -> Gate`
  does not. Unit-testable with fixture transcripts.

### ② KeystrokePlanner — `plan_keys(gate, decision) -> list[Key]`

Pure function. Translates a resolved decision into the exact keystrokes
for that gate's TUI menu.

- `decision`: plan → approve/deny; ask → chosen option index;
  permission → allow/deny.
- Output: plan-approve → Enter on the default row (or `"1"`);
  plan-deny → navigate to the reject row + Enter; ask → `Down × index`
  + Enter (cortexOS `selectOption` pattern); permission → `y`/`n`.
- **Why deep:** isolates the **one TUI-layout-coupled piece** ADR 0001
  flags as the known sensitivity. Everything else stays transcript-only.
  When a menu's row order changes, only this module changes.
  Unit-testable: `(gate, decision) -> keys`, asserted against expected
  sequences, no PTY.

### ③ GateCoordinator — `await resolve(gate) -> list[Key]`

Stateful orchestrator for **one** gate's lifecycle. The park-and-wake
dance lives here so the glue stays dumb.

- Create/attach a pending-approval row (`kind = gate`) → the durable
  surface (Telegram + web).
- Register an `asyncio.Event` doorbell keyed to `(entity, gate)`.
- `await` the doorbell — **no timeout, park forever**; optional nudge
  re-ping at ~60 min, then silence. Never auto-decide.
- On wake: read the decision off the resolved row, call
  `KeystrokePlanner.plan_keys`, return the keys.
- Exposes the hooks ProcessManager needs to exempt the Entity from
  idle-kill and suspend the reader timeout while awaiting.
- **Why deep:** PtySession just `await`s `resolve()` and injects the
  result — it never touches doorbells, rows, or nudge timers. Testable
  with a fake approval store + fake doorbell: ring it, assert the
  returned keys; assert it blocks until rung; assert the nudge fires.

## Glue (thin wiring into existing code)

| Seam | Change |
|------|--------|
| `runtime/transcript_reader.py` | In `await_next_assistant_turn`, run `GateDetector.detect` over new entries each poll. Add a **3rd outcome `Gated(gate)`** beside completed `(text, usage)` and `TimeoutError`. |
| `runtime/pty_session.py` | On `Gated`: Entity → `GATED`, suspend the 180s timeout, `keys = await coordinator.resolve(gate)`, inject via `_inject` / `sendKeySequence`, Entity → normal, resume awaiting the real turn. |
| Entity model / state | New `GATED` (WAITING) state + transitions: normal → `GATED` on detect, `GATED` → normal on inject. |
| `process/manager.py` | Doorbell registry (`asyncio.Event` keyed by entity/gate). Exempt `GATED` entities from `kill_idle_entities` via the existing `exempt_names`. Wire `/approve` → ring the doorbell. |
| `commands/dispatch.py` | `/approve` `/deny` resolve a **gate** approval (mark row + ring doorbell) alongside today's mode-request flow. Add a `kind` discriminator on the approval row. |
| `web/app.py` | Gate approval reuses the mode-request approve/deny endpoints, branched by `kind`. |

## Data flow

```
.jsonl ─poll─▶ GateDetector.detect(entries)
                    │
        None ───────┴────────── Gate(kind, payload)
          │                            │
   normal await loop      TranscriptReader returns Gated(gate)
                                       │
                          PtySession: Entity → GATED, suspend 180s timeout
                                       │
                          GateCoordinator.resolve(gate):
                            ├─ create approval row (kind=gate)  ─▶ Telegram + web
                            ├─ register doorbell (asyncio.Event)
                            ├─ await doorbell   (parked; idle-kill exempt; nudge ~60m)
                            │       ▲ /approve | /deny rings it  (dispatch.py / web)
                            └─ on wake → KeystrokePlanner.plan_keys(gate, decision)
                                       │ list[Key]
                          PtySession._inject(keys) → Entity → normal → resume await
```

## Test seams

- **GateDetector** — fixture `.jsonl`: plan_mode attachment;
  `ExitPlanMode`/`AskUserQuestion` `tool_use` with no `tool_result`; a
  normal completed turn; the `deferred_tools_delta` false-positive
  case → assert detected kind / `None`.
- **KeystrokePlanner** — `(gate, decision)` table → assert key
  sequences.
- **GateCoordinator** — fake approval store + fake doorbell → ring →
  assert returned keys; assert it blocks until rung; assert the nudge
  schedules.
- **Integration** — a recorded transcript that pauses on a gate →
  assert `Gated` outcome and that `_inject` is called with the planned
  keys (mock the PTY write).

## Decisions deferred to implementation (from design.md)

1. **New approval `kind` vs. reuse `mode_request` rows.** Lean: add a
   `kind` discriminator to the approval row, not a whole new store.
2. **Exact keypress per gate.** The plan menu may be 3 rows
   (yes-auto / yes-manual / no), not a clean 1/2 — confirm the live
   layout. Lives in KeystrokePlanner (the one TUI-coupled module).
3. **Restart-while-parked recovery.** A restart orphans the held Turn;
   the row survives with no coroutine. Re-spawn + re-detect, or mark
   stale and re-ask.
4. **③ permission-prompt transcript shape.** Capture a real one;
   confirm it's structurally detectable before building its detector.
5. **Nudge interval.** Default ~60 min, then silence. Tunable.

## Implementation order (→ slices in plan.md)

1. **Spine / tracer bullet:** `GATED` state + GateDetector(plan) +
   GateCoordinator + doorbell + PtySession injection + `/approve` +
   idle-kill exemption + timeout suspension → the end-to-end plan-gate
   round-trip. Blocks the rest.
2. AskUserQuestion gate (detector + KeystrokePlanner option-index).
3. Web-dashboard surface (reuse mode-request endpoints, `kind`).
4. No-answer nudge (~60 min re-ping).
5. Permission-prompt gate (capture + verify shape + implement) — HITL.
6. Restart-while-parked recovery.
