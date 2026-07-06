# ADR 0028 — Unified "needs-you" is a polled server-side rollup (reverses 039's split; gate dropped)

- **Status:** Accepted
- **Date:** 2026-07-06
- **Sprint:** [2026-Q2-S10](../sprints/2026-Q2-S10.md) (web redesign)
- **Ticket:** [051](../tickets/051-unified-needs-you-lane/)
- **Relates to:** [ADR 0027](0027-web-delegators-desk.md) (the Delegator's Desk —
  the needs-you lane is its hero), [ADR 0024](0024-decision-channel-entity-keyed.md)
  (the decision channel this surfaces; the rollup stays entity-carrying), Ticket 039
  (the awaiting-you rollup this generalizes, and whose mode/vault exclusion it
  reverses), [ADR 0026](0026-web-push-notification-channel.md) (the async ping;
  `ALERT_KINDS` stays a separate wire contract), [ADR 0005](0005-permission-prompt-undetectable.md)
  (why gates are undetectable under bypass)

## Context

The web nags for input in four disjoint places — two header "bell" popups + three
SSE chat bubbles — with approve/deny hand-rolled five times and popup chrome
triplicated. There is no single notion of "what needs me": the bell badge
(vault+mode, stale and disagreeing with its own popup), 039's card filter
(decision+gate bool, mode/vault **deliberately** excluded), the SSE bubbles
(mode+vault+decision), and Web Push `ALERT_KINDS` each define a *different* subset.
Ticket 051 — the hero of the S10 Delegator's Desk (ADR 0027) — consolidates these
into one actionable feed reused by the Stack home (052) and the Work view (053).

Two properties of the existing code force the shape of the fix:

- **There is no CLEAR event.** `clear_awaiting_decision` fires no notification
  (`manager.py:214-225`); only `vault_action_resolved` emits. An SSE-driven feed
  would SET on the event but never CLEAR — an answered item would stick until a
  reload. 039 already hit exactly this and chose the htmx poll for it.
- **SSE is lossy and vault has no reseed.** The SSE queue is bounded / drop-oldest
  (`sse.py:24-62`), so a sleeping iPad tab silently loses frames; and pending vault
  has no read endpoint at all — a reload loses a pending payment. A push-only feed
  structurally loses items.

## Decision

**Build the unified `needs_you` set as a server-side rollup in `view_model.py`,
delivered on the existing htmx poll (5s); demote SSE to a "re-poll now" nudge, not
the render source.** The rollup is re-derived from live state every poll, so it
**self-heals**: an item resolved on any surface (another tab, Telegram) simply
stops appearing on the next poll — no client bookkeeping, no server resolve event.

Three consequences are settled as part of this decision:

- **One canonical set = `{decision, mode, vault, errored}`.** This reverses 039's
  deliberate "mode/vault stay on the bell" split (`view_model.py:132`) — that split
  is what created the four disjoint notions. `errored` (`EntityState.ERROR`,
  invisible as "dormant" today) becomes a first-class net-new lane kind whose only
  resolving action is `/reset` (nothing on the message path clears `ERROR`).
- **Gate is dropped, not migrated.** Gates are dead end-to-end post-029: both
  emitting tools (`ExitPlanMode`/`AskUserQuestion`) are bare-name-denied to both
  coordinator roles, the permission gate never fires under bypass (ADR 0005), and
  `GET /api/gates/pending` already returns `[]` because the created `approver='user'`
  never intersects the endpoint's filter. The web read surface + endpoint are
  deleted; the POST act routes + the in-memory `is_parked_at_gate` detector stay as
  an inert safety net.
- **No new wire kind, no new act endpoint.** The rollup is a server-side *view* over
  existing state — `ALERT_KINDS` (the push / Telegram-suppression / email
  cross-surface contract) is untouched, and the lane switches over the existing
  per-kind POST endpoints. Each item carries one actor field (`entity`, the full
  dotted address), so 052's hero count and 053's per-tab filter fall out of the same
  list — consistent with ADR 0024's entity keying.

## Consequences

- **Positive:** one source of truth for "what needs me," reused verbatim by three
  surfaces; self-heals cross-surface with zero client bookkeeping; closes the vault
  reload-loss and the mode approver disjoint-set defects for free; decouples the
  lane from wire-`kind` churn.
- **Accepted trade-off:** up to ~5s latency for a new item to appear (the SSE nudge
  narrows it) plus a per-poll re-render, in exchange for self-heal, cold-open
  reseed, and cross-surface reusability. A live SSE-driven feed would be
  lower-latency but sticks on resolve and loses items on a sleeping tab.
- **Hard to reverse:** 052 and 053 wire directly to the `view["needs_you"]` shape
  and the mount contract (the partial + the `data-nyi-entity` header anchor + the
  shared JS handler). Changing transport or item model later re-touches three
  surfaces — which is why this is recorded.
- **Known push gap (deferred to 041):** gate/errored are absent from `ALERT_KINDS`,
  so they never ping the lock screen; and `web_push.py` has no `else` branch (an
  unhandled ALERT kind fails silently). Out of 051; flagged for the push-side
  follow-up.

## Alternatives considered

- **SSE-driven push feed** — rejected: no CLEAR event (items stick until reload),
  lossy on a sleeping tab, no vault reseed, and not server-renderable for 052/053.
- **Migrate `gate` into the lane** — rejected: dead end-to-end; migrating carries
  dead machinery forward.
- **Unified `POST /api/needs-you/{key}/act` endpoint** — rejected: a new act
  mechanic (the explicit non-goal) that would rewrite four pinned endpoint tests and
  re-point the 048 deep-link, only to re-fan-out by kind internally.
- **Keep 039's mode/vault-on-the-bell split** — rejected: that split is the source
  of the four disjoint "needs-you" notions this ticket exists to unify.
