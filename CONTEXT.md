# Hive

Hive is a multi-agent orchestration platform: it runs and coordinates a fleet
of AI coding agents that you control from Telegram.

## Language

### Entities

**Entity**:
Any AI agent Hive runs and manages — always one of: Maestro or Team Lead.
_Avoid_: agent, bot

**Maestro**:
A top-level Entity that orchestrates — it owns Teams, spawns and kills other
Entities, and is the Entity you address from Telegram.
_Avoid_: orchestrator, boss, CEO

**Team Lead**:
A mid-tier Entity that runs a Team on behalf of a Maestro, fanning leaf
work out through the Claude Code **Workflow** tool.
_Avoid_: manager, supervisor

**Worker** _(retired)_:
The former persistent leaf Entity. Worker creation was banned on every
path (lead, maestro, user) per ADR 0013 (Ticket 016) and the entity type
was deleted in Ticket 018. Leaf work now runs as ephemeral **[[Leaf
agent]]s** inside a Lead's **Workflow run** — see **Leaf agent** below.
_Avoid_: subagent, WorkerAgent

**Leaf agent**:
An ephemeral agent spawned inside a Lead's Workflow run to carry out one
slice of leaf work. Not an Entity — it has no Hive lifecycle, org-tree
presence, or mailbox; it exists only for the duration of the run and
returns its result to the Lead.
_Avoid_: worker, subagent, Workflow worker

**Team**:
A Team Lead and the leaf work it runs, created by a Maestro to pursue a
goal.
_Note_: with the Worker entity retired (ADR 0013) and deleted (Ticket
018), a Team in practice is a Team Lead plus its Workflow runs.

**Entity name**:
An Entity's identifier, which doubles as its **address**. A **name
component** — a Maestro's name or a Team's name (e.g. `otter`, `backend`) —
never contains a dot. The dotted form `maestro.team` (e.g. `otter.backend`)
is the **address**: Hive inserts the `.` as the hierarchy separator and
reads identity by splitting on it (so `otter.backend` is Team `backend`
under Maestro `otter`). Validated at creation (Ticket 032).
_Avoid_: handle, slug.

**Project**:
A codebase a Maestro owns — a first-class registry record (name → root
path → owning Maestro, nullable). **Not an Entity.** At most one Maestro
owns a Project, and a Maestro owns at most one Project; the **PA Maestro**
owns none. Distinct from the project-management sense of "project"
(Sprints/Tickets) — see Flagged ambiguities.
_Avoid_: repo (a Project is the ownership record, not the git repo),
workspace.

**Project ownership**:
The 1-Project-↔-≤1-Maestro model plus the write-policy it drives: a
project Maestro writes only its own Project; the **PA Maestro** reads any
Project but writes only **ownerless** ones. Enforced by the **Ownership
guard** (Ticket 024, [ADR 0017](adr/0017-ownership-guard-pretooluse-hook.md)).

**Ownership guard**:
The enforcement layer behind **Project ownership**: a Claude Code
`PreToolUse` hook, generated into a per-spawn settings file and injected
with `--settings`, that blocks an Entity's file-edit tools
(`Write`/`Edit`/`MultiEdit`/`NotebookEdit`) on paths outside its writable
set. It fires **even under bypass mode** (where permission `deny` rules do
not), but fences only the file tools — a guardrail against accidental
cross-project writes, **not** a `Bash`/subprocess-proof wall.
_Avoid_: permission rule, sandbox, deny rule.

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
The Harness a given Entity is currently assigned to run on. "Switch a Lead's
runtime" means "move it to a different Harness."

**Turn**:
One prompt sent to an Entity and the full response that comes back.

**Turn-end sentinel**:
The record a Harness itself writes into its transcript when a Turn truly
completes — on Claude Code, the `turn_duration` system entry. Written by
the Harness binary, so it is deterministic: the model cannot forget,
fake, or race it, unlike anything the model emits. Hive accepts a Turn
on the sentinel; quiescence guessing is fallback only.
_Avoid_: done-marker, end-of-turn message

**Session pinning**:
Binding an Entity's Adapter to the exact Harness session it spawned —
identified by the Harness's own session record — instead of inferring
which transcript is the Entity's from directory activity. Eliminates
silent cross-Entity transcript mix-ups when sessions share a directory.
_Avoid_: transcript guessing, session sniffing

