# Harness pivot — Pi first, headless by default, PTY as fallback

## Status

Accepted, 2026-10-01. Extends [ADR 0001](0001-harness-agnostic-runtime.md) (the
harness-agnostic interface it promised is now real: two harnesses behind one
registry) and supersedes the "PTY is the only runtime" half of
[ADR 0007](0007-pty-only-runtime.md). ADR 0007's reasoning for deleting the old
`ClaudeSession` machinery (`_sessions`, `spawn_entity`, preemption) still stands;
only its "never run headless" conclusion is reversed.

## Context

Hive depended on Claude Code being logged in. Claude Code asks for a fresh login
roughly every 30 days (and on some updates). A logged-out host cannot run any
Entity, so the whole fleet goes dark until someone re-authenticates by hand.

Two billing facts changed since ADR 0007:

- Headless runs (`claude -p`, Agent SDK) currently draw from the same
  subscription usage limits as interactive use. Anthropic announced a separate
  monthly credit for headless use and then paused it, so headless is neither
  API-billed nor separately metered today.
- That could flip back. If headless gets its own credit pool, Hive should spend
  it first and fall back to the interactive PTY when the pool runs out.

## Decision

1. **Harness registry** (`runtime/harness.py`, `runtime/registry.py`). Hive
   detects which harnesses are installed *and signed in* — Pi, Claude Code, and
   (detect-only) Codex — via cheap, spend-free probes cached for 60 s. Preference
   order is `HIVE_HARNESS_ORDER` (default `pi,claude`), overridable per role with
   `HIVE_HARNESS_ORDER_<ROLE>` (e.g. `HIVE_HARNESS_ORDER_LEAD=pi,claude`).
   **Fenced roles default to Claude first**: every role whose lockdown is a
   Claude-only control — the Ownership guard
   ([ADR 0017](0017-ownership-guard-pretooluse-hook.md), Maestros), the role
   tool/skill denylists (Maestros, Leads) and the Vault's Bash/Write/Edit denial —
   defaults to `claude,pi` (`config.FENCED_ROLES`: maestro, lead, vault), using Pi
   only when Claude cannot run the turn (logged out, out of quota). Any other role
   keeps `HIVE_HARNESS_ORDER`. When a fenced role does run on Pi, `/status` marks
   it "⚠️ guardrails NOT enforced" and Telegram is alerted. A harness joins the run
   by adding one `HarnessSpec`; Codex (T015), OpenCode (T016) and a direct
   model-API harness each need exactly that and nothing in the router.
2. **Headless is the default mode; PTY is the fallback**
   (`HIVE_RUN_MODE_ORDER`, default `headless,pty`). New adapters:
   `ClaudeHeadlessAdapter` (`claude -p --output-format stream-json`, continuity
   via `--resume`) and `PiAdapter` (`pi -p --mode json --session-id`). The
   existing PTY `ClaudeAdapter` is unchanged and is now the Claude harness's
   second mode. Pi has no PTY driver, so for Pi the fallback is the next
   *harness*.
3. **`HarnessRuntime` is the one `Runtime` an Entity holds.** Per turn it ranks
   (harness, mode) candidates — harness-major, headless before PTY — and runs the
   first that works. Runtimes are built lazily, so no PTY spawns unless a turn
   needs one.
