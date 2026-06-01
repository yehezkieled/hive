# QuotaMonitor v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A notify-only sensor that polls Anthropic's plan-quota endpoint and alerts via Telegram before the 24/7 Hive fleet hits a 5-hour or 7-day quota wall. Designed as a *queryable* sensor so future quota-aware features (Layers 2 & 3) can read its state without redesign.

**Why now:** The PTY harness migration moves Hive onto plan-billed (Max-plan flat-rate) usage. Plan quota becomes the cost ceiling — hit it and the fleet stalls. There is no warning today.

**Out of scope:** auto-actions (Layer 2), quota-aware multi-Maestro planning (Layer 3 — see `ROADMAP.md` Phase 5), DB persistence of readings (Phase 5 dashboard widget).

**Architecture:** A `QuotaMonitor` class is a manager-owned background asyncio task. Each poll re-reads `~/.claude/.credentials.json` and GETs `https://api.anthropic.com/api/oauth/usage` with the OAuth bearer token and the `anthropic-beta: oauth-2025-04-20` header. The endpoint is undocumented (community-discovered) — see `docs/adr/0002-quota-from-undocumented-oauth-endpoint.md`. Successful parses produce a `QuotaReading` (5h + 7d, each with utilization + reset, plus `fetched_at`) held in memory. Threshold crossings (80 / 90 / 100) fire one alert per `(window, band)` via the `NotificationDispatcher`; the fired-band set is cleared per-window when that window's `resets_at` advances. Failures skip silently; after ~15 min of continuous failure a "monitor blind" meta-alert fires, with a recovery ping when polling resumes. A `/quota` Telegram command exposes the current reading on demand.

**Tech stack:** Python 3.11+, asyncio, stdlib `urllib.request` (no new HTTP dependency), pytest + pytest-asyncio (auto mode), existing `hive.notifications.NotificationDispatcher`.

---

## File structure

| Path | Responsibility |
|------|----------------|
| `src/hive/runtime/quota_monitor.py` | new — `WindowReading`, `QuotaReading`, `QuotaMonitor` |
| `src/hive/runtime/__init__.py` | add exports |
| `src/hive/commands/quota.py` | new — `/quota` command handler |
| `src/hive/commands/dispatch.py` | register `/quota` |
| `src/hive/process/manager.py` | own + start/stop `quota_monitor` |
| `src/hive/config.py` | add `HIVE_QUOTA_POLL_SECONDS` (default 180) |
| `tests/runtime/test_quota_monitor.py` | new — 21 behaviour tests for the monitor |
| `tests/commands/test_quota_command.py` | new — 3 behaviour tests for `/quota` |
| `docs/adr/0002-quota-from-undocumented-oauth-endpoint.md` | new — data-source decision |

---

## Behaviors (TDD checklist)

### Polling & parsing
- [x] 1. Poll calls the endpoint with `Authorization: Bearer <token>` and `anthropic-beta: oauth-2025-04-20`.
- [x] 2. Successful response parses `five_hour` and `seven_day` into a `QuotaReading`.
- [x] 3. Codename / per-model keys (`seven_day_sonnet`, `omelette`, `tangelo`, `cowork`, …) are not surfaced.
- [x] 4. Credentials file is re-read every poll — a changed token is used next poll.
- [x] 5. `get_quota()` returns `None` before the first successful poll.
- [x] 6. `get_quota()` sets `fetched_at` to the successful-poll time.

### Threshold / alert logic
- [x] 7. Crossing 80% upward fires one alert.
- [x] 8. Same band does not re-fire on subsequent polls while still above threshold.
- [x] 9. 90% and 100% upward crossings each fire their own alert.
- [x] 10. Missed-poll jump (60% → 95%) fires only the highest band crossed.
- [x] 11. `five_hour` and `seven_day` are alerted independently.
- [x] 12. A window's `resets_at` advancing clears *that* window's fired bands; the other window's stay.
- [x] 13. Alert text includes the window name, the band crossed, and `resets_at`.

### Failure handling
- [x] 14. Transient HTTP failure (timeout / 5xx / 401) logs, skips, does not crash.
- [x] 15. `fetched_at` is *not* updated on a failed poll.
- [x] 16. Defensive parsing: missing `five_hour` / `seven_day` keys treated as failure.
- [x] 17. N consecutive failures (default 5 ≈ 15 min) fire the "monitor blind" meta-alert once; a single success resets the counter.
- [x] 18. Recovery after the meta-alert fires the "back online" ping; meta-alert re-arms.
- [x] 19. The poll loop never propagates exceptions — any unexpected error is caught.

### Lifecycle
- [x] 20. `start()` begins polling; `stop()` cancels the polling task cleanly.
- [x] 21. After restart (fresh monitor) with an already-over-threshold window, the alert fires again — documents the in-memory trade-off.

### `/quota` command
- [x] 22. Returns current 5h + 7d utilization and reset times.
- [x] 23. Notes staleness when `fetched_at` is older than 2× poll interval.
- [x] 24. Handles the "no reading yet" case with a clear message.

---

## Wiring

- [x] `ProcessManager` owns `quota_monitor` and starts/stops it alongside other background tasks.
- [x] `CommandDispatcher` registers `/quota`.
- [x] `runtime/__init__.py` exports `QuotaMonitor`, `QuotaReading`, `WindowReading`.
- [x] `config.py` adds `HIVE_QUOTA_POLL_SECONDS` (int, default 180).

## Verification

- [x] `pytest tests/runtime/test_quota_monitor.py tests/commands/test_quota_command.py -v` — all 24 tests pass.
- [x] `pytest` — full suite green, no regressions.
- [x] `ruff check src/ tests/` clean.
- [x] `ruff format --check src/ tests/` clean.
- [x] On the live VPS, after restart: `/quota` returns a reading; logs show poll cycles.

## Out of scope (deferred)

- Auto-actions on quota (Layer 2) — harness-switch needs Phase 4 adapters; auto-pause/resume needs separate design.
- Quota-aware multi-Maestro planning (Layer 3) — ROADMAP Phase 5.
- DB persistence + history (Phase 5 dashboard widget).
- Per-model alerting (`seven_day_sonnet`, `seven_day_opus`).
- Configurable thresholds per window — v1 uses 80/90/100 same for both.
