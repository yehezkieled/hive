# Harness-agnostic runtime architecture

## Context

Hive runs every entity by spawning a headless `claude -p` subprocess per turn.
From 2026-06-15, Anthropic bills headless / SDK invocations as metered API
usage instead of against the Claude Max subscription — turning a 24/7 fleet
from a flat ~$100/month plan into $1000+/month of metered cost. Hive must move
off headless invocation, and — having now been forced to react to one
Anthropic billing change — must not stay locked to a single vendor.

## Decision

Hive becomes a **harness-agnostic orchestrator**. A *harness* is a standalone
agentic CLI; Hive drives one per entity behind a uniform, turn-level adapter
interface — `send_turn(entity, prompt) -> (response, usage)`.

- **Claude Code** runs as a persistent **interactive PTY session**, not
  headless `claude -p`, so usage stays plan-billed. `--continue` resumes it
  after a restart.
- Three target harnesses: `claude-code` (Claude Max plan), `codex`
  (ChatGPT/Codex plan), `opencode` (provider-agnostic — cheap models such as
  GLM via API).
- **Full capability parity**: any entity may be assigned any harness; no role
  is harness-restricted. Enforced by one rule — *no Claude-specific code
  outside the Claude adapter*.
- v1 ships the `claude-code` adapter only. `codex` and `opencode` are full
  adapters added later behind the same interface.
- Harness is chosen per entity via `/runtime <entity> <harness> [model]` —
  harness first, because the harness decides billing; the model is a
  sub-choice within it. Plan quota is monitored and the user notified on
  exhaustion; automatic failover is deferred.
- Switching an entity between harnesses carries a **summary handoff**, not the
  verbatim transcript.

## Considered options

- **Tiered citizenship** — orchestrators pinned to Claude, only workers float
  across harnesses (what cortexos does). Rejected: it keeps a hard Claude
  dependency for the maestro, defeating vendor independence.
- **Process-shaped interface** — every runtime is a spawned CLI
  (cortexos-style). Rejected for a turn-level interface, so three
  differently-shaped harnesses (persistent PTY, exec-per-turn, other) sit
  behind one contract.
- **Raw-LLM API runtime** — Hive calling model APIs directly. Rejected: it
  would mean reimplementing an agent loop. Existing harnesses (OpenCode for
  cheap models) avoid that.

## Consequences

- Parity is *capability* parity, not *quality* parity — a cheaper model behind
  a harness follows the `<hive_actions>` protocol less reliably. Model-bound
  and accepted.
- No verbatim conversation continuity across a harness switch — only a summary
  survives.
- Each entity becomes a long-lived process (one PTY per entity), not a
  transient subprocess — RAM-bound on the VPS.
- Per-turn token/cost data leaves the clean `stream-json` channel; it now
  comes from the session transcript or the plan-usage endpoint.
- The Claude adapter scrapes terminal output instead of parsing `stream-json`,
  and is more sensitive to Claude Code TUI changes.
