# Design — Ticket 041: Web Push

Chosen approach for delivering native Web Push to the installed iPad PWA, plus
the policy half that lets Telegram's alert role be turned down. Builds on
`research.md` (the `NotificationChannel` seam) and the four design forks decided
with the user.

## Decisions (the forks)

| Fork | Decision | Why |
|---|---|---|
| **A — push scope** | Push the **actionable set**, grouped into two styles: **"Needs you"** = `decision_request`, `mode_request`, `vault_action_pending`; **"Run ended"** = `workflow_completed`, `workflow_failed`. FYI noise (`workflow_started`, `quota_*`, `auto_bounce` success, `info`) is **not** pushed. | Pushing only the two literal kinds would make turning Telegram down lose approvals + failed-run alerts. The actionable set is what makes the demotion *safe*, while staying off the lock screen for noise. |
| **B — Telegram turn-down** | Build a `HIVE_TELEGRAM_ALERTS` toggle (**default ON**) that silences alert-kind sends in `TelegramBridge`. 041 ships the switch in the ON position. | Satisfies acceptance #5 ("alert role *can* be turned down") without risking going dark. Operator flips it OFF after the on-device parity smoke. |
| **C — lane** | **Fan-out, 4 slices** (2×2 waves). | Genuine parallelism (server ∥ frontend), each slice independently reviewable, matches the sprint's "large anchor" framing. |
| **D — deep-link** | Notification `data` carries `{url, entity, kind, run_id?}`. Tap → focus an open client (or `openWindow('/?focus=<entity>[&run=<run_id>]')`); a **new** minimal client handler scrolls+highlights that entity (reusing 039's `.is-awaiting`/card targeting). | Dashboard has **no** deep-link handler today — only the 039 awaiting *filter*. So focus-on-tap is net-new and lives in the frontend slice. Keep it minimal (scroll+highlight); rich focus polish is out of scope. |
| **E — ADR** | Record as **ADR 0025** (Web Push channel + VAPID via env + Telegram-demotion policy). ⚠ number races with 044 — re-check at ship. | Matches how 0023/0024 captured PWA/decision decisions. |

## Architecture — a 4th channel, nothing more

041 adds **one new `NotificationChannel`** and the edges around it. The dispatch
path, the `Notification` payloads, and the event sources are untouched.

```
  _notify(text, kind, data)            manager.py:741  (UNCHANGED)
        │
        ▼  NotificationDispatcher.dispatch()   dispatcher.py:39-71  (UNCHANGED)
        ├──▶ TelegramBridge.send()    ← B: guard alert-kinds behind HIVE_TELEGRAM_ALERTS
        ├──▶ SSEBroker.send()                                  (UNCHANGED)
        ├──▶ EmailDigest.send()                                (UNCHANGED)
        └──▶ WebPushChannel.send(notification)   ◀── NEW
                 │  1. is notification.kind in the actionable set?  (else drop)
                 │  2. build {title, body, url} from kind + data
                 │  3. for each row in PushSubscriptionStore.all():
                 │        pywebpush(sub, payload, VAPID)  →  410? prune row
```

### Components (what's new)

1. **`WebPushChannel`** — `src/hive/notifications/web_push.py` (new). Implements
   the `NotificationChannel` protocol (`async send`). Holds the
   `PushSubscriptionStore` + VAPID config. Filters by `kind`, maps to
   `{title, body, url}`, signs and POSTs via `pywebpush`, prunes `410 Gone`.
   No-ops (logs once) when VAPID keys are unset → 041 is **inert until
   configured**.
2. **`PushSubscriptionStore`** — `src/hive/bus/push_subscription_store.py` (new),
   mirrors `ModeRequestStore`. Methods: `upsert(sub)`, `all()`, `delete(endpoint)`.
3. **Migration `033_push_subscriptions.sql`** — table `push_subscriptions`
   (`endpoint` PK/unique, `p256dh`, `auth`, `user_agent`, `created_at`).
   ⚠ re-confirm `033` is free at build time.
4. **`POST /api/push-subscribe`** — `app.py`, `Depends(require_token)`, body =
   browser `PushSubscription` JSON → `store.upsert`. (A `DELETE`/unsubscribe is
   a nice-to-have, not required.)
5. **VAPID config** — `config.py`: `HIVE_VAPID_PUBLIC_KEY`,
   `HIVE_VAPID_PRIVATE_KEY`, `HIVE_VAPID_SUBJECT` (default `""`).
6. **Wiring** — `__main__.py`: build `PushSubscriptionStore` + `WebPushChannel`,
   `notification_dispatcher.register(web_push_channel)` after the SSEBroker
   registration (`:379`); pass the store into `create_app` for the endpoint.
7. **Service worker** — `static/service-worker.js`: add `push` (→
   `showNotification`) and `notificationclick` (→ focus/openWindow) listeners;
   bump `CACHE_VERSION`.
8. **Client subscribe** — `templates/_pwa_head.html`: after SW registration,
   feature-detect, `Notification.requestPermission()`,
   `reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey})`,
   `POST /api/push-subscribe` with the `sessionStorage` token. Plus the
   `?focus=` / `postMessage` deep-link handler.
9. **Telegram toggle (B)** — `telegram/bridge.py` `send()`: when
   `HIVE_TELEGRAM_ALERTS` is off, skip the actionable alert-kinds (still relays
   plain `entity_message`/debug so Telegram stays a log). `config.py` flag
   default `True`.
10. **`pywebpush`** dependency in `pyproject.toml`.

## Notification copy (kind → title/body/url)

| kind | style | title | body | url |
|---|---|---|---|---|
| `decision_request` | Needs you | `<entity> needs your decision` | the question | `/?focus=<entity>` |
| `mode_request` | Needs you | `<entity> — approval needed` | mode summary | `/?focus=<entity>` |
| `vault_action_pending` | Needs you | `<entity> — vault approval` | action summary | `/?focus=<entity>` |
| `workflow_completed` | Run ended | `✅ <entity> — run finished` | `<name>` | `/?focus=<entity>&run=<run_id>` |
| `workflow_failed` | Run ended | `❌ <entity> — run ended` | `<name> (<status>)` | `/?focus=<entity>&run=<run_id>` |

## Alternatives considered (rejected)

- **In-memory subscription store** → lost on `hive.service` restart; the whole
  point is async pings when you're away. Durable (asyncpg) wins.
- **Per-user subscription identity** → no per-user model; `HIVE_WEB_TOKEN` is a
  single shared secret. Key by `endpoint` (unique per device).
- **Push every notification kind** → lock-screen noise; defeats "demote
  Telegram" by re-creating the spam on a new surface. Actionable set only.
- **Demote Telegram by default** → risk going dark before the push path is
  proven on-device. Toggle defaults ON.
- **A new `push_*` kind prefix at the emit sites** → would touch every
  `_notify` call. Instead, filter by the *existing* kinds inside the channel —
  zero change to emitters.
- **VAPID public key inlined in the manifest** → it's fine to expose the public
  key, but keeping both keys in `config.py`/`.env` matches every other secret
  and keeps one source of truth.
- **App-level TLS / caddy for HTTPS** → already solved by `tailscale serve`
  (ADR 0023). No new infra.

## Slice structure (feeds `outline.md` + `plan.md`)

```
  Wave 1 (parallel — foundations)
    I1  VAPID config + pywebpush dep + DEPLOYMENT.md ops note      [config/docs]
    I2  PushSubscriptionStore + migration 033 + POST /api/push-subscribe  [persistence]
  Wave 2 (parallel — build on W1)
    I3  WebPushChannel (filter+sign+deliver+prune) + __main__ wiring
        + HIVE_TELEGRAM_ALERTS toggle in TelegramBridge            [server]   blocked-by I1,I2
    I4  SW push/notificationclick + client subscribe + ?focus deep-link  [frontend]  blocked-by I1,I2
```

Logical blockers only: I3/I4 need VAPID (I1) + the subscribe endpoint/store
(I2). I3 ∥ I4 (server vs frontend, different files). I1 ∥ I2 (config vs
store/endpoint). Shared `app.py`/`__main__.py` touches are file-overlap, not
logical blockers — the fleet merges one at a time.

## Cross-cutting impact (declared up front)

- **`docs/DEPLOYMENT.md`** — VAPID keygen + `.env` + restart ops note (in I1).
- **`docs/adr/0025-web-push-notification-channel.md`** — new (this run).
- **`CONTEXT.md`** — glossary: Web Push channel, Push subscription, alert-vs-log
  Telegram role (this run).

## Verification

- Units (`pytest -m "not integration"`): endpoint stores a subscription;
  channel filters non-actionable kinds, builds correct payload/url, prunes on a
  mocked `410`; Telegram toggle suppresses alert-kinds when off. `pywebpush`
  mocked.
- `ruff check src/ tests/ && ruff format --check src/ tests/`.
- **On-device (acceptance gate):** installed PWA on a real iPad, backgrounded —
  receives both a "Run ended" and a "Needs you" push; tap deep-links to the
  entity; then flip `HIVE_TELEGRAM_ALERTS=false` and confirm parity.

## Out of scope

Pre-16.4 fallback · deleting Telegram · rich notification action buttons ·
per-entity rich focus/scroll polish beyond highlight · unsubscribe UI.
