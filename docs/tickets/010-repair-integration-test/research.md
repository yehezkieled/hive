# 010 — Research

Findings, each grounded in the current tree.

## Why it fails today — two causes, not one

`tests/integration/test_lead_worker_roundtrip.py`
(`test_worker_replies_to_lead_via_hive_actions`):

1. **Manual entity wiring.** It builds the hierarchy by hand —
   `mgr._entities[lead.name] = lead`, `router.register(...)` — instead
   of the facade. It never registers the `dev` maestro the lead claims
   as parent.
2. **No fake adapter injected.** It calls `mgr.send_to_entity(worker, …)`
   with nothing overriding the adapter. The autouse guard
   `_no_real_pty` (`tests/conftest.py:25`) monkeypatches
   `ProcessManager._get_or_create_adapter` to **raise `RuntimeError`** —
   so the test errors out before any assertion. Without the guard it
   would spawn a real `claude` and hang on `read()`.

It also assumed a **real model** would emit a correct `<hive_actions>`
block — non-deterministic even if it ran.

## The seam exists and is well-trodden

- `tests/fakes.py` — `FakeAdapter` + `using_adapter(mgr, fake)`. The
  ticket's `tests.fakes.using_adapter` is **this module** (a file, not a
  `tests/fakes/` package). `using_adapter` overrides
  `_get_or_create_adapter` to return the fake and registers it in
  `mgr._adapters`, so `kill_all` sees it. Used by ~20 unit tests.
- `FakeAdapter(responses)` returns canned `send_turn` text as
  `(text, usage)`. A `str` = one turn; a `list` = successive turns.

## The facade API is intact post-004

All on `ProcessManager` (`src/hive/process/manager.py`):
- `register_maestro` (:300), `create_team` (delegates to lifecycle),
  `spawn_worker` (delegates to lifecycle) — the clean setup idiom.
- `send_to_entity` (:318) → `dispatcher.send_to_entity`.
- `_last_routed_actions` (:129) is populated by the dispatcher when it
  routes a parsed action (`message_dispatcher.py:272, 297-298, 340-341`).
- `kill_all` (:389), `router`, `audit_log` attributes — all present.
- Adapters are **lazy**: `create_team`/`spawn_worker` do not spawn one,
  so hierarchy setup does not trip the guard (peer-messaging tests
  prove this — they set up leads/workers with no fake and pass).

Clean construction idiom (from `tests/test_peer_messaging.py:111-121`):
`ProcessManager(router=router, audit_log=audit_log, max_sessions=2)`.

## `<hive_actions>` is JSON-in-tags

Parser: `src/hive/bus/actions.py:132` `parse_actions`. The body is a
**JSON array**, not XML child tags. Verified example
(`tests/test_process_manager.py:741-743`):

```
<hive_actions>
[{"type": "message", "to": "dev.backend", "text": "task complete"}]
</hive_actions>
```

`send_to_entity` runs the turn, then parses + routes the block
("Phase 3", `message_dispatcher.py:239`).

## Redundancy — repair, not delete

`tests/test_process_manager.py::TestActionRouting` already drives
`send_to_entity` through a `FakeAdapter` whose response contains a
`<hive_actions>` block and asserts routing
(`:730 test_message_routed_to_recipient`, `:775` worker→other lead).

What this test **uniquely** covers, and must keep to earn its place:
- worker → **its own lead** via the dotted-name convention
  (`dev.backend.w1` → `dev.backend`), the canonical report-up path;
- the **audit-log** assertion — `peer_message_sent` with
  `actor=worker, target=lead` — via `audit_log.recent(action_prefix=…)`
  (`src/hive/bus/audit_log.py:55`). No unit test asserts this.

## Marker state

`pyproject.toml:51-53`:
```
markers = [
    "integration: tests that require real claude -p sessions (skipped in CI)",
]
```
`testpaths = ["tests"]` collects the `integration/` folder by default;
CI excludes it via `-m "not integration"`. This file is the **only**
test carrying the marker. Post-007 nothing requires real `claude -p`,
so the marker's stated reason no longer exists.
