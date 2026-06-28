# Questions — Ticket 041: Web Push

The unknowns going into 041. Each is answered in `research.md` (with code
refs) or carried forward as an **OPEN design fork** for `design.md`.

## Architecture / seam

1. **Where does a 4th notification channel attach?** → `NotificationChannel`
   protocol + `NotificationDispatcher` fan-out; register at startup beside
   Telegram/SSE/Email. (research §1) **Answered.**
2. **What single dispatch point feeds it?** → `ProcessManager._notify(text,
   kind, data)`. (research §1) **Answered.**
3. **Does the dispatcher isolate a failing channel?** → yes, per-channel
   try/except, logs not re-raises. (research §1) **Answered.**

## Event sources (the two push categories)

4. **What event = "run finished"?** → kind `workflow_completed` from
   `WorkflowWatcher` (`failed`/`interrupted` → `workflow_failed`). NOT
   `entity_message`. (research §2) **Answered.**
5. **What event = "needs your decision"?** → kind `decision_request` from the
   message dispatcher (029/038 channel). (research §2) **Answered.**
6. **What identifiers are available for a deep-link?** → `data.entity` (both);
   `data.run_id` + `data.name`/`status` (workflow); `data.question` (decision).
   (research §2) **Answered.**
7. **Is decision state durable (so a missed push isn't lost)?** → yes:
   `entity.awaiting_decision` + `last_decision_question`, recoverable via
   `GET /api/decisions/pending`. Push is best-effort on top. (research §2)
   **Answered.**

## Subscribe / store / endpoint

8. **What store pattern?** → asyncpg pool + async CRUD, mirror
   `ModeRequestStore`/`TaskStore`. New `PushSubscriptionStore`. (research §3)
   **Answered.**
9. **What migration number?** → **033** (verified; last is 032). ⚠ races with
   in-flight worktrees — re-check at build. (research §3) **Answered (with
   caveat).**
10. **How is `/api/push-subscribe` authed?** → reuse `require_token`
    (Bearer header or `?token=`), same as every write endpoint. (research §1)
    **Answered.**
11. **Subscription identity — per-user or per-device?** → single shared
    `HIVE_WEB_TOKEN`; key by the push `endpoint` (unique per device/browser),
    dedupe + prune on `410 Gone`. (research §3) **Answered.**

## Config / dependency / deploy

12. **Where do VAPID keys live?** → `config.py` env vars, matching
    `WEB_TOKEN`/`TELEGRAM_BOT_TOKEN`. (research §4) **Answered.**
13. **What library?** → none exists; add `pywebpush` (+ `py-vapid` for the
    keygen ops step). (research §4) **Answered.**
14. **Is HTTPS in place (SW + push need a secure context)?** → yes, via
    `tailscale serve` (ADR 0023, shipped with 040). (research §4) **Answered.**
15. **Where does the VAPID ops note go?** → `docs/DEPLOYMENT.md` after the
    `WEB_TOKEN` section (~line 337). (research §4) **Answered.**

## Client / service worker

16. **Where do the SW `push` + `notificationclick` handlers go?** →
    `static/service-worker.js` (append after the fetch handler; CACHE_VERSION
    bump). (research §5) **Answered.**
17. **Where does the client subscribe (permission + `pushManager.subscribe`)?**
    → after SW registration in `_pwa_head.html`; token already in
    `sessionStorage` as `hive_web_token`. (research §5) **Answered.**

## OPEN — design forks (decided in `design.md` with the user)

- **F-A. Push scope / Telegram parity.** Push *only* the two named kinds
  (`workflow_completed`, `decision_request`), or the broader *actionable* alert
  set (also `workflow_failed`, `mode_request`, `vault_action_pending`) so
  Telegram's alert role can genuinely be turned down?
- **F-B. Telegram turn-down.** Does 041 actually demote Telegram, or add the
  capability behind a toggle (default ON) flipped only after on-device parity?
- **F-C. Lane.** Fan-out (≈4 sliced issues + fleet build) vs direct (one PR)?
- **F-D. Deep-link contract.** Exact URL the notification opens
  (`/dashboard?...` query keys the dashboard JS can consume) — needs a
  CONFIRM-IN-CODE check of the 038/039 client.
- **F-E. New ADR?** Record Web Push as a channel + VAPID + Telegram-demotion
  policy as ADR **0025** (proposed)? ⚠ number races with 044.
