# Plan

Skipped artifacts: `questions`, `research`, `design`, `outline` —
this ticket is mechanical (no design fork, no investigation).

## Steps

1. `git log origin/main..HEAD` — review what's about to ship.
2. `git push origin main`.
3. `systemctl --user restart hive.service`.
4. `journalctl --user -u hive.service -n 50 --no-pager` — confirm
   clean startup, no Python tracebacks.
5. Smoke from Tailscale IP — `http://100.79.194.84:<port>/` returns
   the dashboard in a browser (not just curl).
6. Edit `.env` on host — set `HIVE_USE_PTY=true`.
7. `systemctl --user restart hive.service`; re-check journalctl.
8. Telegram: `/quota` — verify response shows the 5h + 7d windows
   with percentages.
9. Telegram: `/m:dev <test msg>` — verify a Turn completes under the
   PTY path (look for PTY-specific log lines in journalctl).
10. Add to `docs/CHANGELOG.md`:
    `2026-MM-DD — Sprint 2026-Q2-S1: PTY runtime + QuotaMonitor live`.

## Rollback

If PTY misbehaves: `unset HIVE_USE_PTY` in `.env`, restart
`hive.service`. Headless `claude -p` path resumes. Headless is still
plan-billed until 2026-06-15.

## Cross-cutting impact

None. This ticket does not edit reference docs.
