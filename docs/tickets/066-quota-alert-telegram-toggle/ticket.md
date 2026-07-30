# 066 — Silence plan-quota pings on Telegram (`HIVE_TELEGRAM_QUOTA_ALERTS`)

> Un-sprinted live-annoyance fix, found while dogfooding on 2026-07-29.
> **Implementation is written, tested, and deployed on this host but NOT yet
> merged** — the code ships in its own PR; this ticket lands first.

## What

Give plan-quota notifications their own Telegram off-switch, independent of the
Ticket 041 actionable-alert gate.

- New `QUOTA_KINDS` frozenset in `notifications/dispatcher.py` — the five kinds
  `QuotaMonitor` emits (`quota_warn`, `quota_urgent`, `quota_exhausted`,
  `quota_monitor_blind`, `quota_monitor_recovered`). Deliberately **disjoint**
  from `ALERT_KINDS`.
- New `HIVE_TELEGRAM_QUOTA_ALERTS` config knob (default `true` — unset changes
  nothing for anyone else).
- A second, independent gate in `telegram/bridge.py::send`.
- Set `HIVE_TELEGRAM_QUOTA_ALERTS=false` in this host's `.env`.

Quota utilisation stays fully visible — `/quota` on demand and the web quota
chip are untouched. Only the unsolicited Telegram pings stop.

## Why

**The pings were relentless and had no off-switch.** The observed symptom was a
Telegram ping roughly every 10–15 minutes, all day, on the bot named `wonder`
(which is Hive's own `TELEGRAM_BOT_TOKEN`, not a Claude Code channel bot).

**Root cause is the monitor flapping, not the usage bands.** `QuotaMonitor`
polls every 180s with a symmetric debounce of 5: five consecutive failures fire
`quota_monitor_blind`, five consecutive successes fire
`quota_monitor_recovered`. Journal evidence over 7 days:

```
1694 poll failures / 7 days  ≈ 242/day   vs ~20 polls/hour
  → roughly half of all polls fail (HTTP 401 Unauthorized, HTTP 429 Too Many Requests)
  → the monitor oscillates blind → recovered → blind all day
  → one ping pair every ~15 min
```

So the noise came from the monitor's **reachability meta-alerts**, not the
80/90/100% band crossings (those fire once per band per window).

**The existing knob would have made things worse.** `HIVE_TELEGRAM_ALERTS`
(Ticket 041) gates only `ALERT_KINDS` — `decision_request`, `mode_request`,
`vault_action_pending`, `workflow_completed`, `workflow_failed`. No quota kind is
in that set, so flipping it off would have silenced **decisions and payment
approvals** — the alerts that actually matter — while the quota pings kept
arriving. Hence a separate, disjoint gate rather than widening the existing one.

## Acceptance

- `HIVE_TELEGRAM_QUOTA_ALERTS=false` suppresses all five quota kinds on Telegram;
  default `true` preserves today's behaviour.
- The two gates are provably independent in both directions: quota off still
  relays decisions/approvals, and `HIVE_TELEGRAM_ALERTS=false` still relays quota.
- `QUOTA_KINDS` is disjoint from `ALERT_KINDS`, with a drift guard asserting every
  band kind `QuotaMonitor` emits is covered (a future 95% band cannot bypass the
  gate).
- `ruff check` + `ruff format --check` clean; full `pytest -m "not integration"`
  green.
- Deployed + verified on this host: live config reads
  `TELEGRAM_QUOTA_ALERTS=False` / `TELEGRAM_ALERTS=True`.

## Status of the work

Done but unmerged, as of 2026-07-30:

- Code + `.env` written; 10 new tests in
  `tests/test_telegram_quota_alerts_toggle.py`.
- `ruff` clean; full suite **1432 passed, 2 skipped**.
- `hive.service` restarted (all entities idle first — nothing interrupted),
  clean startup, web smoke `HTTP 200` from the Tailscale IP.
- **Not committed** — the code lands in a separate PR from this ticket doc.

## Non-goals

- **Fixing the flapping itself.** The ~50% poll-failure rate (401/429) is the
  *trigger* for the noise, not the noise. Worth its own ticket — the 401s point
  at credential/token handling and the 429s say Hive is polling the undocumented
  OAuth endpoint too aggressively (a back-off on 429 is the obvious fix). Muting
  the pings deliberately does **not** address it.
- Removing or re-tuning the band thresholds (80/90/100) or the poll interval.
- Suppressing quota on other channels — SSE (web) and email are not "pings"; Web
  Push already filters to `ALERT_KINDS`, so quota never reached it.
- The unrelated drive-by spotted alongside this: httpx logs the full Telegram Bot
  API URL — including the bot token — to journald on every restart.
