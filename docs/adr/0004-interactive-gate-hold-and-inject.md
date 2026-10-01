# Interactive-gate handling: hold-and-inject, human-in-the-loop

## Context

On the PTY (Claude Code) Harness, an Entity that hits an **interactive gate**
— plan-mode approval (`ExitPlanMode`), an `AskUserQuestion` call, or a tool
permission prompt — freezes the subprocess on a TUI menu. Nothing presses a key, so no assistant turn is
written and `TranscriptReader.await_next_assistant_turn` times out at 180s; the
Turn fails. Headless `claude -p` never had this problem — those points resolved
non-interactively. The PTY is the plan-billed runtime Hive depends on after the
2026-06-15 headless-billing cutoff (Phase 1), so the hang must be fixed without
disabling planning (the user wants planning kept) and without screen-scraping
(ADR 0001 — the transcript is the source of truth).

## Decision

When an Entity hits a gate, **hold the Turn open on the menu and inject the
user's decision as a keypress** ("hold-and-inject"), rather than escaping the
menu and re-prompting on a fresh Turn. The model mirrors Claude Code's own app:
the Turn freezes and waits for the human, indefinitely, with no automatic
decision.

- **Detection is structural**, from the session `.jsonl` transcript (a
  `tool_use` named `ExitPlanMode` / `AskUserQuestion` with no matching
  `tool_result`, or an `attachment.type == "plan_mode"`) — never screen-scraping.
- A gated Entity enters a new explicit `WAITING`/`GATED` state. While gated it
  is **exempt from idle-kill** (`kill_idle_entities`) and the **180s reader
  timeout is suspended** — without both, Hive's own machinery would reap the
  parked Turn.
- The gate is surfaced by **reusing the existing pending-approval row** (the
  `request_mode_change` / `/approve` shape), which already spans Telegram
  buttons and the web dashboard.
- The user's approval wakes the blocked Turn via an **in-memory signal**
  (`asyncio.Event`, the "doorbell") keyed to the gate; the Turn then injects the
  keypress and resumes. The persistent approval row is the durable surface; the
  in-memory signal is the wake path.
- **No automatic resolution.** Hive never auto-approves or auto-denies on a
  timer; it parks indefinitely and only re-pings the user.

## Considered options

- **Escape-and-re-prompt (B2).** Press Esc to free the PTY immediately, park the
  plan as an async approval row, and on approval re-prompt the Entity with the
  captured plan text as a fresh Turn. Rejected: it severs the maestro's
  plan→execute continuation. An orchestrator that just planned holds rich live
  context (why this plan, the first step, which Workers to spawn); re-prompting
  forces it to reconstruct that — wasteful and error-prone exactly where it
  matters most. B2 reused Hive's async approval machinery wholesale, but that
  machinery cannot wake a Turn that is blocked *right now*.
- **DB-poll for the wake (W2).** The blocked Turn polls the approval row's
  status instead of waiting on an in-memory signal. Rejected for normal use: it
  churns the database for the full (possibly hours-long) decision window. Its
  one advantage — surviving a Hive restart mid-decision — is not worth it, since
  a restart kills the held Turn anyway; "re-ask on reboot" is acceptable for a
  rare case.
- **Hold-and-inject à la cortexOS.** cortexOS solves the same problem by
  navigating the live menu (Down×N + Enter) driven by an always-on fast-checker
  daemon with hook/screen-based detection. We keep its hold-and-inject
  *mechanism* but detect from the transcript, not the screen, per ADR 0001.

## Consequences

- A new `WAITING`/`GATED` Entity state — with idle-kill exemption and
  reader-timeout suspension — must exist before this can work.
- A maestro parked on a gate cannot take new Turns (its single PTY is held on
  the menu); new messages to it queue behind the gate. Intended — the gate is
  answered first — but a visible behaviour change on Telegram.
- A Hive restart while an Entity is parked orphans the held Turn; the approval
  row persists but has no coroutine behind it. Recovery (re-spawn + re-detect,
  or mark stale and re-ask) is deferred to the design.
- A decision made after a plan-quota window hits 100% will fail on the resulting
  work — the generic quota failure, not gate-specific.
- Shipping order: plan-approval gate first, `AskUserQuestion` second, tool
  permission prompts third — the permission-prompt transcript shape is
  unverified and must be captured and confirmed detectable (no screen-scraping)
  before its detector is built.
