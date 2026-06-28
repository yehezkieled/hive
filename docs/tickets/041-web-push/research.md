# Research — Ticket 041: Web Push

What we found in the code. Sourced from a 5-agent parallel read of the web
stack, the notification architecture, the two event sources, and
config/deploy/tests, then **the contested facts verified directly** (migration
number, ADR number, the "run finished" kind). File refs are `path:line` at
`origin/main` `c684f2f`.

> **Headline:** Hive already has a clean outbound-notification architecture — a
> `NotificationChannel` protocol with a fan-out dispatcher. **Web Push is a
> fourth channel beside Telegram / SSE / Email, not a new pipeline.** Almost all
> the plumbing 041 needs (the dispatch point, the event payloads, the store
> pattern, HTTPS, the PWA + service worker) already exists; 041 fills in the
> push-specific edges.

```
  _notify(text, kind, data)                 ProcessManager.manager.py:741
        │
        ▼
  NotificationDispatcher.dispatch()          dispatcher.py:39-71  (per-channel try/except)
        ├──▶ TelegramBridge.send()           bridge.py:136-150    (text only — ignores kind/data)
        ├──▶ SSEBroker.send()                sse.py:27-63         (formats kind+data → browser)
        ├──▶ EmailDigest.send()              email.py:59-84       (buffers)
        └──▶ WebPushChannel.send()    ← 041  (NEW: filters by kind, signs+POSTs to push endpoints)
```

## §1 — Web server & the notification seam

- **FastAPI app** with Jinja2 templates: `src/hive/web/app.py:59`
  (`create_app(process_manager, sse_broker, …)`), instantiated in
  `src/hive/__main__.py:358-409` when `WEB_PORT > 0`.
- **Auth** = Bearer `HIVE_WEB_TOKEN` via `require_token`
  (`src/hive/web/auth.py:21-44`) — accepts `Authorization: Bearer <t>` **or**
  `?token=<t>` (for `EventSource`). Config at `config.py:108`. Every write
  endpoint uses `Depends(require_token)`.
- **Route convention**: `@app.post("/api/<resource>/<id>/<action>")` +
  `Depends(require_token)`. New `POST /api/push-subscribe` slots in beside the
  existing writes (`app.py:161` `/api/command`, `:316`
  `/api/decision/{entity}/reply`, `:404` `/api/vault-action/...`).
