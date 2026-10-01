# Permission-prompt gate deferred: no transcript signature

## Status

Accepted — 2026-06-01

## Context

Ticket 003 added a transcript-only interactive-gate bridge for the PTY
harness. Two of the three gates ship and work structurally:

- **plan** — an `ExitPlanMode` `tool_use` / a `plan_mode` attachment
- **ask** — an `AskUserQuestion` `tool_use`

The third, **tool-permission prompts** ("Allow `Bash(rm …)`? Yes/No"), was
human-in-the-loop (#26): its transcript shape was unverified and had to be
captured from a real session before a detector could be built.

## Decision

**Defer #26.** Permission prompts are **not** structurally detectable in the
`.jsonl` transcript the way plan/ask gates are.

A permission prompt fires on an **ordinary** tool (`Bash`/`Write`/`Edit`/…).
In the transcript, a parked prompt is just an **unmatched `tool_use`** for that
tool — indistinguishable from a tool call that is still executing. There is no
dedicated "awaiting permission" event: verified across 638 existing transcripts
plus three live `--permission-mode default` captures. The only permission
markers in the schema are the `permission-mode` *setting* and a
`command_permissions` *config* snapshot (`allowedTools` list). Plan/ask gates
are detectable only because they use **unique tool names**; permission prompts
have no unique signature.

## Consequences

- #26 ships no detector. The bridge covers the **plan** and **ask** gates.
- Hive runs Entities with `bypassPermissions`/`yolo`, so permission prompts do
  not occur in normal operation — the gap is largely moot in practice.
- If permission gating is ever required, the only path is a **time-based
  heuristic** (an ordinary `tool_use` unmatched for > N seconds → treat as a
  permission gate), accepting false-positives on slow but legitimate tool
  calls. That trades the bridge's structural-detection guarantee for a
  screen-state heuristic and must be a deliberate, separately-scoped decision.

## Findings

Full capture evidence:
`docs/tickets/003-plan-interaction-bridge/research.md` §
"#26 permission-prompt gate — capture findings".
