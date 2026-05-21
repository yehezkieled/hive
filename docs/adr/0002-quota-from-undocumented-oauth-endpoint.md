# ADR 0002 — Plan quota is read from an undocumented OAuth endpoint

**Date:** 2026-05-20
**Status:** Accepted

## Context

QuotaMonitor must report the current Anthropic plan-quota utilization (5-hour and 7-day rolling windows) so the Hive fleet, running 24/7 on the plan-billed PTY harness, gets a warning before hitting a quota wall. Hitting 100% silently stalls every Entity's Turns. The only authoritative source of plan-quota utilization is Anthropic's account-side accounting — local Hive state cannot reconstruct it.

## Decision

QuotaMonitor reads plan quota by polling:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken from ~/.claude/.credentials.json>
anthropic-beta: oauth-2025-04-20
```

The response is a JSON document containing `five_hour.utilization`, `seven_day.utilization`, and matching `resets_at` timestamps as direct percentages (0–100). The endpoint is **undocumented** — discovered and shared by the Claude Code community (the `claude-code-statusline` project and similar tools). Anthropic has not committed to maintaining it; an open feature request for an official equivalent (`anthropics/claude-code#44328`) is unresolved at time of writing.

QuotaMonitor calls this endpoint, treats the response as authoritative, and is designed to fail gracefully if it ever moves or changes shape — see the "monitor blind" meta-alert in the implementation plan.

## Alternatives considered

1. **Sum local `~/.claude/projects/**/*.jsonl` token counts and apply known limit constants** — what community tools (`ccusage`, etc.) do. **Rejected:** independent reporting found `.jsonl` token logs under-count by ~46× (input tokens 100–174× off; output 10–17× off). The local logs are not a reliable basis for a plan-quota percentage. Also account-blind: it only sees Claude Code usage on this host, missing claude.ai web usage that draws down the same plan.
2. **Scrape Claude Code's interactive `/usage` slash command via a PTY.** **Rejected:** requires spinning a PTY just to read a number; the output format is human-readable and would break with any UI tweak. Strictly worse than a direct HTTP call.
3. **Wait for an official endpoint.** **Rejected:** the 2026-06-15 plan-billed cutover lands before any official endpoint is likely to ship.

## Consequences

- **Pro:** Authoritative, account-accurate percentages with zero token math. Returns both windows together. Costs one tiny HTTPS GET every 3 minutes.
- **Con:** Undocumented — Anthropic can change or remove it without notice. Mitigated with defensive parsing (missing expected keys → treat as failure) and a "monitor blind" meta-alert that fires when polls fail continuously, so silent breakage becomes a visible alert rather than false comfort.
- **Con:** Couples QuotaMonitor to Anthropic OAuth. When Codex / OpenCode adapters land (Phase 4), each will need its own quota source — no shared abstraction here. Acceptable: each plan has its own quota model and likely its own endpoint shape.
- **Trigger to revisit:** Anthropic ships an official, documented usage endpoint (e.g. via the issue above) → swap the source, keep the rest of QuotaMonitor unchanged.