**Workflow run**:
A Lead's single execution of the Claude Code **Workflow** tool — one
deterministic fan-out of **Leaf agents**, carried out inside one of the
Lead's **Turns**. It is the unit Hive surfaces as live progress (agent
count / phase / completion) on the dashboard and Telegram.
_Note_: observed **read-only** — Hive watches a run, it does not steer it
(steering is later scope). A run is alive only while its Lead's Turn is in
flight.
_Avoid_: job, batch, "the Workflow" (the tool) vs. one run of it.

**Interaction pattern**:
A named, reusable coordination shape for a **[[Workflow run]]** — e.g.
**debate**, blackboard, tournament. Delivered as a **recipe in the role files**
(prose + a Workflow-script skeleton), pushed into an Entity's prompt at spawn,
not as a skill or saved script (ADR 0020). Asymmetric depth: a **Team Lead**
gets the full executable recipe and *runs* the pattern; a **Maestro** gets only
a menu of names and *names* one in the contract it hands a Lead (it cannot drive
a Workflow itself). Adds no engine capability — it makes free-form fan-out
authoring a consistent, shared vocabulary.
_Note_: `debate` ships in Ticket 034; `blackboard` (035) and `tournament` (036)
follow in S8 on the same mechanism.
_Avoid_: skill, template, macro, "the workflow" (one pattern is a *shape* a run
takes, not the run).

**debate**:
The first **[[Interaction pattern]]**: N **[[Leaf agent]]s** each argue a
different answer to one question, running in parallel and blind to each other;
then one judge Leaf agent reads all the cases and returns a verdict with reasons.
One round, one answer per agent. For decisions over a wide solution space or a
claim needing adversarial scrutiny (the 2-side "true vs false" form is
adversarial-verify). Distinct from blackboard (agents collaborate on a shared
artifact) and tournament (candidates pruned in rounds).
_Avoid_: vote, poll, ensemble.

**hive_actions**:
The protocol an Entity uses to act on the rest of Hive — message a peer,
request a spawn, finish a task. The Entity emits a `<hive_actions>` block;
Hive parses it and routes the actions.

**Interactive gate**:
A point mid-Turn where the Harness pauses for human input rather than
completing the Turn — plan-mode approval (`ExitPlanMode`), an
`AskUserQuestion` call, or a permission prompt. On the PTY Harness a gate
blocks the Turn until answered and Hive bridges it (hold-and-inject).
_Avoid_: prompt, menu, interrupt

**Auto-bounce**:
Hive automatically killing a jammed Entity's Harness session and respawning
it — the conversation preserved via the Harness's own resume — when the
Entity stalls past a threshold and is **not** legitimately waiting (at an
**[[Interactive gate]]**, on a live **[[Workflow run]]**, or on a user
decision via the Ticket 029 channel). The recovery
net for jams a transcript can't reveal: an un-bridgeable permission prompt
(ADR 0005) or a wedged session. Repeated bounces in a short window stop and
escalate to the user instead of flapping. Per Ticket 020 / ADR 0015.
_Avoid_: restart, reboot, kill-and-retry.

**Phase-confirmation gate**:
A code-enforced floor (Ticket 019, [ADR 0019](adr/0019-maestro-phase-confirmation-gate.md))
that blocks a Maestro's **first** `spawn_team` until it has completed one
user decision round-trip — tracked by the durable `confirmed_with_user` flag,
set when a user reply clears `awaiting_decision`. It rides Ticket 029's
conversational decision channel (the maestro-facing replacement for the
**[[Interactive gate]]**, ADR 0018) fired at the planning→executing boundary.
**Content-dumb**: it proves the Maestro *asked*, not that the user *approved* —
obeying a "no" stays the Maestro's job. Per-Maestro opt-out via the
`phase_confirm` flag (default on, including the PA Maestro); a user-typed
`/team create` is exempt (the human is the confirmation).
_Avoid_: approval gate (Hive reads nothing), native gate (not a TUI gate),
plan-mode gate.

**Thinking skill**:
A Claude Code skill that pauses mid-Turn to involve a human — an
interview/Q&A, an `AskUserQuestion` selection, or a STOP/approval
checkpoint. Reachable only by a Maestro, whose gates bridge to the user on
Telegram; a Team Lead would stall on one, since its gate escalates to a
parent Entity that cannot answer. Contrast an *autonomous*
skill, which runs to completion without a human. Per-role exposure is set
by the skill-curation denylist (Ticket 012, ADR 0008).
_Note_: the blocking test is liveness (does it wait for a human?), not
side-effects or fan-out.

