# Outline — Ticket 041: Web Push

Implementation structure, organised by the four slices from `design.md`. File
refs are the seams from `research.md`. This is the module sketch the per-slice
issues expand.

```
  Wave 1 (foundations, parallel)        Wave 2 (parallel, blocked-by W1)
  ┌─ I1 config + dep + ops              I3 server: channel + wiring + Telegram toggle
  └─ I2 store + migration + endpoint    I4 frontend: SW + client subscribe + deep-link
```

## I1 — VAPID config + dependency + ops note  `[config/docs]`

- **`pyproject.toml`** (`:10-25`): add `pywebpush>=1.14` to `dependencies`
  (pulls in `py-vapid`, `http-ece`).
- **`src/hive/config.py`** (after `WEB_TOKEN`, `:108`): add
  `VAPID_PUBLIC_KEY = os.environ.get("HIVE_VAPID_PUBLIC_KEY", "")`,
  `VAPID_PRIVATE_KEY = os.environ.get("HIVE_VAPID_PRIVATE_KEY", "")`,
  `VAPID_SUBJECT = os.environ.get("HIVE_VAPID_SUBJECT", "")`. Empty ⇒ push inert.
- **`docs/DEPLOYMENT.md`** (after the `WEB_TOKEN` section, ~`:337`): ops note —
  generate keys (`vapid --gen` / `py-vapid`), paste into `.env`,
  `systemctl --user restart hive.service`. ✱ cross-cutting.
- **Tests:** none beyond config import; a `config` smoke that the three vars
  default to `""`.

## I2 — Subscription store + migration + subscribe endpoint  `[persistence]`

- **`src/hive/bus/migrations/033_push_subscriptions.sql`** (NEW — ⚠ re-confirm
  `033` free at build): `push_subscriptions(endpoint TEXT PRIMARY KEY, p256dh
  TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at TIMESTAMPTZ
  DEFAULT now())`.
- **`src/hive/bus/push_subscription_store.py`** (NEW — mirror
  `mode_request_store.py:15-142`):
  - `class PushSubscriptionStore: __init__(self, pool)`
  - `async upsert(self, sub: dict) -> None` (`INSERT … ON CONFLICT(endpoint) DO
    UPDATE`)
  - `async all(self) -> list[dict]`
  - `async delete(self, endpoint: str) -> None`
- **`src/hive/web/app.py`**: new `POST /api/push-subscribe`,
  `Depends(require_token)`, body `PushSubscriptionIn` (pydantic: `endpoint`,
  `keys: {p256dh, auth}`); calls `store.upsert`. Add `push_store` param to
  `create_app(...)` (`:59-73`).
- **`src/hive/__main__.py`**: build `PushSubscriptionStore(pool)`, pass into
  `create_app`.
- **Tests** (`tests/web/test_push_subscribe.py`): POST stores a subscription;
  missing-token → 401; upsert is idempotent on `endpoint`.

## I3 — WebPushChannel + wiring + Telegram toggle  `[server]`  (blocked-by I1, I2)

- **`src/hive/notifications/web_push.py`** (NEW):
  - `_ACTIONABLE = {"decision_request","mode_request","vault_action_pending",
    "workflow_completed","workflow_failed"}`
  - `class WebPushChannel: __init__(self, store, public_key, private_key,
    subject)`
  - `async send(self, n: Notification)`: return early if no VAPID keys or
    `n.kind not in _ACTIONABLE`; `_render(n) -> {title, body, url}`
    (the kind→copy table in `design.md`); for each `store.all()` → `pywebpush(...)`
    in a thread (`asyncio.to_thread`, pywebpush is sync); on `WebPushException`
    with 404/410 → `store.delete(endpoint)`.
  - `_render(n)`: switch on `n.kind`; pull `entity`/`question`/`run_id`/`name`/
    `status` from `n.data`; build `url=/?focus=<entity>[&run=<run_id>]`.
- **`src/hive/__main__.py`** (after SSEBroker register, `:379`): construct
  `WebPushChannel(push_store, config.VAPID_*)`,
  `notification_dispatcher.register(web_push)`.
- **`src/hive/config.py`**: `TELEGRAM_ALERTS = _envbool("HIVE_TELEGRAM_ALERTS",
  True)`.
- **`src/hive/telegram/bridge.py`** (`send()`, `:136-150`): when
  `not config.TELEGRAM_ALERTS` and `n.kind in _ACTIONABLE`, skip (still relay
  non-alert kinds so Telegram stays a log).
- **Tests** (`tests/.../test_web_push_channel.py`): non-actionable kind → no
  send; actionable → correct payload/url per kind; mocked `410` → `delete`
  called; no VAPID keys → no-op. `tests/.../test_telegram_toggle.py`: alerts-off
  suppresses actionable kinds, relays the rest. `pywebpush` mocked throughout.

## I4 — Service worker + client subscribe + deep-link  `[frontend]`  (blocked-by I1, I2)

- **`src/hive/web/static/service-worker.js`** (append after fetch handler,
  ~`:105`; bump `CACHE_VERSION`):
  - `self.addEventListener('push', e => { const d = e.data.json();
    e.waitUntil(self.registration.showNotification(d.title, {body:d.body,
    icon, badge, tag:d.tag, data:{url:d.url}})); })`
  - `self.addEventListener('notificationclick', e => { e.notification.close();
    e.waitUntil(focusOrOpen(e.notification.data.url)); })` — `focusOrOpen`:
    `clients.matchAll({type:'window'})` → focus + `postMessage({type:'hive-focus',
    url})` if one is open, else `clients.openWindow(url)`.
- **`src/hive/web/templates/_pwa_head.html`** (after SW register, `:13-19`):
  - `subscribeForPush(reg)`: guard `'PushManager' in window`;
    `Notification.requestPermission()`; `reg.pushManager.subscribe({
    userVisibleOnly:true, applicationServerKey: urlBase64ToUint8Array(<VAPID
    pub>)})`; `POST /api/push-subscribe` with the `sessionStorage` token. VAPID
    public key injected into the template from `config`.
  - Deep-link handler: on load read `?focus`/`&run`; on `message` event handle
    `hive-focus`. `focusEntity(entity, run)` — minimal: turn on the 039
    awaiting filter / scroll the entity card into view + brief highlight (reuse
    `.is-awaiting`/card targeting from `landing.html`). Rich focus is out of
    scope.
- **`src/hive/web/app.py`**: expose `VAPID_PUBLIC_KEY` to the template context
  (landing/dashboard render).
- **Tests:** JS isn't unit-tested here; covered by the on-device smoke. Optional:
  a template-render test that the VAPID public key is injected when configured.

## Cross-cutting / reference-doc impact

- `docs/DEPLOYMENT.md` (I1), `docs/adr/0025-*.md` (this run), `CONTEXT.md`
  glossary (this run).

## On-device acceptance (post-merge, not a unit)

Installed PWA on a real iPad, backgrounded → receives a "Run ended" push and a
"Needs you" push; tap deep-links to the entity; flip `HIVE_TELEGRAM_ALERTS=false`
→ confirm parity, Telegram quiet on alerts but still logging.
