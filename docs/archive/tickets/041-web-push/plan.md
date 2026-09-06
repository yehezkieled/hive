# Plan — Ticket 041: Web Push  (issue #231)

**Lane:** direct (one PR closing #231). Matches the repo convention — every prior
ticket, including the comparable cross-cutting 040, shipped as a single issue.
The four slices below are the **build order inside the one PR**, not separate
issues: the feature is inert until every layer lands and is verified as one
integrated on-device smoke.

See `design.md` (decisions + ADR 0026) and `outline.md` (signatures). Build
W1 (I1‖I2) then W2 (I3‖I4).

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `pyproject.toml` | modify | I1 — add `pywebpush>=1.14` to `dependencies` |
| `src/hive/config.py` | modify | I1 — `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` (default `""`); I3 — `TELEGRAM_ALERTS` (default `True`) |
| `docs/DEPLOYMENT.md` | modify | I1 — VAPID keygen + `.env` + restart ops note (after `WEB_TOKEN` §, ~`:337`) ✱ |
| `src/hive/bus/migrations/033_push_subscriptions.sql` | create | I2 — table `push_subscriptions` (⚠ re-confirm `033` free at build) |
| `src/hive/bus/push_subscription_store.py` | create | I2 — `PushSubscriptionStore` (`upsert`/`all`/`delete`), mirror `mode_request_store.py` |
| `src/hive/web/app.py` | modify | I2 — `POST /api/push-subscribe` + `push_store` param on `create_app`; I4 — inject `VAPID_PUBLIC_KEY` into template context |
| `src/hive/__main__.py` | modify | I2 — build `PushSubscriptionStore`; I3 — build + `register(WebPushChannel)` after SSEBroker (`:379`) |
| `src/hive/notifications/web_push.py` | create | I3 — `WebPushChannel` (filter actionable kinds, render copy, sign via `pywebpush`, prune `410`) |
| `src/hive/telegram/bridge.py` | modify | I3 — guard actionable alert-kinds behind `TELEGRAM_ALERTS` in `send()` |
| `src/hive/web/static/service-worker.js` | modify | I4 — `push` + `notificationclick` handlers; bump `CACHE_VERSION` |
| `src/hive/web/templates/_pwa_head.html` | modify | I4 — client subscribe (permission + `pushManager.subscribe` + POST) + `?focus=` deep-link handler |
| `tests/web/test_push_subscribe.py` | create | I2 — endpoint stores subscription; 401 without token; upsert idempotent |
| `tests/notifications/test_web_push_channel.py` | create | I3 — kind filter, payload/url per kind, `410`→prune, no-keys no-op (`pywebpush` mocked) |
| `tests/telegram/test_alerts_toggle.py` | create | I3 — alerts-off suppresses actionable kinds, relays the rest |
| `docs/adr/0026-web-push-notification-channel.md` | (created this run) | — design decision |
| `CONTEXT.md` | (edited this run) | — glossary: Notification channel / Web Push channel / Push subscription / Alert role |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (new tests above; `pywebpush` mocked).
- Channel unit-proves: non-actionable kind → no send; each actionable kind →
  correct `{title, body, url}`; mocked `410` → `store.delete`; empty VAPID → no-op.
- Telegram toggle unit-proves: `TELEGRAM_ALERTS=false` suppresses the actionable
  alert-kinds, still relays the rest.
- **On-device (acceptance gate, post-merge):** installed PWA on a real iPad,
  backgrounded → receives a "Run ended" and a "Needs you" push; tap deep-links
  to the entity; then set `HIVE_VAPID_*` + flip `HIVE_TELEGRAM_ALERTS=false` and
  confirm parity (Telegram quiet on alerts, still logging). Per CLAUDE.md, smoke
  from the Tailscale HTTPS origin in an actual browser, not curl/loopback.

## Out of scope

Pre-16.4 iOS fallback · deleting Telegram · rich notification action buttons ·
per-user subscriptions · unsubscribe UI · rich focus/scroll polish beyond
highlight.

## Cross-cutting impact

- **`docs/DEPLOYMENT.md`** — new VAPID-keys ops note (I1).
- **`docs/adr/0026-web-push-notification-channel.md`** — recorded this run.
- **`CONTEXT.md`** — glossary cluster recorded this run.
- ⚠ **Number race:** ADR already hit it — **044 merged `0025` mid-run, so this
  ADR is `0026`**. Migration `033` is still next-free against `origin/main`
  (max = 032) but isn't a file yet — **re-verify `033` at build** before
  creating it; other in-flight worktrees could take it. Known Hive gotcha.

## To build

One PR on a `ticket-041/...` branch, closing #231. Build W1 (I1‖I2) → W2
(I3‖I4) → units green → ship → on-device smoke → flip the toggle.