- **The channel abstraction**:
  - `NotificationChannel` Protocol (async `send(Notification)`) —
    `notifications/dispatcher.py:29-36`.
  - `Notification` dataclass: `text`, `kind` (default `info`), `data` (optional
    dict), `timestamp` — `dispatcher.py:13-27`.
  - `NotificationDispatcher.register()` / `dispatch()` with **per-channel error
    isolation** (one channel failing doesn't block others) —
    `dispatcher.py:39-71`.
  - **Single dispatch point**: `ProcessManager._notify(text, kind, data)` —
    `process/manager.py:741-752`.
  - **Reference impls**: `SSEBroker.send()` (`web/sse.py:27-63`, *uses* kind +
    data via `format_event` `:65-75`) and `TelegramBridge.send()`
    (`telegram/bridge.py:136-150`, *ignores* kind/data — the "alert role" 041
    turns down).
  - **Registration at startup**: `__main__.py` — EmailDigest `:170-187`,
    TelegramBridge `:346`, SSEBroker `:379`. Web Push registers here too.

## §2 — The two event sources + deep-link identifiers

| Category | Kind | Emit point | `data` available |
|---|---|---|---|
| **run finished** | `workflow_completed` | `workflow_watcher.py:212-223` (`_sweep`); kind map `:34-38` | `entity`, `run_id`, `name`, `status`, `done_count`, `agent_count` |
| run *ended badly* | `workflow_failed` | same (`failed`/`interrupted` collapse here `:33`) | same |
| **needs your decision** | `decision_request` | `message_dispatcher.py:472-476` | `entity`, `question` |

- **Verified**: "run finished" is **`workflow_completed`**, not `entity_message`
  (one research agent was wrong). `failed` and `interrupted` both surface as
  `workflow_failed` (`workflow_watcher.py:32-38`).
- **Deep-link keys**: entity name is the durable Hive identity (e.g. `otter`,
  `otter.backend`); `run_id` is the CC workflow id (`runtime/workflow_progress.py:32-43`).
- **Decision state is durable** (a dropped push is recoverable, not lost):
  `entity.awaiting_decision` + `entity.last_decision_question`
  (`models/entity.py:228-235`, migrations `029_*`, `032_*`); read back via
  `GET /api/decisions/pending` (`app.py:342-354`). The decision channel is
  **entity-keyed** (ADR 0024), so a push for a decision deep-links by `entity`,
  not a row id.

## §3 — Subscription store + endpoint

- **Store pattern** (mirror): `ModeRequestStore` (`bus/mode_request_store.py:15-142`)
  / `TaskStore` (`bus/task_store.py:15-80`) — `__init__(pool)`, async
  `create/get/list/…` using `pool.fetchrow/fetch/execute`, return `dict(row)`.
  New `bus/push_subscription_store.py` → `PushSubscriptionStore`.
- **Migration**: runner applies `NNN_*.sql` in order (`bus/migrations/runner.py:19-64`).
  **Verified next free number = `033`** (last on disk is
  `032_entity_decision_question.sql`). ⚠ **Number race**: worktrees for 043/044/045
  are in flight; 044 also adds an ADR. Re-confirm `033` (and bump if taken) at
  build time — known Hive gotcha.
  - Table `push_subscriptions`: `endpoint` (unique), `p256dh`, `auth`,
    `user_agent`, `created_at` (+ optional `expires_at`).
- **Identity**: `HIVE_WEB_TOKEN` is a single shared secret (one operator), so
  there is **no per-user** key — key the store by the push `endpoint` (unique
  per device+browser). On `410 Gone` from the push service, delete that row
  (lazy prune in the channel's `send`).
- **Endpoint**: `POST /api/push-subscribe` (`Depends(require_token)`), body =
  the browser `PushSubscription` JSON; upsert by `endpoint`. Attaches after the
  manifest/SW routes (`app.py:523-539`).

## §4 — Config, dependency, deployment

- **VAPID config**: add `HIVE_VAPID_PUBLIC_KEY` / `HIVE_VAPID_PRIVATE_KEY`
  (+ `HIVE_VAPID_SUBJECT`, a `mailto:`) to `config.py` next to `WEB_TOKEN`
  (`config.py:100-109`); `.env` loaded at `config.py:52`. Default empty →
  **push is opt-in/inert until keys are set** (channel no-ops without keys).
- **Dependency**: none present (`grep` for vapid/webpush = empty). Add
  **`pywebpush`** (sends the signed POST; pulls in `py-vapid`/`http-ece`) to
  `pyproject.toml` deps (`:10-25`). `py-vapid` also gives the CLI for the
  one-time keygen in the ops note.
- **HTTPS**: already satisfied — `tailscale serve https` (ADR 0023, shipped with
  040). Service workers + push require a secure context; the tailnet HTTPS
  hostname provides it. No app-level TLS.
- **Ops note**: `docs/DEPLOYMENT.md` after the `WEB_TOKEN` section (~`:337`) —
  `vapid --gen` (or `py-vapid`) → paste keys into `.env` → `systemctl --user
  restart hive.service`. ✱ cross-cutting (declared in plan).

## §5 — Client / service worker (Ticket 040 foundation)

- **Service worker**: `static/service-worker.js` (root-scoped `/service-worker.js`,
  served `app.py:523-538`), `CACHE_VERSION = 'hive-v2'` (`:11`). Has
  install/activate/fetch; **no `push` or `notificationclick` listener** — append
  after the fetch handler (~`:105`). `push` → `event.data.json()` →
  `self.registration.showNotification(title, {body, icon, badge, tag, data:{url}})`;
  `notificationclick` → focus an open client or `clients.openWindow(url)`.
- **Manifest** `static/manifest.webmanifest` — `start_url`/`scope` = `/`
  (`:5-6`), so the SW controls `/` and `/dashboard`. No push-specific manifest
  fields needed.
- **Subscribe flow (client)**: after `navigator.serviceWorker.register()` in
  `templates/_pwa_head.html:13-19`, add
  `Notification.requestPermission()` → `reg.pushManager.subscribe({
  userVisibleOnly:true, applicationServerKey:<VAPID pub> })` → `POST
  /api/push-subscribe` with the token already in `sessionStorage`
  (`hive_web_token`, see `landing.html:282-317`). Feature-detect
  (`'PushManager' in window`) and fall back silently to SSE-while-open.

## §6 — Tests

- Pattern: `TestClient(create_app(...))` with a mocked `ProcessManager` —
  `tests/test_web_sse.py:102-131`; web tests live under `tests/web/`.
- New `tests/web/test_push_subscribe.py` (endpoint stores a subscription) +
  `tests/.../test_push_channel.py` (channel filters by kind, signs, prunes on
  `410`). `pywebpush` send is mocked. All under `pytest -m "not integration"`.
- On-device verification (installed PWA, backgrounded iPad, real push) is the
  acceptance gate — not coverable by units.

## Open design forks → `design.md`

F-A push scope / Telegram parity · F-B Telegram turn-down mechanism · F-C lane
(fan-out vs direct) · F-D deep-link URL contract (CONFIRM IN CODE against the
038/039 dashboard JS) · F-E new ADR **0026** (renumbered from 0025 — 044 merged
0025 mid-run).

## Verified-fact log (don't re-trust the agents on these)

- Next migration = **033** · Next ADR = **0026** · "run finished" =
  **`workflow_completed`** · no existing webpush dep · decision surface =
  `/api/decision/{entity}/reply` + `/api/decisions/pending`, entity-keyed.
