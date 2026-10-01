# ADR 0009 — Adopt Claude Code's native `/advisor`; retire the custom advisor

- **Status:** Accepted
- **Date:** 2026-06-10
- **Ticket:** [013](../tickets/013-retire-custom-advisor/)

## Context

Hive shipped its own advisor: an MCP tool (`mcp/advisor_server.py`) that pulled
an Entity's recent bus messages, built an Opus review prompt, and spawned a
one-shot `claude -p --model opus` subprocess, rate-limited (5 min / 20 per day)
and logged to an `advisor_calls` table. Claude Code now provides this natively —
the `/advisor` tool (since CC 2.1.101; fleet runs 2.1.170) pairs a stronger
advisor model with the executor, model-driven, with no subprocess and no MCP
server.

Three facts from `research.md` shaped the decision:

1. **The custom advisor's cross-Entity context is already in the session.** It
   queried the entity's own traffic (`sender=p1 OR recipient=p1`); inbound peer
   messages are prepended to the recipient's prompt (`message_dispatcher.py:116`)
   and outbound ones are the entity's own output — both in the session transcript
   the native advisor reads. The "context parity" loss is near-nil.
2. **Native invocation is model-driven, not per-turn.** Hive's `_suppress_advisor`
   stripped `advisorModel` every spawn, believing the advisor "invokes Opus before
   every response (>90s/turn)". The docs say it fires only at decision points,
   with no per-turn cost — so the suppression was disabling a feature on a false
   premise, and can simply be deleted.
3. **The advisor only pays off when stronger than the main model.** Sonnet→Opus
   is a real lift; Opus→Opus is the same brain grading itself at full price (a
   second whole Opus pass each firing).

## Decision

Retire the custom advisor entirely and adopt native `/advisor`, enabled
**per-Entity** with a **model-aware default**.

- **Per-Entity control:** a `**Advisor**:` field in the role file (mirroring
  `**Model**:`), value = a model or `off`, translated to `--advisor <model>` at
  spawn. The global `advisorModel` setting is removed so the per-spawn flag is the
  single source of truth; the `_suppress_advisor`/`_restore_advisor` dance (and
  its shared-settings concurrency hazard) is deleted.
- **Default when unset = model-aware:** main weaker than Opus → `Advisor: opus`
  (on); main = Opus → `off`. With current roles: **Lead (Sonnet) → Opus on**;
  **Maestro (Opus) → off** (oversight comes from Ticket 019's human phase-gate, not
  an Opus-on-Opus pass); **Worker → off** (explicit in `role-worker.md`; short
  tasks, being retired). Any value is overridable per role/spawn.
- **Decouple the overloaded gate:** `ADVISOR_ENABLED` also gated the
  `--mcp-config` plumbing that carries the unrelated hive-knowledge server.
  Replace it with a computed gate — pass `--mcp-config` iff `generate_mcp_config`
  produced ≥1 server — so knowledge search survives and no empty config is written.
- **Drop** the 5min/20-day rate cap (no native hook; plan-quota via QuotaMonitor
  is the cost lever) and the `advisor_calls` telemetry (native usage shows in
  `/usage` + plan quota); migration `028` drops the table.

## Consequences

- **Positive:** ~14 files of bespoke code removed; no second `claude` binary to
  version-pin (relieves Ticket 009's surface); Plan-billed instead of a separate
  metered call; the shared-`settings.json` concurrency hazard is gone; per-Entity
  model-aware default avoids silent Opus-on-Opus double spend while keeping
  "on by default" for the roles that benefit.
- **Negative / accepted:** context parity drops to session-only (near-nil loss,
  but durable bus history beyond the live session window is no longer fed to the
  advisor); no per-Entity invocation cap (model-driven only) — unbounded advisor
  calls draw plan quota, monitored not capped; `advisor_calls` analytics are lost.
- **Reversal cost:** moderate. Re-adding cross-Entity context means a new
  session-injection step (its own ticket); the `advisor_calls` drop is
  destructive (append-only migration history; the data is not recoverable post-028).

## Alternatives considered

- **Keep a thin cross-Entity context injector** — rejected: the exchange is
  already in-session; re-adds the bespoke machinery being deleted.
- **Blanket `advisorModel: opus` globally (no per-Entity field)** — rejected:
  silently doubles spend on every Opus-main entity.
- **Default-off, opt-in per spawn with strict propagation** — rejected:
  over-built; the intent was "on by default, I choose."
- **Rename `ADVISOR_ENABLED` to a static `MCP_CONFIG_ENABLED`** — rejected: a
  hand-maintained flag still risks an empty `--mcp-config`; the computed "any
  server?" gate is correct by construction.
- **Retain the rate cap / telemetry** — rejected: native has no cap hook and
  duplicates QuotaMonitor; both re-impose what the simplification removes.
