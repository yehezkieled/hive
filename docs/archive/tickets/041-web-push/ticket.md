# 041 — Web Push (retire Telegram's alert role)

> ⚠ **Designated spill → S9** (or S8 stretch only). Large + **blocked by 040**.
> The "loop engineering" direction makes this the **first pull into S9**.

## What

Deliver notifications to a backgrounded PWA so Hive can ping the device without
Telegram: generate a VAPID keypair, add a `/api/push-subscribe` endpoint + a
subscription store, a service-worker `push` event handler that shows a native
notification, and a server-side delivery path that routes SSE-worthy events to
Web Push. Split delivery into **"run finished"** vs **"needs your decision"**,
with a deep-link back to the relevant run/decision.

## Why

This is the load-bearing feature for the Phase-4 thesis — *drop Telegram to
debug/log*. A backgrounded PWA can't ping the device without Web Push, so until
this lands Telegram stays the alert tier. Under "loop engineering" (human rarely
in the loop), the async ping is the primary way you learn a run needs you — the
single highest-value gap in the competitor scan (rank #2). iOS/iPadOS 16.4+ only,
and only for an installed PWA → **blocked by 040**.

## Acceptance

- VAPID keypair configured; `/api/push-subscribe` + durable subscription store.
- Service worker handles `push`, shows a native notification, deep-links on tap.
- Server delivers "finished" vs "needs-decision" notifications to subscriptions.
- Verified on an **actual iPad** (installed PWA, backgrounded, receives a push).
- Telegram's alert role can be turned down (not deleted) once parity is shown.

## Non-goals

- Pre-16.4 iOS fallback (unsupported; SSE-while-open remains).
- Replacing Telegram entirely (keep as debug/log).
- Rich notification actions beyond open/deep-link — later.

## Notes

Blocked by **040** (push only fires for an installed PWA). New: VAPID config in
`config.py`, push routes in `app.py`, subscription store, SW push handler. Large
→ spilled to S9 by design.
