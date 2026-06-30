# 048 — Useful Web Push deep-link (041 follow-up)

> Follow-up to [041](../041-web-push/). Small, frontend-weighted.

## What

Make tapping a Web Push notification land the user **on the thing that needs
them**, not just open the app. 041 shipped the deep-link as best-effort, which
in practice was a no-op (the focus target `[data-entity]`/`#card-<entity>` didn't
exist in the markup, and `.is-focused` had no styling), so a tap just opened the
PWA to wherever it was.

- **"Needs you" push** (`decision_request` / `mode_request` / `vault_action_pending`)
  → URL `/?reply=<entity>`: open the chat drawer, pre-address the composer with
  `/m:<entity> ` and focus it, so the user can type the answer immediately.
- **"Run ended" push** (`workflow_completed` / `workflow_failed`) → URL
  `/?focus=<entity>`: scroll to + briefly highlight that entity's card.

## Why

The point of the async ping (041) is to **skip the hunting** — the push tells you
something needs you; the tap should drop you where you act. Without this, the
deep-link is decoration. This tightens the loop-engineering surface: a decision
ping → reply in two taps.

## Acceptance

- `WebPushChannel` builds `/?reply=<entity>` for the needs-you kinds,
  `/?focus=<entity>&run=<id>` for run kinds (unit-tested).
- A maestro card carries `data-entity="<name>"`; `.maestro-card.is-focused` flashes.
- `window.hiveDeepLink(url)` (landing.html) drives both paths; the SW
  `notificationclick` bridge (warm `postMessage`) and the cold-open URL both
  route to it.
- On a real iPad: tapping a **decision** push opens the chat aimed at the maestro,
  ready to reply; tapping a **run** push scrolls to + highlights the card.

## Non-goals

- Rendering/posting the reply automatically (user still types + sends).
- A bespoke decision panel (reuses the 038 chat bubble + composer).
- Deep-link on the `/dashboard` page (chat lives on `/`).