4. **Fallback is driven by the harness's real error, never guessed**, and only
   for refusals that happen *before any work*: `auth` (logged out), `quota`
   (usage/credit exhausted, rate-limited), `refused` (headless declined),
   `unavailable` (binary missing). Claude Code's own `error` codes
   (`authentication_failed`, `rate_limit`, `billing_error`) decide first; text
   patterns over the harness's own error output decide otherwise
   (`runtime/headless.py`, with captured real samples in
   `tests/runtime/test_headless_errors.py`). Every other failure (`other`:
   crash, timeout, model error) surfaces untouched — it may have failed
   mid-turn, and replaying it on another harness could repeat tool side effects.
   For the same reason an `auth`/`quota` error that arrives *after* the turn
   issued a tool call is reported as `other`, and a run with no final result is
   classified from the harness's stderr only, never from its stdout transcript.
   `auth` blocks the whole harness (its PTY needs the same login); `quota` and
   `refused` block only that mode, for `HIVE_HEADLESS_QUOTA_RETRY_S` (900 s),
   after which headless is tried again; once headless serves a turn again, the
   idle PTY is stopped so the next fallback respawns it on the current
   conversation (`--continue`). Auth blocks last `HIVE_HARNESS_RETRY_S`
   (60 s) and re-probe, so a fresh login is picked up without a restart.
5. **Surfacing.** Each turn's usage dict carries `harness`, `mode` and
   `fell_back`; `/status` shows `via pi/headless`; Telegram gets one line when an
   Entity's harness/mode *changes* (`harness_run` notification; steady state —
   including each Entity's first turn after a restart — is silent unless it fell
   back or runs unfenced). When no (harness, mode) can run — e.g. Claude Code logged out and Pi
   unconfigured — the turn fails with a Telegram-ready message listing every
   harness, its state and the one-line fix, sent as a `harness_unavailable`
   notification (deduplicated to one per 10 min fleet-wide) and also at startup.
6. **Headless never spends API credit.** `ANTHROPIC_API_KEY` /
   `ANTHROPIC_AUTH_TOKEN` are removed from the `claude -p` environment so it can
   only use the logged-in subscription.

Out of scope, by design: the Codex and OpenCode adapters (T015/T016, and their
quota failover) and any direct model-API support (the owner will add it as a
further `HarnessSpec`).

## Considered options

- **Keep PTY-only, add Pi as a second PTY harness.** Rejected: Pi's TUI has no
  transcript sentinel Hive could read as reliably as Claude's `turn_duration`,
  and a PTY per entity is RAM-heavy; headless is one short-lived process.
- **A per-entity config flag instead of per-turn selection.** Rejected: the
  point is to survive a login expiring mid-fleet without a human edit.
- **Fall back on any headless failure.** Rejected for the double-execution risk
  above.
- **Probe by running a real prompt.** Rejected: it spends quota. `claude auth
  status`, `pi --list-models` (or `pi auth check --provider`) and `codex login
  status` read local state only.

## Consequences

- **Pi entities run without Claude-only controls.** Pi has no MCP servers (the
  `search_knowledge` tool is unavailable), no `--allowedTools` equivalent with
  Claude's tool names, no native `/goal` (the dispatcher's `/goal` seed is
  stripped for non-Claude harnesses), and **no PreToolUse hook — the Ownership
  guard ([ADR 0017](0017-ownership-guard-pretooluse-hook.md)) does not fence a Pi
  entity.** Its only fence is its working directory (project root / lead
  worktree). A guard for Pi (an extension hook) is follow-up work; until then
  fenced roles default to `claude,pi` (decision 1) and one that lands on Pi is
  flagged on `/status` and in Telegram as running without its guardrails.
- **Interactive gates do not occur headless** (`--dangerously-skip-permissions`
  under yolo/yotree), so the gate bridge only matters in the PTY fallback.
- **Workflow progress, jam description and the Ticket 020 auto-bounce are PTY
  signals.** Headless turns are bounded by `HIVE_HEADLESS_TIMEOUT_S` (default
  1 h) instead of the PTY's no-progress reader; a timeout is an `other` error and
  is surfaced, not bounced.
- **Pi's real success path is covered by fakes, not a live run on this host:**
  Pi is installed but signed in to no provider here. The Pi event parsing follows
  Pi's documented JSON event schema; the failure paths (`No API key found…`,
  `No models available…`) were captured from the real binary.
- Each headless turn pays a process start (~1 s for Claude Code); the PTY paid
  that once per entity. Accepted for the login-resilience gain.
