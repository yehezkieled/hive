# 022 — Maestro must delegate research, not do it itself (un-bridged interactive gate)

## What

When a Maestro is asked to "do research" (or given a vague goal), it
should **propose a Team and wait** — never run the research/interview
itself. Today a "do research yourself"-style goal makes the Maestro
invoke an **interactive prompt** (a thinking-skill interview or a
permission-gated tool) that the PTY Harness has no human to answer and
Hive's gate bridge (Ticket 003) does not cover, so the Turn hangs to
the 180s no-progress timeout. Fix it so the Maestro's autonomous path
can never reach an un-bridged interactive prompt.

## Why — reproduced 2026-06-10

Differential test against deployed maestro `otter`, same session, via
the web command path:

```
"spin up leads and try to DO RESEARCH on startup ideas"
    → JAM. ~/.claude/sessions/<pid>.json: waitingFor="permission prompt"
    → 180s TimeoutError, no result (happened on 2.1.170 AND 2.1.172)

"Propose a team plan to build an expense-tracker, do NOT research"
    → WORKS, full plan in 40s

"Spin up a research TEAM … do not research yourself, propose + wait"
    → WORKS, full team proposal in 39s
```

The constant: the Maestro doing **interactive work itself** hangs; the
Maestro **delegating** works. The maestro JD already says "propose
teams, wait for approval," but two things tip it into self-service:

1. A "do research" phrasing tempts the model to research directly.
2. The JD *encourages* thinking skills for vague goals — "`/grill-me`
   and `/brainstorming` — interview the user to sharpen a vague goal."
   ADR 0008 deliberately keeps these for the Maestro on the premise
   that "its gates bridge to Telegram." But the observed gate surfaced
   as a **permission prompt**, not an `AskUserQuestion`, so Ticket
   003's bridge never fired and the user got an error, not a question.

So the ADR 0008 premise has a hole: *some* maestro interactive paths
produce prompts that are **not** the bridgeable `AskUserQuestion` /
`ExitPlanMode` kinds.

## Open questions (→ research/design)

- **Pin the exact trigger.** Reproduce interactively (not headless `-p`
  — headless auto-denies and continues, masking it) with temporary
  instrumentation, and identify what prompts: a thinking-skill
  execution permission, `WebSearch`/`WebFetch`, or a Bash/tool call
  inside a skill. Headless `--dangerously-skip-permissions` did NOT
  prompt for `WebSearch`/MCP/Bash — so the gap is interactive-mode
  specific.
- **Which fix layer(s):**
  - (a) **JD** — a vague/"do X" goal routes to *propose a Team*;
    reserve `/brainstorming` / `/grill-me`-the-user for when the user
    explicitly asks to be interviewed. Cheapest; behaviour-shaping.
  - (b) **Denylist** — deny the Maestro the specific tool(s) that
    produce un-bridged interactive prompts in autonomous context
    (builds on Ticket 015's `tool_policy.role_tool_denylist`). Only
    once (a)'s repro pins the tool.
  - (c) **Bridge** — detect the prompt via the new
    `sessions/<pid>.json` `waitingFor` signal (see Ticket 020) and
    bridge it to Telegram like Ticket 003 does for plan/ask gates.
    Biggest; revisits ADR 0005 + ADR 0008.
- Does (a) alone suffice for the live fleet, with (b)/(c) as
  defence-in-depth?

## Acceptance

- A "do research / vague" goal to a Maestro produces a **Team proposal
  + wait**, never an un-bridged interactive prompt — proven on
  deployed code (the exact goal that jammed on 2026-06-10 now returns
  a proposal).
- If the bridge route (c) is taken: a maestro permission prompt
  surfaces on Telegram and is answerable; otherwise the Maestro never
  reaches one.
- `ruff` + `pytest -m "not integration"` green; relevant JD / policy
  tests updated.

## Non-goals

- The generic auto-bounce recovery net — Ticket 020 (complementary:
  020 recovers *any* jam; 022 prevents *this* one).
- Reworking the gate bridge for Leads/Workers (they escalate to a
  parent by design).

## Workaround (live, until this lands)

Phrase Maestro goals as delegation — "spin up a team to do X", not
"do X". Verified working 2026-06-10.

## Notes

Found diagnosing the failed 015 live smoke test. Pairs with Ticket 020
and touches the premises of ADR 0005 (permission prompts now have a
detection channel) and ADR 0008 (maestro thinking-skill gates don't
all bridge).
