# 001 — Deploy PTY runtime + QuotaMonitor to production

## What

Push Sprint 30 (PTY harness) and Sprint 31 (QuotaMonitor) to
`origin/main`, restart `hive.service`, verify, then flip
`HIVE_USE_PTY=true` so production runs on the PTY path.

## Why

2026-06-15 is when Anthropic moves headless `claude -p` to
API billing. The PTY path keeps Hive plan-billed. Code is written and
merged to local `main` — it just needs deploying. ~20 days of runway
from now.

## Acceptance

- `origin/main` matches local `main`.
- `hive.service` running with `HIVE_USE_PTY=true` set in `.env`.
- `/quota` Telegram command returns live data from QuotaMonitor.
- Smoke check from `http://100.79.194.84:<port>/` passes (browser, not
  just curl).
- `docs/CHANGELOG.md` entry added.
