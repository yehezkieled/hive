# 020 — Questions

The unknowns going in. Each is resolved in `research.md` / `design.md`;
the answer is summarised here with a pointer.

## Trigger & decision

1. **What fires a bounce — a blind count of consecutive timeouts, or a
   count plus liveness checks?**
   → **Safety checks** re-run at decision time: `is_parked_at_gate`,
   `workflow_active`, and `entity.awaiting_decision` (029 defense-in-depth).
   Bounce only when a stall threshold (default 2) is reached **and all
   checks are clear**. See `design.md` §D1.

2. **Does a held-off timeout (entity legitimately waiting) count toward
   the stall threshold?** → No. Only a timeout where both safety checks
   are clear is a genuine stall; a held-off timeout neither increments
   nor is treated as a jam. A successful turn resets the count. §D4.

3. **Is the permission-prompt jam distinguishable from a bridged
   plan/ask gate?** → Yes, for free. The detector
   (`gates.py:68-93`) only ever produces `plan`/`ask` gates; a permission
   prompt has no transcript signature (ADR 0005) so it is never
   registered → `is_parked_at_gate` is **False** for exactly the jam we
   want to kill. `research.md` §R3.

## Recovery mechanism

4. **How is the conversation preserved across the kill?** → Automatically.
   Respawn goes through `_get_or_create_adapter`, and `--continue` is
   added whenever the prior `.jsonl` exists (`pty_session._has_prior_session`).
   No extra wiring. §R2.

5. **Where do the bounce logic and its counters live?** → Orchestration
   in `message_dispatcher.send_to_entity` (the single chokepoint every
   `TimeoutError` already flows through, where auto-compact recovery
   already lives); counter state in a per-entity dict on `ProcessManager`
   so it **survives the respawn**. §D2.

## Flap protection

6. **What stops an endless kill→respawn→kill loop?** → A **time-windowed**
   flap-guard: M bounces within W (default 3 / 30 min) → stop bouncing,
   move the entity to `ERROR`, notify with the reason. §D3.

## Diagnosis

7. **Can Hive tell the user *why* it bounced, not just *that* it did?**
   → Yes, best-effort. At bounce time, assemble a reason from the
   session-state file (`status`/`waitingFor`), `is_alive`, and the last
   transcript entry; fall back to "cause unknown". It is **advisory** —
   the bounce decision never depends on it. §D5.

8. **Do we use the `~/.claude/sessions/<pid>.json` `waitingFor` field?**
   → Yes, but as the **diagnosis** (the message), not the **trigger**
   (the decision). Keeps recovery robust against an undocumented CC field
   while still explaining the jam. §D5, ADR 0015.

## Scope & sequencing

9. **Does 020 depend on 029 (now MERGED, #157)?** → No build dependency.
   029 shipped as a maestro→user *conversational decision channel* (not a
   gate-bridge rework), adding the durable `entity.awaiting_decision` flag.
   020's hook point (`send_to_entity:218`) is untouched; safety-check #1
   uses only the public `is_parked_at_gate` contract (Ticket 028). 020
   honors `awaiting_decision` as a defense-in-depth check, though it cannot
   currently overlap with an in-flight send. `research.md` §R5.

10. **Does 020 need 030 to land first?** → Soft, not hard. The
    `workflow_active` safety check is a second guard on the exact
    false-timeout 030 chases, so 020 is robust even if 030's fix is
    imperfect. §R4.

11. **Direct or fan-out lane?** → **Direct** — one cohesive feature, one
    PR. §design / `plan.md`.
