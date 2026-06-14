# Design — Ticket 029 (REDIRECTED)

> **Redirect (2026-06-14).** The first pass fixed the *bridge* (reorder the
> reader so a native gate is detected before sentinel acceptance; ADR 0015).
> Grilling that against the domain showed the bridge is the wrong thing to
> harden: a maestro is a **conversational** entity, and a native TUI gate
> pushed over Telegram needs a fragile detect→translate→inject bridge that
> fights the grain. 029 is re-scoped to **the maestro→user decision channel**:
> the maestro asks in plain text via a `hive_action`, ends its turn, and waits.
> The native-gate bridge is retired for maestros. **ADR 0016 supersedes ADR
> 0015.** See `research.md` for why the bridge is untrustworthy.

## The model (what we're building)

A maestro asks you a directional decision the most native way Hive has — a
`hive_action` — and parks on a durable flag until you reply. No PTY freeze, no
menu, no keystroke injection.

```
maestro: request_decision{ to:"user", text:"Plan: nav+CSS. Approve?" }
   │  Hive routes the text to Telegram (via the 021 user router-sink)
   │  sets entity.awaiting_decision = true  (durable)
   │  TURN ENDS (remaining actions in the block are truncated)
   ▼
scheduler poke fires → entity.awaiting_decision? → SKIP   (nothing advances it)
   ▼
you (Telegram): "go" / "drop checkout" / "why CSS?"      (any user-sourced msg)
   │  clears awaiting_decision; wakes the maestro
   ▼
maestro reads your words and DECIDES: proceed / revise+re-ask / answer+re-ask
```

## Decisions (grilled this session)

| # | Decision | Detail |
|---|----------|--------|
| Q1 | **Soft stop, hard guarantee** | The maestro ends its turn after asking. A durable `awaiting_decision` flag marks it waiting; the scheduler **skips** any entity with the flag set. No turn is held open. |
| Q2 | **Mechanism = `request_decision{to:user}`** | Reuse the existing hive_action. Extend `can_request_decision` to allow maestro→`user`; route `to:"user"` through the **021 user router-sink**; set `awaiting_decision`; **truncate the rest of the action block** so "ask then act in the same turn" is impossible. |
| Q3 | **Retire native gates for maestros** | Add `AskUserQuestion` to `_MAESTRO_DENY` (`ExitPlanMode` is already there). Maestros can then emit **zero** native gates → the 003 bridge is vestigial. Optional cheap dismiss-guard (detect a stray gate, inject Esc, nudge) **only if** the binary-confirm shows bare-name denial leaks. **ADR 0016 supersedes 0015.** |
| Q4 | **Clear on a user-sourced reply; maestro interprets** | The flag clears on an inbound message **from the user** (not a peer entity). Hive does not parse intent — the maestro reads the reply and decides (a counter-question re-arms via a fresh `request_decision`). |
| Q5 | **`awaiting_decision` is durable** | A real DB column on `entities`, persisted on upsert, restored on boot — so a restart can't make the maestro forget it's waiting and get poked into acting. Surfaces on the dashboard. |

Naming: the flag is **`awaiting_decision`** (not `pending_approval` — "approval"
is already taken by vault payments and the old gate rows). It covers *any*
unresolved held decision, including a lead→maestro `request_decision` the
maestro hasn't answered. **Money is unaffected** — vault `request_payment`
keeps its own hard approve/deny rail.

## Red-team refinements folded in

A 4-agent adversarial pass (verdict: sound-in-spirit, needs-rework) surfaced
holes; all are addressed above + here:

- **Persistence is real plumbing, not notional.** `Entity` has no
  `awaiting_decision` field and the `entities` table has no column
  (`models/entity.py`, `bus/migrations/002_entities.sql`, latest migration
  028). Add the field **before** the migration/`_row_to_entity` or restore
  passes an unknown kwarg. → outline.md.
- **Clearing needs a source marker.** `router.Message` has only
  sender/recipient/content — no `from_user`. Clearing in the generic router
  drain would let a peer message false-clear the flag. → clear in the **user
  dispatch path by name**, or add `from_user` to `Message`.
