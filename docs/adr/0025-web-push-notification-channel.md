# ADR 0025 — Web Push is a fourth NotificationChannel that filters by kind; Telegram's alert role is demoted by policy toggle, not deleted

- **Status:** Accepted
- **Date:** 2026-06-28
- **Ticket:** [041](../tickets/041-web-push/) (Web Push — retire Telegram's alert role)
- **Relates to:** [ADR 0023](0023-https-via-tailscale-serve-for-pwa.md) (the
  HTTPS/installed-PWA prerequisite this rides on), [ADR 0024](0024-decision-channel-entity-keyed.md)
  (the entity-keyed decision a "Needs you" push deep-links to), Ticket 017
  (the `workflow_completed`/`workflow_failed` events a "Run ended" push fires on),
  Ticket 040 (the PWA + service worker)

## Context

Phase 4's thesis is to **demote Telegram to debug/log** and make the installed
iPad PWA the daily driver. The missing piece is an **async ping**: a
backgrounded PWA cannot reach the device on its own, so until Web Push lands,
Telegram stays the only thing that pings you when you're away from the
dashboard.

Hive already has a clean outbound-notification architecture (Ticket 015): a
`NotificationChannel` protocol (one async `send(Notification)`), a
`NotificationDispatcher` that fans every event out to all registered channels
with per-channel error isolation, and a single emit point
`ProcessManager._notify(text, kind, data)`. Three channels register today —
Telegram, SSE, Email digest. Each `Notification` already carries a `kind`
(`decision_request`, `workflow_completed`, …) and a structured `data` dict
(`entity`, `run_id`, `question`, …). **Telegram's `send()` ignores `kind`/`data`
and relays only text** — it is an undifferentiated firehose.

Two design pressures fall out of that:

- **What should a push carry?** The ticket names two categories ("run finished",
  "needs your decision"). But pushing *only* those two literal kinds means
  turning Telegram down would silently lose approvals (`mode_request`,
  `vault_action_pending`) and failed runs (`workflow_failed`) — the other things
  you must not miss while away. So the push scope and the Telegram demotion are
  one coupled decision, not two.
- **Where does the "which kinds are alerts" logic live?** Either re-tag events
  at every `_notify` call site with a `push_*` kind, or let the new channel
  filter the existing kinds. The emit sites are spread across `message_dispatcher`,
  `workflow_watcher`, `approval_handler`, `scheduler` — touching all of them to
  add a routing concept is the wrong blast radius.

## Decision

**Add Web Push as a fourth `NotificationChannel` that filters the existing event
stream by `kind`. Demote Telegram's *alert* role behind a default-on policy
toggle; never delete the channel.**

1. **One new channel, zero emitter changes.** `WebPushChannel` implements the
   `NotificationChannel` protocol and registers with the dispatcher after the
   SSE broker. It filters to the **actionable set** — **"Needs you"**
   (`decision_request`, `mode_request`, `vault_action_pending`) and **"Run
   ended"** (`workflow_completed`, `workflow_failed`) — drops everything else
   (`workflow_started`, `quota_*`, `auto_bounce` success, `info`). No call site
   that emits `_notify` changes; routing is a property of the channel.
2. **Durable subscription store keyed by endpoint.** A `PushSubscriptionStore`
   (asyncpg, mirroring `ModeRequestStore`) backs `POST /api/push-subscribe`
   (`require_token`). Subscriptions key on the push `endpoint` (unique per
   device+browser) — Hive has no per-user identity (`HIVE_WEB_TOKEN` is a single
   shared secret), so there is nothing to key by but the device. A `410 Gone`
   from the push service prunes the row lazily inside `send`.
3. **VAPID via env, inert until set.** `HIVE_VAPID_PUBLIC_KEY` /
   `HIVE_VAPID_PRIVATE_KEY` / `HIVE_VAPID_SUBJECT` in `config.py`, matching every
   other secret. Empty keys → the channel no-ops, so merging 041 changes nothing
   until an operator generates and pastes keys.
4. **Telegram demoted by toggle, not deletion.** A `HIVE_TELEGRAM_ALERTS` flag
   (default **on**) gates the actionable alert-kinds in `TelegramBridge.send()`.
   041 ships it **on** — merging changes no pings. The operator flips it **off**
   only after the on-device parity smoke; Telegram then stays a debug/log
   surface. This realises acceptance #5 ("alert role *can* be turned down once
   parity is shown") without risking going dark.
5. **Deep-link is best-effort on top of durable state.** A push `data` carries
   `{url, entity, kind, run_id?}`; tap focuses an open client or opens
   `/?focus=<entity>[&run=<run_id>]`. A dropped or missed push loses nothing —
   decisions are durable on the entity row (ADR 0024) and runs are in the
   progress store.

## Alternatives rejected

- **Push only the two literal kinds (`workflow_completed` + `decision_request`).**
  Smallest, but turning Telegram down would then drop approvals and failed-run
  alerts. Rejected: it can't satisfy the demotion half of the ticket safely.
- **Re-tag events with a `push_*` kind at the emit sites.** Spreads a delivery
  concern across four `process/` modules and every future `_notify` caller.
  Rejected for blast radius; filtering belongs in the channel.
- **In-memory subscription store.** Lost on `hive.service` restart — and the
  whole point is reaching you across time. Rejected for a durable table.
- **Per-user subscription identity.** There is no user model; `HIVE_WEB_TOKEN`
  is shared. Rejected; key by `endpoint`.
- **Demote Telegram by default in 041.** Risks going dark before push is proven
  on a real iPad. Rejected; toggle defaults on, flipped manually post-smoke.
- **App-level TLS / caddy for the secure context.** Already solved by
  `tailscale serve` (ADR 0023). Rejected; no new infra.
- **Push the FYI/noise kinds too (`quota_*`, `workflow_started`).** Re-creates
  the firehose on the lock screen — the exact thing demoting Telegram is meant
  to fix. Rejected; those stay on SSE / Telegram-debug.

## Consequences

- Web Push is **purely additive** to the notification core: one channel class,
  one store, one migration, one endpoint, plus the SW/client edges. The
  dispatcher, the `Notification` model, and every emit site are untouched — a
  future channel (e.g. a desktop one) follows the same recipe.
- The **"alert role vs log role"** split is now canonical language (CONTEXT.md)
  and toggle-enforced, not just an aspiration.
- 041 merges **inert**: no VAPID keys → no pushes; toggle on → Telegram
  unchanged. The behaviour change is a deliberate operator action, not a
  side effect of the merge.
- **Migration / ADR number race:** migration `033` and this ADR `0025` are the
  next free numbers against `origin/main` at authoring time, but worktrees for
  Tickets 043/044/045 are in flight (044 also adds an ADR). Re-verify and
  renumber both at ship time if a parallel branch took them — a known Hive
  gotcha.
- If a real need ever appears for per-user subscriptions, rich notification
  actions, or pushing more kinds, each is an additive follow-up — none requires
  unwinding this design.
