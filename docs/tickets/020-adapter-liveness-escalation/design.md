# 020 — Design

Auto-bounce a jammed PTY session: detect a genuine stall, kill + respawn
(conversation preserved), explain why, and stop if it flaps. Grounded in
`research.md`; decisions were grilled against CONTEXT.md + the ADRs.

## D1 — The bounce decision: stall count + two safety checks

On a turn `TimeoutError`, do **not** kill blindly. Run two safety checks
first — each a reason to *hold off*:

```
  Turn raised TimeoutError
        │
        ├─ is_parked_at_gate(name)? ─ yes ─▶ HOLD OFF   (waiting for the user)
        │            │ no
        ├─ workflow_active(window)? ─ yes ─▶ HOLD OFF   (Workflow still running)
        │            │ no
        ▼
     genuine stall → stalls += 1
        └─ stalls >= THRESHOLD (default 2) → BOUNCE
```

Checks are re-run **at decision time** (not inferred from the fact that a
timeout happened), so 020 stays correct even if 030's liveness-reset is
imperfect (`research.md` §R4) and even as 029 changes gate registration
(§R5 — 020 uses only the public `is_parked_at_gate` contract).

Why the permission jam is the thing that gets through both checks: it is
undetectable as a gate (ADR 0005) so check #1 is False, and it has no
Workflow so check #2 is False. The *absence* of a registered gate is the
kill signal (`research.md` §R3).

## D2 — Placement: orchestration at the chokepoint, state above the adapter

- **Orchestration** wraps the `adapter.send_turn` call in
  `MessageDispatcher.send_to_entity` (`message_dispatcher.py:217-218`) —
  the one point every turn's `TimeoutError` flows through, beside the
  existing auto-compact recovery it mirrors.
- **State** lives in a per-entity dict on `ProcessManager`, e.g.
  `_liveness[name] = {"stalls": int, "bounces": deque[float]}`, alongside
  the existing `_compacting` / `_parse_failure_budget` maps.

The state **must not** live on the adapter: the bounce destroys and
recreates the adapter, so an adapter-held flap counter would be wiped
exactly when it must fire. Manager-level state survives the respawn.

## D3 — Flap-guard: time-windowed, then give up loudly

Record each bounce time in `bounces`. Before bouncing, prune entries older
than `W` and check the count:

```
  len(bounces within last W) >= M  →  GIVE UP:
     1. do NOT respawn again
     2. entity.transition_to(ERROR)        (visibly broken, not silently dead)
     3. _notify(kind="error", <reason>)    "Gave up on otter — 3 bounces in
                                            30 min, still stuck on a permission
                                            prompt. Needs you."
     4. _audit("entity.bounce_failed", …)
```

Defaults: **M = 3 bounces, W = 30 min**, both configurable. Time-windowed
(not absolute) so a healthy entity that legitimately bounces a few times
over days keeps its self-healing; only *clustered* bounces count as a flap.
Precedent: the scheduler's "3 spawns / 120 min" windowed budget.

After give-up, recovery is a **human** action (message it, restart it).
020 stops the bleeding and surfaces it; it does not try to fix a genuinely
broken session.

## D4 — Counting rules

- `stalls` increments **only** on a timeout where both safety checks are
  clear. A held-off timeout neither increments nor counts as a jam — so a
  maestro you take 7 minutes to answer is never bounced the instant it
  resumes.
- Any **successful** turn (sentinel-accepted, returns text+usage) resets
  `stalls` to 0.
- On a bounce, `stalls` resets to 0 (fresh process) and the bounce time is
  appended to `bounces`.
- `bounces` is pruned by the window, never fully cleared — that is what
  lets the flap-guard see across respawns.

## D5 — Best-effort reason (advisory, never load-bearing)

At bounce time, assemble a human reason from the sources in `research.md`
§R6 (first hit wins): `waitingFor`/`status` → `is_alive` → last transcript
entry → "cause unknown". Extend `_parse_session_id` (or add a sibling) to
also surface `status`/`waitingFor`; the bounce notification and the audit
`details` carry the reason.

Hard rule: the **decision** in D1 never reads `waitingFor`. The reason is
for the *message* only. If CC drops or renames the field, the bounce still
fires on the safety-check logic; the user just gets "cause unknown". This
buys the explanation without making recovery depend on an undocumented
interface (`research.md` §R6 `CONFIRM AT BUILD`).

## D6 — Config knobs

Module-level constants (mirroring `AUTO_COMPACT_*`), overridable:

| Knob | Default | Meaning |
|------|---------|---------|
| `BOUNCE_STALL_THRESHOLD` | 2 | consecutive genuine stalls before a bounce |
| `BOUNCE_FLAP_MAX` (M) | 3 | bounces within the window before giving up |
| `BOUNCE_FLAP_WINDOW_S` (W) | 1800 | the flap window, seconds |

The reader's 180s no-progress timeout itself is **unchanged** — out of
020's scope.

## Notification & audit vocabulary

| Event | Channel | Text / action |
|-------|---------|---------------|
| Recovered | `_notify(kind="info")` | `Auto-bounced <entity> — <reason>. Conversation resumed.` |
| | `_audit("entity.bounce", details={reason, stalls, session_id})` | |
| Gave up | `_notify(kind="error")` | `Gave up on <entity> — <M> bounces in <W>, <reason>. Needs you.` |
| | `_audit("entity.bounce_failed", details={reason, bounces})` | |

## Alternatives considered

- **Blind consecutive-timeout count (no safety checks).** Simpler, matches
  a literal reading of the acceptance. Rejected: kills a maestro waiting on
  the user, and kills a healthy Lead mid-Workflow (030's false-timeout).
  The two checks are cheap reads that already exist.
- **`waitingFor` as the trigger** (bounce precisely when the field is set).
  Sharper, fewer false stalls. Rejected as the *decision* input: it is an
  undocumented CC field; making recovery depend on it is fragile, and it
  only covers the permission class, not wedged-TUI / hung-process jams. Kept
  as **advisory diagnosis** (D5) — the best of both.
- **Counter on the adapter.** Natural-feeling, but wiped by the very bounce
  it must survive (D2). Rejected.
- **Detect the permission prompt and bridge it** (close ADR 0005's gap).
  Much larger; ADR 0005 found no transcript signature. 020 recovers instead
  of detecting — orthogonal and cheaper.
- **Give up after M absolute bounces.** Rejected: permanently disables
  self-healing for a long-lived entity. Time-windowed (D3) instead.

## Cross-ticket dependencies (assumptions baked in)

- **029** (in flight): 020 assumes post-029 gate-registration semantics and
  uses only the public `is_parked_at_gate` contract. Regression test: a
  gated maestro is never bounced.
- **030**: soft. `workflow_active` (check #2) covers the false-timeout class
  030 fixes, so 020 does not block on 030. 030-first is cleaner.
- **021** (maestro→user routing): independent. 020 notifies via the
  dispatcher (`_notify`), not 021's router.

## Documentation impact (cross-cutting)

- **CONTEXT.md** — new glossary term **Auto-bounce**.
- **ADR 0015** — "Auto-bounce jammed sessions, guarded by liveness checks"
  (extends ADR 0005's "can't detect → recover" posture; records the
  advisory-not-trigger call on `waitingFor`).