- **`to:"user"` is unroutable today** twice over: `can_request_decision`
  returns `False` for maestros, and the `request_decision` branch does
  `_entities.get(action.to)` (drops `user`). Unlike the `message` branch it has
  **no alias resolution and no failure-return**. → reuse the 021 sink + a
  `_reject_action`-style failure path so the maestro never narrates fictional
  delivery.
- **Scheduler skip is a separate check.** `is_parked_at_gate` reads
  `gate_coordinator.pending_request_id` (a GATED-state check) — orthogonal to a
  flag on a RUNNING/IDLE entity. Add `or entity.awaiting_decision` as a second
  skip condition; don't overload `is_parked_at_gate` (preserves 028 semantics).
- **Nudge cadence.** The gate path re-pinged hourly via the coordinator. The
  flag path has no parked coroutine → reuse the 3600s cadence with a
  `last_nudged_at` guard, or a question can sit silently forever.
- **Restart-window reply race.** A reply that lands during shutdown→restore
  finds no awaiting entity. On restore, if the queue has user mail, clear the
  flag (or clear-on-restore and let the queued reply re-arm).
- **Multi-maestro addressing** (lower priority): if two maestros await and you
  reply with no addressee, which clears? Document the PA-default limitation;
  full per-thread addressing is 031's domain.

## Alternatives rejected

- **Reader-reorder bridge fix (the first 029 / ADR 0015).** Correct *if* we
  keep the bridge — but we're retiring it. Superseded, not built.
- **Keep a native gate just for 019.** This was the fork; you chose to
  re-mechanize 019 onto `request_decision`. Keeping a native gate would force
  `AskUserQuestion` to stay allowed and the bridge to stay live — re-accepting
  all the fragility. Rejected.
- **Wall-clock timeout on the wait.** Contradicts park-forever / never-auto-
  decide. Use the nudge instead.
- **Hive parses the reply for approve/deny (Q4 alt).** Re-creates the rigid
  structured-gate world; throws away non-keyword replies. Rejected.

## Confirm on the pinned binary (013-class assumptions)

- `--disallowedTools AskUserQuestion` (bare native name) actually blocks
  emission. **Evidence it works:** `ExitPlanMode` has been bare-name-denied to
  coordinators since Ticket 015 with no plan-gate freezes. Still verify.
- CC accepts **mixed** tokens in one `--disallowedTools` flag (bare names +
  `Skill()` tokens — `lifecycle_manager` merges them flat).
- (Only if building the guard) injecting Esc cleanly dismisses a stray TUI menu
  and leaves the PTY usable.

## Cross-ticket boundaries

- **021 (router-user-queue) — dependency.** Make `user` a first-class router
  sink (no-queue, direct-to-notification, with a failure-return). **Both**
  `message{to:user}` (021) and `request_decision{to:user}` (029) reuse that one
  delivery path — not two divergent ones. 029 builds *on* 021.
- **031 (lead addressing) — shared alias.** `request_decision` must use the
  same `self.<team>`/`<maestro>.<team>` alias resolver 031 adds for `message`.
  One resolver, both branches.
- **019 (phase-confirmation) — re-mechanized onto this.** 019's "confirm before
  acting" *is* `request_decision{to:user}` + `awaiting_decision` at the phase
  boundary — resolving 019's own open "hive_action vs native gate" question in
  favour of the action. 019 stays blocked-by-029 (it consumes this channel).

## Side effects on shared docs

- **ADR 0016** (new) — conversational decision channel over the native-gate
  bridge; **supersedes ADR 0015** (referenced, not edited — repo precedent).
- **CONTEXT.md** (declared, applied with the implementation): revise
  *Interactive gate* (no longer the maestro path) and *Thinking skill*
  (maestros pause via the message loop, not a mid-turn gate); add the
  decision-request / `awaiting_decision` concept.
- **019/ticket.md** updated to the re-mechanized model (this PR).
