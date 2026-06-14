# Plan — Ticket 029 (REDIRECTED): maestro→user decision channel  (issue #144)

**Lane:** direct — one coherent feature (the decision channel), built in the
dependency order in `outline.md`. It has hard cross-ticket edges: **021 is a
prerequisite**, **031 shares an alias resolver**, **019 is re-mechanized onto
it**. See `design.md` for the decisions and `research.md` for why the bridge is
retired rather than fixed.

> Replaces the first-pass plan (the abandoned reader-reorder). That fix hardened
> the bridge; we're retiring it.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/models/entity.py` | modify | add `awaiting_decision: bool = False` (+ `last_nudged_at`) — **before** the migration/restore reads it |
| `src/hive/bus/migrations/029_entity_awaiting_decision.sql` | create | `ALTER TABLE entities ADD COLUMN awaiting_decision BOOLEAN NOT NULL DEFAULT 0` |
| `src/hive/bus/entity_store.py` | modify | persist + restore the column (`upsert`, `_row_to_entity`) |
| `src/hive/bus/permissions.py` | modify | `can_request_decision`: allow maestro→`user` |
| `src/hive/process/message_dispatcher.py` | modify | `request_decision{to:user}` → route via the **021 user-sink**, share the **031 alias resolver**, set `awaiting_decision`, **truncate trailing actions**, return a failure signal on dispatch error |
| `src/hive/bus/router.py` *(or user dispatch path)* | modify | clear `awaiting_decision` only on a **user-sourced** inbound |
| `src/hive/process/scheduler.py` | modify | skip a poke when `is_parked_at_gate(e) or e.awaiting_decision`; reuse the 3600s nudge cadence |
| `src/hive/process/tool_policy.py` | modify | add `AskUserQuestion` to `_MAESTRO_DENY` |
| `src/hive/runtime/pty_session.py` | modify *(optional, Q3 guard)* | only if binary-confirm shows denial leaks: detect a stray gate → inject Esc + nudge (no translate/inject/park) |
| `tests/...` | modify/create | dispatcher / store / scheduler / permissions / tool_policy / clear-path (see outline) |
| `docs/adr/0017-conversational-decision-channel.md` | created | this PR (the redirect's ADR) |
| `docs/tickets/019-*/ticket.md` | modify | re-mechanize 019 onto `request_decision` (this PR) |
| `docs/tickets/029-*/` artifacts | modify | this PR (redirect) |

## Dependencies (must respect)

```
021 user router-sink ──▶ 029 request_decision{to:user}   (029 builds on 021's sink)
031 self.<team> alias ──▶ shared by the request_decision branch
029 decision channel ──▶ 019 phase-confirmation (019 consumes it; stays blocked-by-029)
```

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (full suite — scoped runs miss failures).
- Unit proof the redirect works: maestro `request_decision{to:user}` →
  routes to the notification dispatcher, sets `awaiting_decision`, truncates
  trailing actions, returns failure on dispatcher error; the flag round-trips
  upsert→restore; an awaiting entity is skipped by the scheduler; a peer
  message does **not** clear the flag.
- **Binary-confirm (013-class):** `--disallowedTools AskUserQuestion` blocks
  emission on the pinned binary (ExitPlanMode precedent says yes).
- **Deployed re-smoke (S6 DoD — required):** drive a maestro propose-and-wait;
  confirm the question reaches Telegram, the maestro is **not** advanced by a
  scheduler poke, your reply wakes it, and a restart mid-wait does not poke it
  into acting.

## Out of scope

- The vault `request_payment` rail — money keeps its own hard approve/deny path,
  never governed by `awaiting_decision`.
- Per-thread multi-maestro reply addressing — documented limitation; 031's
  domain.
- Tickets 027 / 030 (Workflow-turn timeout) and 028 (scheduler poke) — separate.

## Cross-cutting impact (reference docs)

- **ADR 0017** (new) — conversational decision channel over the native-gate
  bridge. (The first-pass reader-reorder ADR was abandoned, not published;
  ADR 0015/0016 belong to parallel tickets 020/025.)
- **CONTEXT.md** — applied with the implementation PR (not now, while the
  bridge is still live): revise *Interactive gate* and *Thinking skill*; add
  the decision-request / `awaiting_decision` concept.
- **019/ticket.md** — re-mechanized (this PR).
- **README / DEPLOYMENT** — no operator-facing change.

## Build

Direct lane, one feature PR that closes #144, built in the outline order. Unlike
the first-pass docs, the implementation touches `src/` → the build PR runs full
CI + a deployed re-smoke.
