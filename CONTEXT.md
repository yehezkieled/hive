# Hive

Hive is a multi-agent orchestration platform: it runs and coordinates a fleet
of AI coding agents that you control from Telegram.

## Language

### Entities

**Entity**:
Any AI agent Hive runs and manages — always one of: Maestro, Team Lead, or Worker.
_Avoid_: agent, bot

**Maestro**:
A top-level Entity that orchestrates — it owns Teams, spawns and kills other
Entities, and is the Entity you address from Telegram.
_Avoid_: orchestrator, boss, CEO

**Team Lead**:
A mid-tier Entity that runs a Team of Workers on behalf of a Maestro.
_Avoid_: manager, supervisor

**Worker**:
A leaf Entity that carries out one assigned task and reports the result.
A Worker never spawns other Entities.
_Avoid_: subagent, WorkerAgent

**Team**:
A Team Lead together with the Workers it manages, created by a Maestro to
pursue a goal.

### Execution

**Harness**:
A standalone agentic CLI that runs a full agent loop — reasoning, tool use,
file editing — on its own. Hive drives one Harness per Entity. The three Hive
targets are Claude Code, Codex, and OpenCode. A Harness is not a bare model;
it is the whole agent tool wrapped around one.
_Avoid_: runtime, model, LLM, backend

**Adapter**:
The Hive code that drives one Harness and presents the rest of Hive a
uniform, turn-level interface. One Adapter per Harness.

**Runtime**:
The Harness a given Entity is currently assigned to run on. "Switch a Worker's
runtime" means "move it to a different Harness."

**Turn**:
One prompt sent to an Entity and the full response that comes back.

**hive_actions**:
The protocol an Entity uses to act on the rest of Hive — message a peer,
request a spawn, finish a task. The Entity emits a `<hive_actions>` block;
Hive parses it and routes the actions.

### Billing

**Plan-billed**:
A Turn whose cost is covered by a flat-rate subscription — Claude Code on a
Claude Max plan, Codex on a ChatGPT/Codex plan. Capped by the plan's quota
windows, not charged per token.
_Avoid_: subsidised

**API-billed**:
A Turn metered per-token against an API key, paid in real money. Hive treats
API-billed usage as the expensive path, used only by deliberate choice.
_Avoid_: pay-as-you-go, raw API

**Plan quota**:
The usage allowance on a Plan-billed Harness, expressed as utilization
(0–100%) of a rolling window. Two windows run at once — a 5-hour window
and a 7-day window. Plan quota is account-wide: the developer's own
Claude usage draws it down alongside Hive's Entities. It is not the same
as per-Turn token counts. When a window reaches 100%, Turns on that
Harness fail until it resets.
_Avoid_: rate limit, token usage

## Relationships

- A **Maestro** owns zero or more **Teams**
- A **Team** is exactly one **Team Lead** plus one or more **Workers**
- Every **Entity** runs on exactly one **Harness** at a time, through that
  Harness's **Adapter**
- Any **Entity** may be assigned to any **Harness** regardless of its role —
  full capability parity (how *well* it runs still depends on the model)
- A **Harness** is either **Plan-billed** or **API-billed**, depending on how
  it authenticates

## Example dialogue

> **Dev:** "If a Worker is running on the Codex Harness, is it still an Entity Hive manages?"
> **Hezki:** "Yes. The Harness only executes its Turns — Hive still owns the Worker's lifecycle, its Team membership, and its task. Swap the Harness and it's the same Entity."
> **Dev:** "So 'runtime' is just the Harness it's on right now?"
> **Hezki:** "Right. And the Adapter for that Harness is the code that actually drives it."

## Flagged ambiguities

- **"agent" vs "Entity"** — the README and older docs say "agent"; the code's base class is `Entity`. Resolved: **Entity** is canonical.
- **`WorkerAgent` vs "worker"** — the class is `WorkerAgent` but the role string is `"worker"` everywhere else. Naming drift; **Worker** is the canonical term.
- **"runtime" vs "Harness"** — a **Harness** is the external tool; a **Runtime** is which Harness an Entity is assigned to. Not synonyms.
- **"subsidised"** — informal word for **Plan-billed**. Use Plan-billed.
