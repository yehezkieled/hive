# Research — Ticket 038: Web decision-UI parity (029 → web)

Code-grounded map of the three subsystems 038 touches, plus the **proven
`mode_request` mirror pattern** the design copies. Every claim cites
`file:line`. Sourced from a parallel deep-read of the backend 029 lifecycle, the
web SSE/endpoint layer, and the frontend render (run 2026-06-21).

> **One-line takeaway.** Hive already owns the hard half. A plain-text web reply
> *already* unparks the maestro today. What's missing is purely the **delivery
> layer**: the question text never reaches the browser (it's not in the SSE
> `data` payload), there's no dedicated reply endpoint, no interactive bubble,
> and no fresh-load recovery. 038 mirrors `mode_request` to close all four.

---

## §A — Backend: the 029 decision lifecycle (server-side)

**Emit / park.** `request_decision` is parsed with required fields `{to, text}`
only — no id, no options; the whole question is the free-text `text`
(`bus/actions.py:80`, `:281-290`). In `MessageDispatcher._handle_actions` the
`to=="user"` branch (`process/message_dispatcher.py:423-484`):

1. checks `can_request_decision` (only a maestro may target `user`;
   `bus/permissions.py:89-109`),
2. emits `_notify("[decision needed] {text}", kind="decision_request",
   data={"entity": entity_name})` — **the question is in `text`, and `data`
   carries ONLY `{entity}`** (`:467-471`),
3. sets `entity.awaiting_decision = True`, arms `last_nudged_at`, persists,
   audits `request_decision_sent`, then **`break`s** — ending the turn and
   dropping any trailing actions (no ask-then-act in one block).
4. If `notification_dispatcher is None` it **rejects without parking**
   (`:450-465`) — a decision only exists if a delivery channel was live.

**Park state.** `Entity.awaiting_decision: bool` is **durable** (persisted by
`EntityStore`, migration `029_entity_awaiting_decision.sql`; `models/entity.py:229`,
`bus/entity_store.py:39/155`). `last_nudged_at` is **in-memory only** (re-armed
without firing on restart; `scheduler.py:223-224`). The **question text is not
persisted anywhere queryable** — it lives only in the transient SSE `text` and
the maestro's harness transcript (`message_dispatcher.py:467-471`). This is the
core gap behind a fresh-load `pending` endpoint.

**Unpark + resume (the path 038 parallels).** A Telegram plain-text reply becomes
`Command("message", default_maestro, text)` (`telegram/commands.py:37/46`) →
`CommandDispatcher._send_to_entity` (`commands/dispatch.py:636-659`), which runs
**in this exact order** (`:645-648`):

```
clear_awaiting_decision(entity)   # unpark BEFORE the turn, so a re-ask re-arms
send_to_entity(entity, reply)     # THIS is the resume — injects reply as prompt
router.route("user", entity, reply)
router.route(entity, "user", response)
```

`clear_awaiting_decision` (`process/manager.py:214-241`): no-op unless parked;
clears the flag, disarms the nudge, and **for a maestro flips
`confirmed_with_user=True`** — lifting the Ticket 019 phase-confirmation floor and
auditing `phase_confirmation_cleared`. Its docstring warns it must be called
**only from the user path**, never scheduler/peer, so a peer message can't
false-clear it. `send_to_entity` resumes the harness via its stored `session_id`
(`--resume`; `message_dispatcher.py:218-268`). `awaiting_decision` is **not** a
gate, so the `is_parked_at_gate` injection guard (`:112-120`) does not block the
reply — but it *does* return `<parked at gate>` if the entity is genuinely gated,
which the endpoint should surface rather than swallow.

**Already-works fact.** The web `/api/command` endpoint routes through this same
dispatcher (`web/app.py:157-176`), so a plain-text web reply to the **default**
maestro *already* unparks it today. The gaps are: it only targets the default
maestro (a non-default parked maestro would be missed), there's no structured
payload, no bubble, and no recovery.

**While parked.** The scheduler skips poking a parked maestro and instead nudges
the **user** every `decision_nudge` interval (`scheduler.py:236-272`,
`:212-234`); auto-bounce is held off (`manager.py:421-423`).

## §B — Web backend: SSE delivery + the mirror endpoints

**SSE transport.** `ProcessManager._notify` (`manager.py:738-749`) fans a
`Notification(text, kind, data)` to all channels (Telegram + SSE). `SSEBroker`
(`web/sse.py:27-62`) is a per-subscriber bounded queue (size 100, **drops oldest
on overflow** — best-effort, so events can be lost → recovery endpoint is
mandatory, not optional). `format_event` (`web/sse.py:65-75`) serialises to
`data: {text, kind, data, timestamp}\n\n` — **anything 038 adds must go inside
`data`**; there is no other channel to the browser. The stream is
`GET /sse/notifications?token=…` (EventSource can't set headers, so token in
query; `web/app.py:401-418`).

**The `mode_request` mirror (copy this end-to-end):**

| Layer | mode_request | 038's analogue |
|-------|-------------|----------------|
| Durable store | `ModeRequestStore.create/get/list_pending/approve/deny`, Postgres, `UPDATE … WHERE id=$1 AND status='pending' RETURNING *` (idempotent → `None`→404) (`bus/mode_request_store.py:15-143`) | a decision store / equivalent |
| Emit + notify | `request_mode_change` creates row + `_notify(kind="mode_request", data={id, requester, requested_mode, reason})` (`approval_handler.py:83-114`) | enrich `decision_request` data with `{entity, question, id}` |
| Reply endpoint | `POST /api/mode-request/{id}/approve|deny` → `pm.approve_mode_request(id)` → 404 if None (`app.py:311-329`) | `POST /api/decision/{entity}/reply` |
| Pending-on-load | `GET /api/mode-requests/pending` → `list_pending(default_maestro)` (`app.py:304-309`) | `GET /api/decisions/pending` |