**Advisor**:
Claude Code's native `/advisor` tool — a stronger model (Opus) the
executor consults at decision points for a second opinion. Enabled
per-Entity by the role file's `**Advisor**:` field (`--advisor <model>`
at spawn); model-driven and Plan-billed. _Note_: from Ticket 013 this is
the **native** tool. The retired *custom advisor* (a Hive MCP server that
spawned a `claude -p` subprocess) is gone — do not conflate them.
_Avoid_: custom advisor, advisor MCP server, `claude -p` advisor.

**Worktree reconciliation**:
The startup pass that makes the worktree floor crash-safe (Ticket 025,
ADR 0016). After entities are restored, it re-adopts each Lead's own
worktree (path derived from the Lead's name, uncommitted edits intact) and
sweeps **orphan worktrees**. The sweep is scoped strictly to
`WORKTREES_DIR` — it can never touch the main checkout or the developer's
`.claude/worktrees/` sessions — and never deletes a worktree holding
uncommitted work.
_Avoid_: orphan cleanup, worktree GC.

**Orphan worktree**:
A directory under `WORKTREES_DIR` with no owning Entity — left by a crash
mid-spawn (worktree created before the Entity persisted) or a failed,
swallowed removal mid-kill. Disposed by [[Worktree reconciliation]]:
git-admin-stale → `prune`, clean → remove, dirty → quarantine (audit +
warn, kept for a human). Not the same as a Claude Code leaf-agent worktree
under `.claude/worktrees/`, which Hive does not sweep.
_Avoid_: stale worktree, dead worktree.

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

### Project management

**Sprint**:
A 2-week calendar window holding committed Tickets. One file per
sprint in `docs/sprints/YYYY-QN-SN.md`, peer files sorted
chronologically by filename. Frozen at sprint close.
_Note_: Sprints 0–31 in `docs/archive/PROJECT_PLAN.md` and
`docs/CHANGELOG.md` are the **legacy** meaning — single units of
shipped work, not 2-week windows. The current meaning starts from
`2026-Q2-S1`.

**Ticket**:
One unit of work. Lives in `docs/tickets/NNN-slug/` as a folder of
artifacts (`ticket.md`, `questions.md`, `research.md`, `design.md`,
`outline.md`, `plan.md`). A Sprint commits a set of Tickets.
_Avoid_: task (overloaded — `/task add` in Telegram is a different
concept), feature (a roadmap-level idea that may eventually become
one or more Tickets).

## Relationships

- A **Maestro** owns zero or more **Teams**
- One Maestro is the **PA Maestro** (the default route — every chat
  that doesn't name a Maestro goes to it; `HIVE_DEFAULT_MAESTRO`,
  currently `otter`). It is not bound to a project. Every other
  Maestro leads exactly one project; Maestros never share a project.
- Maestro↔Maestro communication coordinates shared resources only
  (e.g. Plan quota) — work never crosses Maestro orgs except via the
  user. (A norm, not code-enforced.)
- A **Team** is one **Team Lead** plus the leaf work it runs (its **Workflow runs**)
- Every **Entity** runs on exactly one **Harness** at a time, through that
  Harness's **Adapter**
- Any **Entity** may be assigned to any **Harness** regardless of its role —
  full capability parity (how *well* it runs still depends on the model)
- A **Harness** is either **Plan-billed** or **API-billed**, depending on how
  it authenticates

## Example dialogue

> **Dev:** "If a Team Lead is running on the Codex Harness, is it still an Entity Hive manages?"
> **Hezki:** "Yes. The Harness only executes its Turns — Hive still owns the Lead's lifecycle, its Team membership, and its goal. Swap the Harness and it's the same Entity."
> **Dev:** "So 'runtime' is just the Harness it's on right now?"
> **Hezki:** "Right. And the Adapter for that Harness is the code that actually drives it."

## Flagged ambiguities

- **"agent" vs "Entity"** — the README and older docs say "agent"; the code's base class is `Entity`. Resolved: **Entity** is canonical.
- **"runtime" vs "Harness"** — a **Harness** is the external tool; a **Runtime** is which Harness an Entity is assigned to. Not synonyms.
- **"subsidised"** — informal word for **Plan-billed**. Use Plan-billed.
- **"project"** — two senses. A **Project** (capital P) is a codebase a
  Maestro owns (Ticket 024 registry record). A lowercase "project" in
  `docs/` means the project-management altitude (Sprints/Tickets). Prefer
  the capital-P term when ownership is meant.