**Closest free-text parallel:** vault's deny endpoint reads an optional JSON body
`{reason}` off the request (`app.py:375-399`) — the template for a reply endpoint
that carries the user's free-text answer. (Note: vault has **no** JSON pending
endpoint, only an htmx fragment — do **not** copy vault for recovery;
`mode_request` is the right model.)

**Auth.** `require_token` (`web/auth.py:21-44`) — fails closed when `WEB_TOKEN`
unset; accepts `Authorization: Bearer` (fetch) or `?token=` (EventSource) via
`hmac.compare_digest`. 038's endpoints add `Depends(require_token)` identically.

**view_model.** `build_landing_view_model` surfaces vault/mode pending but **not**
`awaiting_decision` and computes no pending-decision list (`web/view_model.py:125-156`,
`:243-264`) — new computation is required regardless of storage choice (also
relevant to 039).

## §C — Frontend: SSE render (browser)

**One render path.** All interactive approval rendering lives **only** in
`web/templates/landing.html`. The React `/dashboard` is read-only data-viz polling
`/api/dashboard/all` every 30s — **no EventSource, no SSE handling**
(`static/dashboard/refresh.js:1-38`). 038's entire frontend change is in
`landing.html`.

**Where it falls through (the bug).** `startNotificationStream` (`landing.html:716-741`,
verified) is an if/else-if chain on `evt.kind`: `mode_request` → bubble,
`vault_action_pending` → bubble, `vault_action_resolved` → system line, **else →
dead `[kind] text` line**. `decision_request` has no branch, so today the user
sees a grey `[decision_request] [decision needed] <question>` pill with no reply
field (`:731-732`). Adding `else if (evt.kind === 'decision_request')` is safe
(chain, not separate `if`s).

**The bubble template to copy.** `appendModeRequestBubble` (`landing.html:419-470`):
builds `div.msg.msg--agent.msg--mode-req`, renders escaped fields + an actions
row, attaches a per-button click handler that reads the token from
`sessionStorage('hive_web_token')`, disables controls, `fetch`es the POST with
`Authorization: Bearer`, swaps actions for a `.mode-req__resolved` label on
success, flashes `.mode-req--error` on failure. `appendVaultRequestBubble`
(`:359-417`) is the same shape and additionally **renders structured JSON from the
response** — the closest example for a reply that echoes a result. **Difference
for 038:** a decision needs a free-text `<input>/<textarea>` + Send (not
allow/deny buttons), so the POST carries a JSON body (`Content-Type: application/json`)
like vault-deny, not the body-less mode POST.

**Fresh-load recovery template.** `initBellPanel`/`initGatePanel` call
`loadPending()` → `fetch('/api/mode-requests/pending')` → `renderRows` draws
`.bell-row` blocks with the same id-keyed POST (`landing.html:1004-1100`,
`:1106-1221`). This is the surface 038's `/api/decisions/pending` mirrors.

**CSS.** `.mode-req__*` classes are reusable (`static/landing.css:518-583`); the
only genuinely new markup is the reply field (no `.mode-req__input` class exists).

### The iPad-portrait constraint (matters for the sprint goal)

`@media (max-width: 900px) { .chat-rail { display:none } }` (`landing.css:1423-1424`,
verified). The chat rail — where bubbles live — is **hidden on iPad portrait
(~768px)**. So an inline decision bubble is **invisible on the very device S8
targets** until Ticket 037 fixes the rail-hide. The bell-style **fixed-position
pending panel is the only surface visible at narrow widths**. → strong argument
that 038 must surface decisions in a recovery/bell panel, not rely on the inline
bubble alone, OR explicitly depend on 037.

---

## Gotchas the implementer must respect

1. **Enrich the producer, not just `app.py`.** The question text is only in
   `notification.text`; capturing it for `data`/a store means editing the
   `request_decision` handler at `message_dispatcher.py:466-471`.
2. **`clear_awaiting_decision` is user-path-only** and flips `confirmed_with_user`
   (phase gate, 019). Route the reply as a user-sourced event and **reuse the
   method** — don't re-implement the clear inline and skip that branch.
3. **Order is load-bearing:** clear → send (`dispatch.py:645-646`). Reversed, a
   re-ask within the same turn gets immediately cleared.
4. **Idempotent resolve.** Mirror the `AND status='pending' … RETURNING` guard so
   a double reply (bubble + panel, or two tabs) can't double-resume the maestro.
5. **Target the named entity.** Plain web text routes to `default_maestro` only;
   a non-default parked maestro needs entity-keyed addressing — hence
   `/api/decision/{entity}/reply` (the ticket's stated path).
6. **`notification_dispatcher is None` ⇒ no park.** A stored "pending decision"
   must always have a live delivery path; condition any store write the same way.
7. **Enriching `data` also changes the Telegram payload** (`_notify` fans to all
   channels) — Telegram reads `.text`, so extra `data` fields are ignored, but
   confirm.
8. **Migration number races** across parallel worktrees (highest committed is
   `031`; a new store ⇒ `032`) — re-check at ship time and renumber if needed.
9. **Escape untrusted text** with the top-level `escapeHtml` (`landing.html:246`,
   not the inner one at `:856`); the question is model-generated.
